"""Credentials."""

from functools import wraps


def configuration_credentials(function):
    """Bind one credential snapshot around admission or retry data queries."""

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        from tradingagents.credentials import use_credentials

        with use_credentials(self.configuration.execution_credentials()):
            return function(self, *args, **kwargs)

    return wrapped
