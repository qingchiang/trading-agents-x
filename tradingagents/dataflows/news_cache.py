"""Request-triggered, bounded source-material cache, independent of the Run DB."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import get_config
from .news_selection import NewsCandidate, render_candidate, split_candidates
from .symbol_utils import market_timezone


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _connect(config):
    path = Path(config["data_cache_dir"]) / "news" / "sources.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
          scope TEXT, item TEXT, version TEXT, published TEXT, retrieved REAL,
          payload TEXT, PRIMARY KEY(scope,item,version));
        CREATE INDEX IF NOT EXISTS articles_scope ON articles(scope,published);
        CREATE TABLE IF NOT EXISTS refreshes (
          scope TEXT, signature TEXT, fetched REAL, header TEXT,
          PRIMARY KEY(scope,signature));
    """)
    return connection


def _eligible(rows, start, end):
    # Keep the newest version visible to this request; never backdate revisions.
    latest = {}
    for payload, in rows:
        row = NewsCandidate(**json.loads(payload))
        if row.day is None or not start <= row.day.isoformat() <= end:
            continue
        key = row.record_id or row.link or row.title
        if key not in latest or row.retrieved_at > latest[key].retrieved_at:
            latest[key] = row
    return list(latest.values())


def fetch_news_feed(source, scope_key, start, end, fetch, *, budget=100, now=None, config=None):
    """Reuse exact refreshes and merge previously observed, eligible candidates.

    Failures never certify a successful refresh. Cache errors degrade to the
    normal fetch, including when persistence fails after that fetch completed.
    """
    config = get_config() if config is None else config
    current = now() if now else datetime.now(UTC)
    fetched = None
    attempted_error = None
    connection = None
    if not config.get("news_cache_enabled", True) or not config.get("data_cache_dir"):
        return fetch()
    # Only non-secret selection/routing settings participate; credentials are
    # neither persisted nor logged. A disabled source is never invoked here.
    settings = {k: config.get(k) for k in (
        "data_vendors", "data_vendors_by_market", "tool_vendors", "global_news_queries",
        "global_news_query_limit", "global_news_candidate_limit", "news_selection_version",
    )}
    scope = _hash([source, scope_key, settings])
    signature = _hash([start, end, budget, settings])
    interval = max(0, int(config.get("news_cache_refresh_seconds", 900)))
    retention = max(1, int(config.get("news_cache_retention_days", 90)))
    oldest = (current - timedelta(days=retention)).date().isoformat()
    try:
        connection = _connect(config)
        receipt = connection.execute(
            "SELECT fetched,header FROM refreshes WHERE scope=? AND signature=?",
            (scope, signature),
        ).fetchone()
        failure = None
        fresh = []
        retrieved = current
        if receipt and 0 <= current.timestamp() - receipt[0] < interval:
            header = receipt[1]
        else:
            try:
                fetched = fetch()
                retrieved = current if now else datetime.now(UTC)
                if fetched.startswith(("Error", "<")):
                    failure = "source returned unavailable"
                header, fresh = split_candidates(fetched, source)
                normalized = []
                for row in fresh:
                    try:
                        clean_date = re.sub(r"\s+(?:JST|CST)$", "", row.published)
                        if len(clean_date) <= 10:
                            raise ValueError("date-only publication")
                        stamp = datetime.fromisoformat(clean_date.replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=market_timezone(scope_key))
                        row = replace(row, published=stamp.isoformat(),
                                      market_day=stamp.astimezone(market_timezone(scope_key)).date().isoformat())
                    except (ValueError, TypeError, AttributeError):
                        pass
                    normalized.append(row)
                fresh = normalized
            except Exception as exc:
                failure = type(exc).__name__
                header = f"## {source} news for {scope_key}"
                fetch_error = exc
                attempted_error = exc
            if failure is None:
                with connection:
                    for row in fresh:
                        if row.day is None:
                            continue
                        item = row.record_id or row.link or row.title
                        version = _hash([row.title, row.content, row.published])
                        existing = connection.execute(
                            "SELECT version,payload FROM articles WHERE scope=? AND item=? ORDER BY retrieved DESC,rowid DESC LIMIT 1",
                            (scope, item),
                        ).fetchone()
                        if existing:
                            previous = NewsCandidate(**json.loads(existing[1]))
                            if version == _hash([previous.title, previous.content, previous.published]):
                                continue
                            # A -> B -> A is a newly observed revision, not a
                            # cache hit on A's original historical occurrence.
                            version = _hash([version, existing[0]])
                        revision = existing is not None
                        row = replace(row, retrieved_at=retrieved.isoformat(), revision=revision,
                                      market_day=retrieved.astimezone(market_timezone(scope_key)).date().isoformat() if revision else row.market_day)
                        connection.execute(
                            "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?)",
                            (scope, item, version, row.day.isoformat(), retrieved.timestamp(), json.dumps(asdict(row))),
                        )
                    connection.execute("DELETE FROM articles WHERE published < ?", (oldest,))
                    connection.execute(
                        "DELETE FROM articles WHERE rowid IN (SELECT rowid FROM articles WHERE scope=? ORDER BY retrieved DESC LIMIT -1 OFFSET ?)",
                        (scope, max(1, int(config.get("news_cache_scope_limit", 2000)))),
                    )
                    connection.execute(
                        "DELETE FROM articles WHERE rowid IN (SELECT rowid FROM articles ORDER BY retrieved DESC LIMIT -1 OFFSET ?)",
                        (max(1, int(config.get("news_cache_total_limit", 50000))),),
                    )
                    connection.execute("DELETE FROM refreshes WHERE fetched < ?", (current.timestamp() - interval,))
                    if all(row.day is not None for row in fresh):
                        connection.execute("INSERT OR REPLACE INTO refreshes VALUES (?,?,?,?)", (scope, signature, current.timestamp(), header))
                    else:
                        # Undated live items are not persisted as dated history;
                        # do not certify a hot refresh that would lose them.
                        connection.execute("DELETE FROM refreshes WHERE scope=? AND signature=?", (scope, signature))
        rows = _eligible(connection.execute(
            "SELECT payload FROM articles WHERE scope=? AND published>=? ORDER BY retrieved", (scope, oldest)
        ).fetchall(), start, end)
        if failure and not rows:
            if fetched is not None:
                return fetched
            raise fetch_error
        # Refresh outcome belongs to this read, never to the saved article
        # version or its semantic identity.
        rows = [replace(row, refresh_failure=failure) for row in rows]
        rows.extend(replace(r, retrieved_at=retrieved.isoformat()) for r in fresh if r.day is None)
        if not rows:
            return fetched if fetched is not None else header
        if not header.startswith("## "):
            header = f"## {source} news for {scope_key}"
        fresh_keys = {r.record_id or r.link or r.title for r in fresh}
        added = sum((r.record_id or r.link or r.title) not in fresh_keys for r in rows)
        status = "refresh failed: " + failure if failure else ("refresh reused" if not fresh and receipt else "refreshed")
        note = (f"Source cache: {status}; cached_candidates={len(rows)}; cache_added={added}. "
                "Accumulated observed material only; interval completeness is unknown.")
        return header + "\n\n" + note + "\n\n" + "\n\n".join(render_candidate(r) for r in rows)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        if attempted_error is not None:
            raise attempted_error from None
        return fetched if fetched is not None else fetch()
    finally:
        if connection is not None:
            connection.close()
