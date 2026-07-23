from rich.console import Console

from cli.main import PROJECT_DESCRIPTION, UPSTREAM_ATTRIBUTION, app


def test_cli_branding_uses_shared_description_and_rich_links():
    assert app.info.help == f"TradingAgentsX CLI: {PROJECT_DESCRIPTION}"
    assert "Built on " in UPSTREAM_ATTRIBUTION
    assert "[link=https://github.com/TauricResearch/TradingAgents]" in UPSTREAM_ATTRIBUTION
    assert "[TradingAgents](" not in UPSTREAM_ATTRIBUTION

    console = Console(record=True)
    console.print(UPSTREAM_ATTRIBUTION)
    assert console.export_text().strip() == "Built on TradingAgents by Tauric Research"
