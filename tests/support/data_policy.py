"""Build explicit adapter request contexts from reusable test policy arrangements.

Production calls must receive request_context() explicitly. These arrangements
never patch adapter configuration or inject policy into production execution.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from tradingagents.configuration.defaults import build_default_config
from tradingagents.data.context import DataRequestContext

_policy: ContextVar[DataRequestContext | None] = ContextVar("test_data_policy", default=None)


def request_context():
    return _policy.get() or DataRequestContext(build_default_config())


def data_config():
    return deepcopy(dict(request_context().config))


def configure_data(values, *, merge=True):
    resolved = data_config() if merge else {}
    for key, value in deepcopy(values).items():
        if merge and isinstance(value, dict) and isinstance(resolved.get(key), dict):
            resolved[key].update(value)
        else:
            resolved[key] = value
    return _policy.set(DataRequestContext(resolved))


def reset_data(token):
    _policy.reset(token)


@contextmanager
def data_policy(values, *, merge=False):
    token = configure_data(values, merge=merge)
    try:
        yield data_config()
    finally:
        reset_data(token)
