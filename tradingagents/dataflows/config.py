from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from copy import deepcopy

from tradingagents import default_config

_run_config: ContextVar[dict | None] = ContextVar(
    "tradingagents_run_config",
    default=None,
)
# Temporary compatibility state for the characterization suite. Production
# graph/application execution always binds ``_run_config``. This is removed with
# the old public graph API in the final cutover.
_config: dict = deepcopy(default_config.DEFAULT_CONFIG)


def bind_config(config: dict, *, merge: bool = True) -> Token:
    """Bind a configuration to the current async/thread context.

    Context variables propagate into LangGraph tasks but remain isolated across
    concurrent runs. ``merge=True`` preserves the old one-level nested override
    semantics for focused dataflow tests and adapters.
    """
    current = _run_config.get()
    resolved = deepcopy(
        current if current is not None else default_config.DEFAULT_CONFIG
    )
    incoming = deepcopy(config)
    if merge:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(resolved.get(key), dict):
                resolved[key].update(value)
            else:
                resolved[key] = value
    else:
        resolved = incoming
    return _run_config.set(resolved)


def set_config(config: dict) -> None:
    """Compatibility shim for pre-cutover tests and direct dataflow callers."""
    global _config
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(_config.get(key), dict):
            _config[key].update(value)
        else:
            _config[key] = value


def reset_config(token: Token) -> None:
    _run_config.reset(token)


@contextmanager
def use_config(config: dict, *, merge: bool = False) -> Iterator[dict]:
    token = bind_config(config, merge=merge)
    try:
        yield get_config()
    finally:
        reset_config(token)


def get_config() -> dict:
    """Return a defensive copy of the current run-scoped configuration."""
    current = _run_config.get()
    return deepcopy(current if current is not None else _config)
