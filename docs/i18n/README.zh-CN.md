# TradingAgentsX

[English](../../README.md) · **简体中文** · [日本語](README.ja.md)

TradingAgentsX 是一个面向本地单用户的投资研究运行中心。它将 React Web
界面、版本化 FastAPI、SQLite 持久队列与 evidence-first LangGraph 工作流
组合在一起，支持美股、日股、中国 A 股以及兼容 Yahoo 符号的其他标的。

系统输出的是研究结论，不是账户指令。最终契约包括评级、置信度、论点、
证据引用、催化剂、风险、失效条件和时间范围；不会生成仓位比例、账户配置、
入场价、止损、目标价、订单或组合再平衡建议。

> **独立产品线。** TradingAgentsX 保留源自
> [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
> 的 Git 历史、Apache-2.0 归因和论文引用，但已不再把合并 upstream 作为
> 开发策略。上游仅用于只读监控；相关安全或正确性修复会在审计后选择性重写
> 或 cherry-pick。详见 [ADR 0001](../adr/0001-independent-product-line.md)。

> 本项目是研究工具，不构成金融或投资建议。模型可能出错，数据也可能缺失、
> 过期或不可用。

## Web 运行中心

- **Dashboard：** 队列、带标的简称的最近运行、状态和待结算 outcome。
- **New Run：** 标的、PIT 日期、analysts、Fast/Standard/Deep、
  provider/model、reasoning、报告语言和近期标的建议。
- **Runs：** 当前/归档切换、筛选、搜索、分页，以及可恢复的批量归档管理。
- **Run Detail：** 可恢复的事件时间线、报告 tabs、结构化决策、warning、
  可折叠审计详情、token/tool/wall-time 指标，以及取消、retry、
  恢复归档、基于当前运行新建和导出。
- **Memory：** 检索完整 decision、outcome 与 reflection，展开催化因素、
  风险和失效条件，同时显示标的简称并返回对应运行的研究结论。
- **Settings：** 只读能力列表、非敏感默认值和 API key 是否已配置。

UI 支持 `zh-CN`、`en`、`ja`。界面语言与报告输出语言相互独立。
New Run 只显示已配置的服务商，并在选中后动态获取当前模型目录；发现失败时，
环境默认模型和 quick/deep 各自独立的自定义模型 ID 仍可使用。
Markdown 禁用原始 HTML，并在显示前清洗。

## 快速开始

支持 Python 3.10–3.13。只有开发前端时才需要 Node.js；发布 wheel 已携带
编译后的 Web 静态资源。

```bash
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x
python -m venv .venv
source .venv/bin/activate
pip install .
cp .env.example .env
```

在 `.env` 中配置一个 LLM provider，然后分别启动 Web 和 worker：

```bash
tradingagents serve
```

```bash
tradingagents worker
```

浏览器打开 <http://127.0.0.1:8000>。Web 负责接收和展示任务；默认单并发
worker 领取队列任务，并在后台结算满足条件的 outcome。

不使用 Web 时也可以同步运行：

```bash
tradingagents run 7203.T \
  --date 2026-07-24 \
  --profile standard \
  --output-language ja
```

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

`web` 与 `worker` 共用一个本机 named volume；端口默认只发布到
`127.0.0.1`。需要捆绑 Ollama 时，先在 `.env` 中设置
`TRADINGAGENTS_LLM_PROVIDER=ollama` 和
`OLLAMA_BASE_URL=http://ollama:11434/v1`，然后运行：

```bash
docker compose --profile ollama up --build
```

SQLite 与 WAL 文件必须位于 Web/worker 同一主机的本地文件系统，不能放在
NFS、SMB 等网络文件系统。

## 三种研究模式

三种 profile 共用同一份封存后的 `EvidenceBundle` 和同一个
`ResearchDecision` 契约。

| Profile | 流程 |
| --- | --- |
| Fast | 并行 analysts → 最终研究委员会 |
| Standard | 并行 analysts → bull/bear 并行评审 → Research Judge → 单一 Risk Reviewer → Final Committee |
| Deep | 并行 analysts → bull/bear 与最多两轮定向 rebuttal → Research Judge → aggressive/neutral/conservative 三个 risk lenses 并行 → Final Committee |

Deep 在没有新增 evidence ref 或 claim rebuttal 时提前停止。各 analyst 使用
独立 state channel；provenance 不再依赖 prose 传递。系统中没有 Trader
节点。

## 架构与运行生命周期

`AnalysisService` 统一负责请求规范化、run 创建、memory 检索、graph 执行、
事件/报告/决策持久化、checkpoint 清理和 outcome 调度。Graph node 不直接
写文件或应用数据库表。

```text
queued → running → succeeded | failed | cancelled
```

worker 通过数据库 lease 原子领取任务。lease 过期后可从 LangGraph
checkpoint 恢复：

- `retry` 在同一个 run 下增加 attempt，并可复用兼容 checkpoint；
- “基于此运行新建”先打开可编辑的 New Run 表单，确认提交后才创建关联的新 run
  并重新获取证据；
- cancel 在 graph node 边界协作完成，不会强杀正在执行的 provider 请求；
- 成功或取消后删除 checkpoint，失败时保留到后续处理。

终态运行可在 Runs 页面归档和恢复。归档后会立即退出 Dashboard、Memory、
outcome 结算和近期标的建议。Web 启动时检查一次到期归档；worker 在领取任务前
检查，并在成功后每 24 小时再次执行，失败则 1 小时后重试。默认 30 天后永久
清理，可通过 `TRADINGAGENTS_ARCHIVE_RETENTION_DAYS` 修改；设为 `0` 时关闭
永久清理。

事件先写入数据库，再发送给客户端。SSE 使用 `Last-Event-ID` 回放刷新或断线
期间遗漏的事件，因此浏览器刷新不会丢失进度。

完整说明见 [架构文档](../architecture.md)。

## Python API

```python
from tradingagents import AnalysisRequest, RunProfile, TradingAgents

app = TradingAgents.from_env()
result = app.run(
    AnalysisRequest(
        ticker="7203.T",
        analysis_date="2026-07-24",
        profile=RunProfile.STANDARD,
        output_language="ja",
    ),
    on_event=lambda event: print(event.sequence, event.event_type),
)

print(result.run_id, result.status)
print(result.decision)
```

`AnalysisResult` 返回 `run_id`、状态、规范化标的、typed reports、
`ResearchDecision`、metrics 和 warnings。需要交给独立 worker 时，使用
`TradingAgents.enqueue(request, idempotency_key=...)`。

旧 `TradingAgentsGraph` 公共导出和 `(final_state, decision)` tuple 已删除。
迁移方法见 [breaking migration guide](../migration-independent-platform.md)。

## CLI

CLI 现在是非交互式命令：

```text
tradingagents run TICKER [options]
tradingagents serve
tradingagents worker [--once]
tradingagents runs list|show|cancel|retry
tradingagents memory import PATH [--apply] [--no-backup]
tradingagents export RUN_ID [--format markdown|json] [-o PATH]
tradingagents db backup PATH
```

`memory import` 默认 dry-run。实际导入按内容 hash 幂等，并默认先备份原文件。
Markdown/JSON 只作为显式导出格式；SQLite 是唯一事实源。

## API 与安全

版本化 API 覆盖 run 创建/查询、事件 SSE、cancel/retry、export、
memory、capabilities 与 health。创建 run 时可发送 `Idempotency-Key`，
避免浏览器重复提交；也可在用户确认模板表单后发送终态 run 的
`source_run_id`。OpenAPI 位于 `/openapi.json`。

API key 只从进程环境读取，不写入 SQLite、SSE 或浏览器存储。默认服务只绑定
loopback。显式开启 LAN 时需要：

```dotenv
TRADINGAGENTS_LAN_ENABLED=true
TRADINGAGENTS_LAN_TOKEN=<long-random-token>
TRADINGAGENTS_SESSION_SECRET=<different-long-random-secret>
TRADINGAGENTS_HOST=0.0.0.0
TRADINGAGENTS_PUBLISH_HOST=0.0.0.0
```

Web 登录会将 token 换成签名的 `HttpOnly`、`SameSite=Strict` cookie，
写请求还会检查同源。这是本地单用户安全边界，不是多租户身份系统。

## 市场、日期与证据

| 市场 | 示例 | 专用路径 |
| --- | --- | --- |
| 美国/默认 | `NVDA`, `SPY` | yfinance 默认路线 |
| 日本 | `7203.T` | J-Quants、EDINET、TDnet、日本新闻与宏观数据 |
| 中国 A 股 | `600519.SS`, `000001.SZ` | Tencent/AkShare、CNINFO、Sina、Eastmoney 与中国宏观数据 |
| Crypto/FX | `BTC-USD`, `EURUSD=X` | 兼容的默认路线 |

历史分析以标的所在市场的本地日期为准。Evidence 保留 requested/effective
date、带时区的 available time、实际来源、质量、fallback 和 provenance；
封存时会拒绝未来可见证据。缺数据表示 unknown，不能自动解释成中性或利空。

当 ticker 与 benchmark 已有六个共同完成收盘价时，后台 worker 形成五个
交易区间，记录 raw return、alpha 与短期 reflection。它不是长期 thesis 或
graph 质量的唯一真值。

## 开发与验收

```bash
pip install -e ".[dev]"
pytest -q
ruff check .

npm ci --prefix frontend
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

CI 覆盖 Python 3.10–3.13、Ruff、前端单测、Playwright、OpenAPI/TS 类型
漂移、wheel/fresh-install 以及 Docker Web+worker smoke。

US/JP/CN/crypto 固定 fixtures 验证 evidence ref、PIT、来源归因、研究决策
边界、rating consistency 和 risk recall，但不等于真实模型性能。质量、token、
延迟与 Deep 风险召回门槛必须使用同模型、每场景三次的真实记录，详见
[Graph 评测说明](../graph-evaluation.md)。

## 迁移、备份与许可

- [Breaking migration guide](../migration-independent-platform.md)
- 在线备份：`tradingagents db backup /path/to/backup.db`
- 旧 report 目录保留为只读档案，不迁移旧 checkpoint。
- reports、events、decisions、outcomes 和 reflections 默认长期保留。
- 首版不提供永久删除 API。

TradingAgentsX 使用 Apache-2.0 许可证，详见 [LICENSE](../../LICENSE) 与
[NOTICE](../../NOTICE)，并保留对原 TradingAgents 项目和论文的归因。
