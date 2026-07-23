# TradingAgentsX

<div align="center">

[English](../../README.md) · **简体中文** · [日本語](README.ja.md)

</div>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg"/>
</div>

一个多智能体 LLM 交易研究框架，为美股、日本股票（`.T`）以及沪深
A 股（`.SS`/`.SZ`）提供一等支持的数据链。不同市场的数据源汇入同一套
智能体图，并明确约束分析日期、记录来源出处和可审计的降级过程。

> **Fork 说明。** 本项目是
> [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
> 的独立维护 fork（Apache-2.0），不会与上游版本逐一同步。上游署名与
> 许可信息保留在 [LICENSE](../../LICENSE) 和 [NOTICE](../../NOTICE) 中。

<div align="center">

[概览](#概览) · [市场支持](#市场支持) · [安装](#安装) ·
[CLI](#cli-用法) · [日本市场](#日本市场) · [中国 A 股](#中国-a-股) ·
[Python API](#python-api) · [开发](#开发) ·
[架构](../architecture.md) · [变更记录](../../CHANGELOG.md)

</div>

## 概览

TradingAgentsX 模拟一支小型投资团队：市场、基本面、新闻和情绪四类分析师
准备证据；多空研究员展开辩论；交易员提出仓位；最后由风险团队和投资组合
经理给出最终决策。

```text
分析师 → 多空辩论 → 研究经理 → 交易员
       → 风险辩论 → 投资组合经理 → 复盘
```

<p align="center">
  <img src="../../assets/schema.png" style="width: 100%; height: auto;">
</p>

框架既支持交互式 CLI，也支持直接通过 Python 调用。分析师可以独立选择，
LLM 提供商可以配置。CLI 与直接调用 `TradingAgentsGraph.propagate()` 的运行
共用跨轮次复盘日志，也可以通过可选的 LangGraph 检查点恢复中断的分析。

> TradingAgentsX 是研究框架，其输出不构成金融、投资或交易建议。结果取决于
> 模型行为、数据质量、时间边界和运行配置。

## 市场支持

系统内部使用与 Yahoo 兼容的规范股票代码。支持的别名会在市场路由前统一
规范化，例如 `600519` 转换为 `600519.SS`，`000001` 转换为
`000001.SZ`，`600519.SH` 转换为 `600519.SS`。不支持或存在歧义的中国
内地六位代码会明确报错，不会意外落入美股路由。

| 市场 | 示例 | 数据链 |
| --- | --- | --- |
| 美国/默认 | `NVDA`、`SPY` | 基于 yfinance 的默认路由 |
| 日本 | `7203.T` | J-Quants 和日本本土披露源，以及已配置的降级源 |
| 中国 A 股 | `600519.SS`、`000001.SZ` | 腾讯/AkShare，以及中国基本面、新闻和宏观数据源 |
| 其他 Yahoo 市场 | `0700.HK`、`AZN.L`、`RELIANCE.NS` | 默认 Yahoo 兼容行为；没有专用本地市场数据链 |
| 加密货币/外汇 | `BTC-USD`、`EURUSD=X` | Yahoo 兼容默认路由及受支持的别名 |

目前中国市场的专用支持范围是沪深个股。北交所 `.BJ`、香港 `.HK`、ETF、
基金、期权以及中国市场盘中高频数据不在本阶段范围内。

### 默认数据源路由

箭头表示有序降级，括号表示多个来源会被组合使用。

| 市场 | 行情与指标 | 基本面与财报 | 个股新闻 |
| --- | --- | --- | --- |
| 美国/默认 | yfinance | yfinance | yfinance |
| 日本 `.T` | J-Quants → yfinance | 日本市场 assembler → J-Quants → yfinance | (EDINET + TDnet + Google News) → yfinance |
| 中国 `.SS`/`.SZ` | 腾讯 qfq → 东方财富 qfq → yfinance | (CNINFO + 新浪) → yfinance | (CNINFO + 东方财富研报 + Google News) → yfinance |

全球新闻分析师还会获得跨区域宏观面板。美国及全球单元格使用 FRED；日本
单元格使用日本银行、e-Stat、财务省和 FRED；中国单元格使用国家统计局、
东方财富、国家外汇管理局以及范围经过刻意限制的 ChinaMoney 降级源。某个
来源缺失时，只会禁用其负责的单元格。

路由、缓存、point-in-time 和失败处理约定详见
[架构文档](../architecture.md)。

### 数据完整性与来源记录

- 面向智能体图的工具从工作流状态接收分析日期。历史分析不会静默注入仅适用
  于当前时点的数据快照。
- 数据源链是显式配置的。路由器不会增加未配置的降级源；有意组合多个来源的
  逻辑由 assembler 负责。
- 结果通过结构化 provenance 保留请求日期、有效日期、实际来源，以及时间或
  降级状态。
- 重要降级、陈旧数据、缺失或部分覆盖、截断，以及 non-PIT/non-vintage 限制
  会显示在 `Data Quality Warnings` 下。新闻窗口成功返回空结果时不会产生警告。
- 设置 `provenance_appendix = True` 或
  `TRADINGAGENTS_PROVENANCE_APPENDIX=true` 可附加详细的英文
  `Data Provenance` 表格。即使关闭该表格，重要警告仍然可见。

## 安装

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x

python -m venv .venv
source .venv/bin/activate
pip install .
```

开发环境请安装 `dev` extra：

```bash
pip install -e ".[dev]"
```

### Docker

```bash
cp .env.example .env  # 添加你实际使用的密钥
docker compose run --rm tradingagents
```

使用随项目提供的 Ollama 服务：

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

## 配置

复制环境变量模板，并配置一个 LLM 提供商：

```bash
cp .env.example .env
```

项目为 OpenAI、Anthropic、Google、Azure OpenAI 和 Amazon Bedrock 提供原生
客户端。OpenAI 兼容注册表覆盖 xAI、DeepSeek、Qwen、GLM、MiniMax、
OpenRouter、Mistral、Kimi、Groq、NVIDIA NIM、Ollama，以及 vLLM、
LM Studio 等任意兼容端点。各提供商的变量请参阅
[.env.example](../../.env.example)。

Amazon Bedrock 需要执行 `pip install ".[bedrock]"`。Azure 用户可以从
`.env.enterprise.example` 开始配置。Ollama 默认使用
`http://localhost:11434/v1`，可通过 `OLLAMA_BASE_URL` 修改。

常见示例：

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
OPENROUTER_API_KEY=...
```

使用任意兼容端点时，将 `llm_provider` 设置为 `"openai_compatible"`，
配置 `backend_url`（或 `TRADINGAGENTS_LLM_BACKEND_URL`）；仅当端点需要
鉴权时才提供 `OPENAI_COMPATIBLE_API_KEY`。

### 可选市场数据密钥

首期中国市场数据链无需密钥。日本数据源会彼此独立地降级，因此以下密钥均非
必需：

```dotenv
JQUANTS_API_KEY=...  # 行情、摘要以及取决于订阅方案的持仓数据
EDINET_API_KEY=...   # 法定披露、持股变动和要约收购
ESTAT_APP_ID=...     # 日本 CPI 序列
FRED_API_KEY=...     # 美国/全球宏观数据及部分日本降级序列
```

## CLI 用法

```bash
tradingagents
# 或
python -m cli.main
```

CLI 可选择股票代码、分析日期、分析师、研究深度和 LLM 提供商。CLI 与
Python 智能体图使用相同的规范股票代码和市场路由。

<p align="center">
  <img src="../../assets/cli/cli_init.png" width="100%" style="display: inline-block;">
</p>

## 日本市场

对于东京市场代码，四类分析师都会使用本土数据源，而不是完全依赖覆盖较薄的
Yahoo 英文数据。

| 领域 | 主要证据 |
| --- | --- |
| 行情/技术面 | J-Quants v2 调整后日线和经过验证的快照 |
| 基本面 | J-Quants 摘要、披露安全的比率、基于 TOPIX 周线的 beta，以及筛选后的近实时财报明细 |
| 新闻 | EDINET 文件、TDnet 适时披露、Google News Japan 和带标签的交易所板块背景 |
| 情绪 | 个股融资融券/做空数据、EDINET 大量持股和要约收购文件，以及仅用于实盘的评级数据 |
| 宏观 | 日本银行政策利率/Tankan、e-Stat CPI、财务省日频国债收益率和 FRED 降级源 |

J-Quants Light 覆盖行情、摘要和交易所板块资金流。Standard 还会解锁个股
融资融券及空头仓位信号，不需要 Premium。没有 J-Quants 密钥时，行情和近
实时基本面可以降级到 yfinance。如果降级源缺乏披露时间戳，历史财报会采取
fail closed。没有 EDINET 时，其他新闻来源仍可独立运行。

日本历史数据链会在可获得时强制执行发布日期边界。当日 EDINET 列表使用短期
缓存，已沉淀的文件列表使用有界磁盘缓存；财务省收益率数据只有在下一个工作日
日本时间 09:30 的发布边界之后才会进入分析。

## 中国 A 股

目前中国市场以沪深个股的低频分析为目标。

| 领域 | 主要证据 |
| --- | --- |
| 行情/技术面 | 腾讯前复权（`qfq`）OHLCV；依次降级至东方财富和 yfinance |
| 基本面 | CNINFO 公司资料，加上经过披露日期过滤的新浪财务摘要和报表 |
| 新闻 | 按精确代码匹配的 CNINFO 公告、东方财富研报和中文 Google News |
| 情绪 | 沪深交易所融资融券、持股变动、评级/目标价和重要公告 |
| 宏观 | 1 年期 LPR、中国 10 年期国债、CPI、GDP、失业率、制造业 PMI 和 USD/CNY 中间价 |

行情、验证快照和技术指标共用同一份 qfq 历史数据。通常的技术指标预热只需
一次有界腾讯请求；只有需要更长历史时才进行分页。由于不同提供商的复权因子
可能不同，降级时会替换整个请求窗口。

公司证据按来源组装，并分别保留 provenance。缺少可信可见时间元数据的历史
数据会 fail closed，或明确标记为 non-PIT。低频 CNINFO 和东方财富候选数据
共用一个按股票代码和分析截止日期严格隔离的有界缓存，使新闻和情绪分析可以
复用结果而不会跨越分析日期边界。

中国宏观数据的时间语义因来源而异。国家统计局数据同时保留发布日期和观察期；
GDP 是年初至今累计同比。只有在找不到符合要求的近期国家统计局发布时，CPI、
GDP 和 PMI 才会降级到按观察期过滤的东方财富序列，并明确标记为
non-vintage。USD/CNY 中间价以国家外汇管理局为主源。ChinaMoney 仅作为
最新收益率曲线快照的降级源，不扩展为历史抓取器。

AkShare 和无密钥中国市场 assembler 依赖公开网页端点，其 schema、分页和
反爬行为可能随时变化。生产使用时应监控有效日期、实际来源和质量警告，不应
把 HTTP 成功直接视为数据新鲜的证明。

## Python API

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["quick_think_llm"] = "gpt-5.4-mini"
config["deep_think_llm"] = "gpt-5.5"
config["quick_reasoning_effort"] = "low"
config["deep_reasoning_effort"] = "high"

graph = TradingAgentsGraph(debug=True, config=config)
final_state, decision = graph.propagate("600519", "2026-07-17")
print(decision)
```

`propagate()` 返回 `(final_state, decision)`。所有配置项请参阅
[`tradingagents/default_config.py`](../../tradingagents/default_config.py)。

### 按角色配置推理强度

`quick_reasoning_effort` 和 `deep_reasoning_effort` 分别配置两类模型角色。
对应的环境变量为：

```dotenv
TRADINGAGENTS_QUICK_REASONING_EFFORT=low
TRADINGAGENTS_DEEP_REASONING_EFFORT=high
```

角色专用值的优先级高于旧的提供商级设置。使用 `provider_default` 可以省略
原生 SDK 参数，并阻止旧配置回退。具体支持的级别取决于提供商和模型；CLI
目录是精选模型选项的准确信息源。

## 持久化与恢复

成功完成的 CLI 与 `TradingAgentsGraph.propagate()` 运行都会把决策追加到
`~/.tradingagents/memory/trading_memory.md`，无需先保存 CLI 报告。以后针对同一
股票发起新的运行时，系统可以比较实际原始收益与相对基准收益，并在投资组合经理
的上下文中加入简短复盘。可通过 `TRADINGAGENTS_MEMORY_LOG_PATH` 修改路径。

检查点功能默认关闭。以下参数都作用于根命令：

```bash
tradingagents --checkpoint
tradingagents --no-checkpoint
tradingagents --clear-checkpoints
```

- 不传 `--checkpoint` 或 `--no-checkpoint` 时，采用
  `TRADINGAGENTS_CHECKPOINT_ENABLED` 的开关状态；未设置该环境变量时默认为关闭。
- `--checkpoint` 会强制启用检查点，`--no-checkpoint` 会强制关闭检查点，两者的
  优先级都高于环境变量。
- `--clear-checkpoints` 会在问卷开始前删除全部检查点数据库，但它本身不会启用
  检查点；本次运行是否启用仍由前两项规则决定。

启用检查点后，如果已经存在匹配的已保存运行，CLI 会自动从该状态继续；如果没有，
则会从头开始分析，并在节点完成后持续保存新的检查点。匹配条件包括规范化股票代码、
分析日期、分析师组合、辩论深度、风险深度和资产类型；任一条件不同都会开始新的运行。

匹配的检查点通常来自上一次已启用检查点、但在成功完成前意外中断的运行，而且中断前
至少已有一个状态写入 SQLite。成功完成的运行会追加决策并清除对应的 checkpoint
thread，因此下次不会恢复已经成功完成的分析。

直接通过 Python 调用时，可在图配置中设置 `checkpoint_enabled=True`。每只股票的
SQLite 检查点会写入
`~/.tradingagents/cache/checkpoints/`；可通过 `TRADINGAGENTS_CACHE_DIR`
修改基础目录。

## 可复现性

LLM 采样和实时数据意味着重复运行很难做到字节级一致。历史分析通过排除仅适用
于实盘的社交数据、身份快照和财报快照来减少一个主要漂移来源。对于给定的已
获取 payload，精确公司身份、规范股票代码、验证行情快照、来源信息和日期截止
边界都是确定的。

降低 `temperature` 只对支持它的模型有帮助；推理模型通常不会采用该参数。
应把本框架视为研究脚手架，而不是具有固定、可复现收益的策略。

## 开发

默认测试会禁用项目 dotenv 加载、用占位凭据替换真实密钥，并跳过所有实时网络
契约测试。

```bash
PYTHON_DOTENV_DISABLED=1 uv run --extra dev pytest -q
PYTHON_DOTENV_DISABLED=1 uv run --extra dev ruff check .
```

跨市场实时数据契约测试需要显式启用，并串行执行：

```bash
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --extra dev pytest -q -m live_data
```

实时测试会验证 schema、已完成日期边界、宽泛合理值范围、实际来源和可审计的
降级过程，而不会固定具体价格或行数。默认 pytest 和 CI 会收集但跳过这些测试。

DeepSeek 线级集成测试需要单独显式启用：

```bash
RUN_LIVE_LLM_TESTS=1 DEEPSEEK_API_KEY=... \
  uv run --extra dev pytest -q tests/test_deepseek_reasoning.py -m integration
```

欢迎参与贡献。共享开发规则见 [AGENTS.md](../../AGENTS.md)，长期设计契约见
[架构文档](../architecture.md)，版本历史见 [CHANGELOG.md](../../CHANGELOG.md)。

## 引用

如果本框架支持了你的研究，请引用原始 TradingAgents 论文：

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```
