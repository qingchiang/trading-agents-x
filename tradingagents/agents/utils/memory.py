"""Append-only markdown decision log for TradingAgentsX."""

import re
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.dataflows.symbol_utils import crypto_base, market_timezone


def _coerce_non_negative_int(
    value,
    *,
    name: str,
    default: int | None,
) -> int | None:
    """Return a non-negative integer config value, preserving ``None``."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer, not {value!r}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        parsed = int(value)
    else:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    if parsed < 0:
        raise ValueError(f"{name} must be >= 0, got {parsed}")
    return parsed


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    _DEFAULT_MAX_ENTRIES = 1000
    _DEFAULT_CROSS_TICKER_LIMIT = 3
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Global cap on resolved entries. Pending entries are never pruned.
        self._max_entries = _coerce_non_negative_int(
            cfg.get("memory_log_max_entries", self._DEFAULT_MAX_ENTRIES),
            name="memory_log_max_entries",
            default=None,
        )
        self._cross_ticker_limit = _coerce_non_negative_int(
            cfg.get(
                "memory_cross_ticker_limit",
                self._DEFAULT_CROSS_TICKER_LIMIT,
            ),
            name="memory_cross_ticker_limit",
            default=self._DEFAULT_CROSS_TICKER_LIMIT,
        )

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
        *,
        asset_type: str | None = None,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        raw = ""
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
            # A newly appended pending entry does not affect the resolved cap,
            # but applying rotation here bounds legacy oversized logs even when
            # no outcome happens to be resolved during this run.
            blocks = raw.split(self._SEPARATOR)
            rotated = self._apply_rotation(blocks)
            if rotated != blocks:
                self._write_blocks(rotated)

        normalized_asset_type = self._asset_type(ticker, asset_type)
        market = self._market(ticker, normalized_asset_type)
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"
        metadata_fields = [f"asset_type={normalized_asset_type}"]
        if market is not None:
            metadata_fields.append(f"market={market}")
        metadata = f"META: {' | '.join(metadata_fields)}"
        entry = (
            f"{tag}\n\n{metadata}\n\nDECISION:\n"
            f"{final_trade_decision}{self._SEPARATOR}"
        )
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> list[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> list[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(
        self,
        ticker: str,
        n_same: int = 5,
        n_cross: int | None = None,
        *,
        asset_type: str | None = None,
    ) -> str:
        """Return formatted past context string for agent prompt injection."""
        entries = [e for e in self.load_entries() if not e.get("pending")]
        if not entries:
            return ""

        cross_limit = (
            self._cross_ticker_limit
            if n_cross is None
            else _coerce_non_negative_int(
                n_cross,
                name="n_cross",
                default=self._cross_ticker_limit,
            )
        )
        target_asset_type = self._asset_type(ticker, asset_type)
        target_market = self._market(ticker, target_asset_type)
        same, cross = [], []
        for e in reversed(entries):
            cross_complete = cross_limit == 0 or len(cross) >= cross_limit
            if len(same) >= n_same and cross_complete:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif (
                e["ticker"] != ticker
                and len(cross) < cross_limit
                and target_market is not None
                and e["market"] is not None
                and e["asset_type"] == target_asset_type
                and e["market"] == target_market
            ):
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures, and appends a REFLECTION section.  Uses
        a temp-file + os.replace() so a crash mid-write never corrupts the log.
        """
        if not self._log_path or not self._log_path.exists():
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        pending_prefix = f"[{trade_date} | {ticker} |"
        raw_pct = f"{raw_return:+.1%}"
        alpha_pct = f"{alpha_return:+.1%}"

        updated = False
        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            if (
                not updated
                and tag_line.startswith(pending_prefix)
                and tag_line.endswith("| pending]")
            ):
                # Parse rating from the existing pending tag
                fields = [f.strip() for f in tag_line[1:-1].split("|")]
                rating = fields[2]
                new_tag = (
                    f"[{trade_date} | {ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {holding_days}d]"
                )
                rest = "\n".join(lines[1:])
                new_blocks.append(
                    f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                )
                updated = True
            else:
                new_blocks.append(block)

        if not updated:
            return

        new_blocks = self._apply_rotation(new_blocks)
        self._write_blocks(new_blocks)

    def batch_update_with_outcomes(self, updates: list[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            matched = False
            for (trade_date, ticker), upd in list(update_map.items()):
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    new_tag = (
                        f"[{trade_date} | {ticker} | {rating}"
                        f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    del update_map[(trade_date, ticker)]
                    matched = True
                    break

            if not matched:
                new_blocks.append(block)

        new_blocks = self._apply_rotation(new_blocks)
        self._write_blocks(new_blocks)

    # --- Helpers ---

    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: list[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _write_blocks(self, blocks: list[str]) -> None:
        """Atomically rewrite the log from separator-delimited blocks."""
        new_text = self._SEPARATOR.join(blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    @staticmethod
    def _asset_type(ticker: str, asset_type: str | None = None) -> str:
        """Normalize explicit asset type or infer it for legacy entries."""
        if asset_type is not None:
            value = getattr(asset_type, "value", asset_type)
            return str(value).strip().lower()
        return "crypto" if crypto_base(ticker) else "stock"

    @staticmethod
    def _market(ticker: str, asset_type: str) -> str | None:
        """Return a stable regional market bucket for memory filtering."""
        if asset_type == "crypto":
            return "CRYPTO"
        try:
            return str(market_timezone(ticker))
        except ValueError:
            # Keep legacy symbols rejected by the shared market utility out of
            # cross-ticker matching rather than inventing a market locally.
            return None

    def _parse_entry(self, raw: str) -> dict | None:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
        }
        body = "\n".join(lines[1:]).strip()
        metadata = {}
        metadata_line = next(
            (line.strip() for line in lines[1:] if line.strip()),
            "",
        )
        if metadata_line.startswith("META:"):
            for field in metadata_line.removeprefix("META:").split("|"):
                key, separator, value = field.strip().partition("=")
                if separator and key in {"asset_type", "market"}:
                    metadata[key] = value.strip()
        asset_type = metadata.get("asset_type") or self._asset_type(entry["ticker"])
        market = metadata.get("market") or self._market(entry["ticker"], asset_type)
        entry["asset_type"] = asset_type
        entry["market"] = market
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"
