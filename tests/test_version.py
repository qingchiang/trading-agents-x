from importlib.metadata import version

import tradingagents
from tradingagents.dataflows import cn_macro, reddit, stocktwits
from tradingagents.dataflows.cn import cn_sentiment, google_news, sina_ratings
from tradingagents.dataflows.jp import http_util
from tradingagents.version import (
    BROWSER_USER_AGENT,
    DISTRIBUTION_NAME,
    IDENTIFIED_USER_AGENT,
    USER_AGENT,
)


def test_runtime_version_matches_distribution_metadata():
    assert tradingagents.__version__ == version(DISTRIBUTION_NAME)


def test_dataflow_user_agents_share_runtime_package_version():
    assert reddit._UA == IDENTIFIED_USER_AGENT
    assert stocktwits._UA == IDENTIFIED_USER_AGENT
    assert http_util.USER_AGENT == IDENTIFIED_USER_AGENT
    assert cn_macro._UA == BROWSER_USER_AGENT
    assert cn_sentiment._UA == BROWSER_USER_AGENT
    assert sina_ratings._UA == BROWSER_USER_AGENT
    assert google_news._UA == USER_AGENT
