"""Resolve a Tokyo ticker to its EDINET code, with a self-healing local cache.

Large-shareholding (大量保有) and tender-offer filings name their *subject*
company in ``subjectEdinetCode`` (an EDINET code, ``E#####``), not by securities
code — so to find filings *about* a ticker we must map ``9984.T`` → its EDINET
code. EDINET's ``documents.json`` is date-keyed only and has no code-table
endpoint, and the official code table (``EdinetcodeDlInfo.csv``) has no clean
static download. So we ship a committed seed snapshot and self-heal at runtime:

  * **Seed** — ``data/edinet_code_map.json`` (built by
    ``scripts/build_edinet_code_map.py``), a possibly-stale ``4-digit base
    secCode → EDINET code`` table for listed issuers. Read-only, in-package.
  * **Learned cache** — ``edinet_code_map_learned.json`` under the config
    ``data_cache_dir``. Every filing carries the filer's own ``secCode`` +
    ``edinetCode``, so as the news/holdings windows iterate ``documents.json``
    they :func:`learn` any issuer the seed lacks (a new IPO, say) and persist it.
    Once seen in any filing, that issuer is resolvable forever — the seed never
    needs refreshing.

The merged view is ``seed`` overlaid with ``learned`` (learned wins, being
fresher). Residual boundary: a brand-new ticker analysed before we have seen any
of its filings is unresolvable and degrades gracefully (no large-holding block).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from importlib import resources

from ..config import get_config
from ..symbol_utils import tokyo_securities_base
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)

_SEED_PACKAGE = "tradingagents.dataflows.data"
_SEED_FILENAME = "edinet_code_map.json"
_CACHE_FILENAME = "edinet_code_map_learned.json"

# Guards the in-memory maps and the cache-file write so concurrent analysts can't
# corrupt the JSON or race the lazy load.
_lock = threading.Lock()

# Lazily built under _lock. ``_seed`` is the committed snapshot (authoritative);
# ``_learned`` is only the runtime-discovered entries (what we persist). They are
# kept disjoint (a base already in the seed is never learned), so a lookup just
# consults the seed first — no third merged copy to keep consistent.
_seed: dict[str, str] | None = None
_learned: dict[str, str] | None = None


def _load_seed() -> dict[str, str]:
    """Load the committed seed mapping (``base secCode → EDINET code``)."""
    try:
        text = (
            resources.files(_SEED_PACKAGE).joinpath(_SEED_FILENAME).read_text("utf-8")
        )
        return dict(json.loads(text).get("codes", {}))
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("EDINET code-map seed unreadable (%s); starting empty.", exc)
        return {}


def _cache_path() -> str:
    return os.path.join(get_config()["data_cache_dir"], _CACHE_FILENAME)


def _load_learned() -> dict[str, str]:
    """Load the runtime-learned cache, tolerating an absent or poisoned file."""
    path = _cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, OSError) as exc:
        logger.warning("EDINET learned cache unreadable (%s); ignoring it.", exc)
        return {}


def _ensure_loaded() -> None:
    """Populate ``_seed`` / ``_learned`` once (caller holds ``_lock``)."""
    global _seed, _learned
    if _seed is not None:
        return
    _seed = _load_seed()
    _learned = _load_learned()


def _persist(learned: dict[str, str]) -> None:
    """Write the learned cache atomically (temp file + replace) under ``_lock``."""
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(learned, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


def _normalize(sec_code: str | None, edinet_code: str | None) -> tuple[str, str] | None:
    """Validate a raw ``(secCode, edinetCode)`` pair into ``(base, edinet)`` or None.

    A listing base is always 4 chars (4-digit, or 3-digit + a letter like 130A);
    anything else is a non-listed/foreign filer's code we must not store.
    """
    base = tokyo_securities_base(sec_code)
    edinet = (edinet_code or "").strip()
    if len(base) != 4 or not edinet:
        return None
    return base, edinet


def _apply_locked(base: str, edinet: str) -> bool:
    """Add a learned ``base → edinet`` if new; return whether anything changed.

    Caller holds ``_lock`` and has run ``_ensure_loaded``. Bases already in the
    authoritative seed are left untouched (seed wins), so seed and learned stay
    disjoint and this only ever fills gaps the seed does not cover.
    """
    if base in _seed or _learned.get(base) == edinet:
        return False
    _learned[base] = edinet
    return True


def resolve_edinet_code(ticker: str) -> str | None:
    """Return the EDINET code for a Tokyo ``ticker`` (e.g. ``9984.T``), or None.

    Resolves via the 4-digit base securities code, the join key shared with
    :func:`tradingagents.dataflows.symbol_utils.tokyo_securities_base`. The seed is
    authoritative, so it is consulted first; None means the issuer is in neither the
    seed nor anything learned so far.
    """
    base = to_jquants_code(ticker)
    with _lock:
        _ensure_loaded()
        return _seed.get(base) or _learned.get(base)


def learn(sec_code: str | None, edinet_code: str | None) -> None:
    """Record a single ``secCode → EDINET code`` pair seen in a filing, if it is new.

    Thin wrapper over :func:`learn_many`; prefer the latter for a bulk scan so the
    lock is taken and the cache written once rather than once per pair.
    """
    learn_many([(sec_code, edinet_code)])


def learn_many(pairs) -> None:
    """Apply many ``(secCode, edinetCode)`` pairs under one lock + one cache write.

    The holdings scan reads the whole market's daily filings, so applying pairs one
    at a time would take the lock and rewrite the cache once per new issuer; this
    batches the entire window into a single persist when anything changed. No-ops
    (without touching disk) for pairs that are malformed, already learned, or
    already covered by the authoritative seed.
    """
    with _lock:
        _ensure_loaded()
        changed = False
        for sec_code, edinet_code in pairs:
            pair = _normalize(sec_code, edinet_code)
            if pair is not None and _apply_locked(*pair):
                changed = True
        if changed:
            try:
                _persist(dict(_learned))
            except OSError as exc:
                logger.warning("Could not persist EDINET learned codes: %s", exc)


def _reset_for_tests() -> None:
    """Drop the in-memory maps so the next call reloads from disk (tests only)."""
    global _seed, _learned
    with _lock:
        _seed = None
        _learned = None
