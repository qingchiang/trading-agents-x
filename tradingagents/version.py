"""Runtime package identity shared by outbound clients."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION_NAME = "trading-agents-x"
PROJECT_URL = "https://github.com/qingchiang/trading-agents-x"


def _distribution_version() -> str:
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _distribution_version()
USER_AGENT = f"{DISTRIBUTION_NAME}/{__version__}"
IDENTIFIED_USER_AGENT = f"{USER_AGENT} (+{PROJECT_URL})"
BROWSER_USER_AGENT = f"Mozilla/5.0 {USER_AGENT}"
