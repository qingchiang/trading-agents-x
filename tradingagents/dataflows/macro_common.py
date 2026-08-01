"""Shared primitives for the macro vendors (fred / estat / boj / jp / cn).

These vendors deliberately share a return shape and rendering, so the cross-region
panel and the get_macro_indicators microscope tool treat every source uniformly.
This module holds the pieces that would otherwise be copy-pasted across them,
including the :class:`SeriesCache` they memoize fetches in. It imports the config
accessor and the clock (for the cache's cross-run disk layer) but no vendor, so
the vendors can still import from it freely.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import NamedTuple

from .config import get_config
from .measurement import classify_vendor_unit
from .utils import get_current_date

logger = logging.getLogger(__name__)

# Rows cap for a rendered observation table: recent values matter most for a
# decision, and daily series (yields, VIX) over a long window would flood context.
MAX_ROWS = 40

# Used only to build a human-scannable *prefix* for a cache filename; injectivity
# comes from a hash of the full key appended after it (see _disk_file), so squashing
# unsafe chars here can't alias distinct keys onto one file.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^0-9A-Za-z._-]")

# Re-fetch a persisted series once its file is older than this. A past date's
# window is look-ahead-immutable, but its *values* aren't: agencies revise already
# published observations (GDP estimates, payroll benchmarks, CPI seasonal factors).
# A bounded age lets those revisions land eventually instead of a write-once entry
# being trusted forever, while still saving the vast majority of a backtest's
# cross-run re-fetches.
_DISK_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
_RECENT_DISK_TTL_SECONDS = 60 * 60  # 60 minutes

# Cap the persisted files per namespace — the disk analogue of ``max_entries`` — so
# a multi-date backtest (one file per series/date/window) can't grow the cache dir
# without bound. Eviction drops the oldest by mtime; a re-request just re-fetches.
# Pruning stats the whole dir, so it runs once per process (first settled write)
# plus every _PRUNE_EVERY writes, not on every write.
_DISK_MAX_FILES = 4096
_PRUNE_EVERY = 512

# A ``.tmp`` older than this was orphaned by a crash between mkstemp and the rename
# (a live write finishes in milliseconds), so a prune can safely reclaim it. Kept
# generous so an in-flight write on a slow filesystem is never deleted mid-flight.
_TMP_ORPHAN_SECONDS = 3600  # 1 hour

# Only treat a date as settled once it is at least this many days behind today.
# Recent dates use the separate one-hour cache because a source can still be
# publishing; after the grace window, the 30-day settled path becomes eligible.
_SETTLE_GRACE_DAYS = 3


def _restore_series(value):
    """Undo JSON's tuple→list coercion so a disk hit matches a fresh fetch.

    Vendors build ``points`` as a list of ``(date, value)`` tuples; JSON round-trips
    them to lists, so without this a cross-run cache hit would return a different
    shape than a live fetch and make ``fetch_series`` cache-provenance-dependent.
    """
    if isinstance(value, dict) and isinstance(value.get("points"), list):
        value["points"] = [tuple(p) if isinstance(p, list) else p for p in value["points"]]
    return value


class _CacheEntry(NamedTuple):
    value: object
    stored_at: float
    settled: bool


class SeriesCache:
    """A bounded process-level LRU for fetched macro series, optionally disk-backed.

    Key is ``(series_id, curr_date, look_back_days)``; value is the fetched series
    dict. Only *successful* fetches are stored — callers skip :meth:`put` on a
    miss — so :meth:`get` returning ``None`` means "not cached", never "cached
    empty" (a transient outage must be retryable). Bounded so a long-running
    process (e.g. a multi-date backtest, which creates a distinct key per date)
    cannot grow it without limit; the default holds ~tens of dates' worth of the
    panel's dozen-odd series, and an evicted entry is simply re-fetched.

    When built with a ``namespace`` (fred/estat/boj/jp/cn), entries are also
    persisted under ``<data_cache_dir>/macro/<namespace>/`` so a later run — a
    multi-date backtest re-reading the same past dates is the motivating case —
    reads them from disk instead of re-hitting the rate-limited API. "Settled"
    means the key's ``curr_date`` is at least ``_SETTLE_GRACE_DAYS`` behind today
    (not merely before it): a source lagging the host clock may still be publishing
    a very recent date. Those moving windows use a separate 60-minute disk entry;
    once settled, only the long-lived path is considered. A cached value is
    inherently as-of ``curr_date`` (its points never exceed it), so serving it in a
    later run cannot leak future data. A past window is fixed but its *values* can
    still be revised upstream (GDP/payroll/CPI revisions), so a persisted entry is
    honored only until it is ``_DISK_TTL_SECONDS`` old, then re-fetched (and the
    stale file deleted). The files are also count-bounded per namespace
    (``_DISK_MAX_FILES``, the disk analogue of ``max_entries``): a prune evicts the
    oldest by mtime so neither a long in-process backtest nor many short CLI runs
    can grow the dir without bound. Because the common case is many short-lived
    processes (an instance-local write counter would never reach the throttle),
    the prune runs once on each process's first settled write and then every
    ``_PRUNE_EVERY`` writes. JSON drops
    tuple-ness, so a disk hit is normalized back to the fresh-fetch shape (see
    :func:`_restore_series`). Without a namespace the cache is memory-only
    (unchanged behavior). The disk layer is a best-effort optimization: any I/O or
    serialization error degrades to a normal fetch.
    """

    def __init__(
        self,
        max_entries: int = 512,
        *,
        namespace: str | None = None,
        recent_ttl_seconds: int | None = _RECENT_DISK_TTL_SECONDS,
    ):
        # ``max_entries`` stays the first positional arg (its historical contract);
        # ``namespace`` is keyword-only so ``SeriesCache(256)`` can't be misread as a
        # namespace and silently disable the bound.
        self._data: OrderedDict = OrderedDict()
        self._max = max_entries
        self._namespace = namespace
        self._recent_ttl_seconds = recent_ttl_seconds
        # Cross-run bound: prune once on this process's first disk write (the
        # counter resets per process, so a short run would never hit the throttle).
        self._pruned = False
        self._writes_since_prune = 0

    def get(self, key):
        if key in self._data:
            entry = self._data[key]
            settled = self._is_settled(key)
            ttl = _DISK_TTL_SECONDS if settled else self._recent_ttl_seconds
            expired = (
                self._namespace is not None
                and ttl is not None
                and time.time() - entry.stored_at > ttl
            )
            if entry.settled == settled and not expired:
                self._data.move_to_end(key)  # mark most-recently-used
                return entry.value
            del self._data[key]
        entry = self._disk_get(key)
        if entry is not None:
            self._remember(
                key,
                entry.value,
                stored_at=entry.stored_at,
                settled=entry.settled,
            )
            return entry.value
        return None

    def put(self, key, value) -> None:
        self._remember(key, value)
        self._disk_put(key, value)

    def clear(self) -> None:
        # In-memory only: the persisted disk layer is deliberately NOT purged here
        # (it is the whole point of a cross-run cache, and several vendor tests call
        # clear() without redirecting data_cache_dir — wiping it would delete the
        # user's real macro cache). Stale disk entries age out via _DISK_TTL_SECONDS.
        self._data.clear()

    # -- internals ---------------------------------------------------------

    def _remember(
        self,
        key,
        value,
        *,
        stored_at: float | None = None,
        settled: bool | None = None,
    ) -> None:
        """Insert into the in-memory LRU, evicting the oldest past the bound."""
        self._data[key] = _CacheEntry(
            value,
            time.time() if stored_at is None else stored_at,
            self._is_settled(key) if settled is None else settled,
        )
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least-recently-used

    def _disk_dir(self) -> str | None:
        if self._namespace is None:
            return None
        return os.path.join(get_config()["data_cache_dir"], "macro", self._namespace)

    def _disk_file(self, key) -> str | None:
        disk_dir = self._disk_dir()
        if disk_dir is None:
            return None
        # Readable prefix for eyeballing the dir, plus a hash of the full key so
        # distinct keys that sanitize to the same prefix (e.g. FRED ids "a/b" vs
        # "a?b", both -> "a_b") never collide onto one file and cross-serve data.
        prefix = "__".join(_UNSAFE_FILENAME_CHARS.sub("_", str(part)) for part in key)
        digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:16]
        return os.path.join(disk_dir, f"{prefix}__{digest}.json")

    def _recent_disk_file(self, key) -> str | None:
        """Return the short-lived cache path, kept distinct from settled data."""
        path = self._disk_file(key)
        if path is None:
            return None
        return path.removesuffix(".json") + ".recent.json"

    def _is_settled(self, key) -> bool:
        """True when the key's ``curr_date`` (key[1]) is settled enough to persist.

        Persist only dates at least ``_SETTLE_GRACE_DAYS`` behind today, not merely
        "before today": a source can lag the host clock (timezone skew +
        next-business-day publication), so a very recent date may still be missing
        its latest observation — persisting that incomplete snapshot would pin it for
        the whole TTL. Both sides are parsed to dates rather than string-compared, so
        a non-zero-padded date (e.g. ``2026-7-5``) is classified correctly.
        """
        if not (isinstance(key, tuple) and len(key) >= 2 and isinstance(key[1], str)):
            return False
        try:
            key_date = datetime.strptime(key[1], "%Y-%m-%d").date()
            today = datetime.strptime(get_current_date(), "%Y-%m-%d").date()
        except ValueError:
            return False
        return key_date <= today - timedelta(days=_SETTLE_GRACE_DAYS)

    def _disk_get(self, key):
        settled = self._is_settled(key)
        path = self._disk_file(key) if settled else self._recent_disk_file(key)
        if path is None:
            return None
        ttl = _DISK_TTL_SECONDS if settled else self._recent_ttl_seconds
        if ttl is None:
            return None
        try:
            stored_at = os.path.getmtime(path)
            if time.time() - stored_at > ttl:
                # Stale: re-fetch so upstream revisions can land, and delete the
                # unusable file rather than leave it to accumulate.
                self._remove(path)
                return None
            with open(path, encoding="utf-8") as fh:
                return _CacheEntry(_restore_series(json.load(fh)), stored_at, settled)
        except (OSError, ValueError):
            return None

    def _disk_put(self, key, value) -> None:
        settled = self._is_settled(key)
        if not settled and self._recent_ttl_seconds is None:
            return
        path = self._disk_file(key) if settled else self._recent_disk_file(key)
        if path is None:
            return
        disk_dir = os.path.dirname(path)
        try:
            os.makedirs(disk_dir, exist_ok=True)
            # Unique temp per writer (mkstemp) so two threads writing the same series
            # can't clobber each other's partial file; the rename publishes the
            # complete JSON atomically, so a concurrent reader never sees a torn one.
            fd, tmp = tempfile.mkstemp(dir=disk_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(value, fh)
                os.replace(tmp, path)
            except (OSError, TypeError, ValueError):
                os.unlink(tmp)  # don't leave the partial temp behind
                raise
        except (OSError, TypeError, ValueError):
            logger.debug("macro disk cache write skipped for %s", self._namespace)
            return
        if settled:
            # A key that crossed the grace boundary must never keep serving the
            # short-lived snapshot under long-term cache semantics.
            recent_path = self._recent_disk_file(key)
            if recent_path is not None:
                self._remove(recent_path)
        self._writes_since_prune += 1
        # Prune on the first disk write of the process (bounds accumulation
        # across short runs) and every _PRUNE_EVERY writes after (bounds a long one).
        if not self._pruned or self._writes_since_prune >= _PRUNE_EVERY:
            self._prune(disk_dir)

    def _remove(self, path: str) -> None:
        """Best-effort delete of a cache file; a failure is never fatal."""
        with contextlib.suppress(OSError):
            os.remove(path)

    def _prune(self, disk_dir: str) -> None:
        """Cap the namespace's file count and reclaim orphaned temp files.

        Evicts the oldest ``.json`` past ``_DISK_MAX_FILES`` (by mtime) and deletes
        any ``.tmp`` old enough to be a crash orphan. Best-effort and self-guarding
        (an I/O error just leaves the dir as-is), so it can be called from the write
        path without risking the fetch it follows.
        """
        self._pruned = True
        self._writes_since_prune = 0
        try:
            names = os.listdir(disk_dir)
        except OSError:
            return

        json_files = [os.path.join(disk_dir, n) for n in names if n.endswith(".json")]
        if len(json_files) > _DISK_MAX_FILES:
            with contextlib.suppress(OSError):
                json_files.sort(key=os.path.getmtime)  # oldest first
                for path in json_files[: len(json_files) - _DISK_MAX_FILES]:
                    self._remove(path)

        # Reclaim temp files left behind by a crash between mkstemp and the rename;
        # guarded per file so a concurrent write finishing here can't abort the sweep.
        cutoff = time.time() - _TMP_ORPHAN_SECONDS
        for name in names:
            if not name.endswith(".tmp"):
                continue
            path = os.path.join(disk_dir, name)
            with contextlib.suppress(OSError):
                if os.path.getmtime(path) < cutoff:
                    self._remove(path)


class PointsSummary(NamedTuple):
    """Latest reading and window change for a series' observation list.

    ``delta``/``pct`` are ``None`` when the window has fewer than two points (so a
    single point is not rendered as a fabricated "+0.00" change) or a value isn't
    numeric; ``pct`` is also ``None`` when the base is zero. ``pct`` is the
    meaningful figure for index series (CPI), where an absolute delta is opaque.
    """

    last_val: str
    last_date: str
    first_val: str
    first_date: str
    delta: float | None
    pct: float | None


class YearOverYearSummary(NamedTuple):
    """Latest reading and its exact same-period prior-year comparison."""

    last_val: str
    last_date: str
    prior_val: str
    prior_date: str
    pct: float


def exact_year_over_year(points: list) -> YearOverYearSummary | None:
    """Calculate YoY only when the exact prior-year calendar point exists.

    February 29 compares with February 28 in the prior non-leap year, the normal
    calendar-year counterpart. No nearest-neighbour lookup or interpolation is
    performed, so sparse/revised series cannot silently produce an approximation.
    """
    if not points:
        return None
    values = {str(point_date): value for point_date, value in points}
    last_date, last_val = points[-1]
    try:
        parsed = datetime.strptime(str(last_date), "%Y-%m-%d").date()
        try:
            prior = parsed.replace(year=parsed.year - 1)
        except ValueError:  # February 29 in a leap year
            prior = parsed.replace(year=parsed.year - 1, day=28)
        prior_date = prior.isoformat()
        prior_val = values[prior_date]
        current = float(last_val)
        base = float(prior_val)
    except (KeyError, TypeError, ValueError):
        return None
    if base == 0:
        return None
    return YearOverYearSummary(
        str(last_val), str(last_date), str(prior_val), prior_date, (current - base) / base * 100
    )


def summarize_points(points: list) -> PointsSummary | None:
    """Summarize an ascending ``[(date, value), ...]`` list, or None if empty.

    Shared by the macro vendors' report rendering and the cross-region panel so
    the latest/change computation stays in one place.
    """
    if not points:
        return None
    first_date, first_val = points[0]
    last_date, last_val = points[-1]
    delta = pct = None
    if len(points) >= 2:
        try:
            delta = float(last_val) - float(first_val)
            base = float(first_val)
            pct = (delta / base * 100) if base != 0 else None
        except (TypeError, ValueError):
            delta = pct = None
    return PointsSummary(last_val, last_date, first_val, first_date, delta, pct)


def render_macro_report(source_label: str, data: dict, curr_date: str) -> str:
    """Render a fetched macro series (the :func:`fetch_series` dict) as markdown.

    ``source_label`` heads the report (e.g. "FRED", "e-Stat", "BOJ"); every macro
    vendor renders through this so the microscope tool's output is uniform across
    sources. Shows title/units/frequency, the latest value, the change over the
    window, and a recent observation table (capped at ``MAX_ROWS``).
    """
    series_id = data["series_id"]
    seasonal = data["seasonal"]
    points = data["points"]
    header = (
        f"## {source_label}: {data['title']} ({series_id})\n"
        f"- Units: {data['units']}\n"
        f"- Frequency: {data['frequency']}"
        f"{f' ({seasonal})' if seasonal else ''}\n"
        f"- Window: {data['start_date']} to {curr_date}\n"
    )

    if not points:
        return header + (
            f"\nNo observations for {series_id} in this window. The series may "
            f"report less frequently than the window length; widen look_back_days."
        )

    s = summarize_points(points)
    if s.delta is None:
        summary = f"\n**Latest:** {s.last_val} ({s.last_date})\n"
    else:
        pct = f" ({s.pct:+.2f}%)" if s.pct is not None else ""
        summary = (
            f"\n**Latest:** {s.last_val} ({s.last_date}) | "
            f"**Change over window:** {s.delta:+.2f}{pct} "
            f"from {s.first_val} ({s.first_date})\n"
        )

    shown = points
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]
        note = f"\n_(showing the most recent {MAX_ROWS} of {len(points)} observations)_\n"

    measurement, unit = classify_vendor_unit(data.get("units"))
    table = (
        "\n| Date | Value | Measurement | Unit |\n| --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {d} | {v} | {measurement} | {unit or '—'} |" for d, v in shown
        )
        + "\n"
    )
    return header + summary + note + table
