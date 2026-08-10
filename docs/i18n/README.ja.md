# TradingAgentsX

[English](../../README.md) · [简体中文](README.zh-CN.md) · **日本語**

TradingAgentsX は、ローカルの単一ユーザー向け投資リサーチ実行センター
です。React Web UI、バージョン化された FastAPI、SQLite の永続キュー、
evidence-first な LangGraph ワークフローを統合し、米国株、日本株、中国
A 株、および Yahoo 互換シンボルを扱います。

出力はリサーチ上の結論であり、口座向けの売買指示ではありません。最終契約
には rating、confidence、thesis、evidence refs、catalysts、risks、
invalidation conditions、time horizon が含まれます。ポジション比率、
口座配分、エントリー、ストップ、目標価格、注文、リバランスは生成しません。

> **独立したプロダクトライン。** TradingAgentsX は
> [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
> 由来の Git 履歴、Apache-2.0 の帰属表示、論文引用を維持しますが、
> upstream のマージを開発方針にはしません。upstream は読み取り専用で監視し、
> セキュリティや正確性に関わる修正だけを監査後に再実装または選択的に
> cherry-pick します。詳細は
> [ADR 0001](../adr/0001-independent-product-line.md) を参照してください。

> 本プロジェクトはリサーチツールであり、金融・投資助言ではありません。
> モデルは誤る可能性があり、データも欠落・陳腐化・利用不能になり得ます。

## Web 実行センター

- **Dashboard:** キュー、銘柄名付きの最近の run、状態、未確定 outcome。
- **New Run:** ticker、PIT 日付、analysts、Fast/Standard/Deep、
  provider/model、reasoning、レポート言語、最近使った銘柄候補。
- **Runs:** 現在/アーカイブの切替、検索、フィルター、ページング、
  復元可能な一括アーカイブ管理。
- **Run Detail:** 永続イベントタイムライン、レポート、構造化 decision、
  折りたたみ可能な監査詳細、token/tool/wall-time 指標、cancel/retry、
  アーカイブ復元、現在の run をもとにした新規作成、export。
- **Memory:** 永続化済み Outcome Observation、Reflection の lifecycle、
  バージョン付き Feedback の適格性と理由を個別に確認する。失敗した Reflection
  の再試行、Feedback の廃止、decision の展開、元の run への移動も行える。
  完了した Observation は、source Decision または紐づく Research Revision の
  market-local cutoff を収益基準にできるが、その後に終了しなければならない。
  履歴上のバージョンなし資格状態は、そのまま明示される。
- **Settings:** provider/model capability、安全なデフォルト値、API key の
  設定有無を読み取り専用で表示。

UI は `ja`、`en`、`zh-CN` に対応します。UI locale とレポート出力言語は
独立しています。New Run は設定済み provider のみを表示し、選択時に現在の
model catalog を取得します。取得失敗時も環境のデフォルト値と quick/deep
それぞれの custom model ID を利用できます。Markdown は raw HTML を無効化し、
表示前に sanitize します。Provider を設定できることは、Research Graph 全体が
同じ水準で検証済みであることを意味しません。現在の検証済み構成は DeepSeek
V4 Flash です。OpenAI、Anthropic、Google、Azure のネイティブ統合は preview、
その他の OpenAI-compatible、ローカルモデル、Bedrock アダプターは
compatibility レベルです。範囲と認定方針は
[LLM provider support levels](../provider-support.md) を参照してください。

## クイックスタート

Python 3.10–3.13 と uv 0.12.1 以降をサポートします。Node.js が必要なのは
フロントエンド開発時だけで、リリース wheel にはビルド済み Web assets が
含まれます。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x
uv sync --locked --no-dev
cp .env.example .env
```

`.env` に利用する LLM provider を設定し、ローカルの Web と worker を
まとめて起動します。

```bash
uv run --locked --no-dev tradingagents start
```

両方は独立した子プロセスのまま動作し、統合出力には `[web]` と
`[worker]` の色付き接頭辞が付き、`NO_COLOR` も尊重します。最初の Ctrl+C
は協調停止を要求し、もう一度押すか 30 秒経過すると残りの子プロセスを
強制終了します。中断された分析は次回の worker が checkpoint から再開
します。ローテーションログが必要な場合だけ `--log-dir PATH` を指定します。
個別のプロセス管理が必要な場合は従来のコマンドも使用できます。

```bash
uv run --locked --no-dev tradingagents serve
uv run --locked --no-dev tradingagents worker
```

ブラウザで <http://127.0.0.1:8000> を開きます。Web は run の受付・表示を
行い、デフォルトで同時実行数 1 の worker がキューを処理し、outcome を
バックグラウンドで確定します。

Web を使わない同期実行:

```bash
uv run --locked --no-dev tradingagents run 7203.T \
  --date 2026-07-24 \
  --profile standard \
  --output-language ja
```

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

`web` と `worker` は同じローカル named volume を共有します。ポートは
デフォルトで `127.0.0.1` のみに公開されます。Ollama を使う場合は `.env`
で `TRADINGAGENTS_LLM_PROVIDER=ollama` と
`OLLAMA_BASE_URL=http://ollama:11434/v1` を設定してから実行します。

```bash
docker compose --profile ollama up --build
```

SQLite と WAL ファイルは、Web/worker と同一ホストのローカルファイル
システムに置いてください。NFS、SMB などのネットワークファイルシステムは
サポートしません。

## リサーチプロファイル

すべての profile は、封印済みの同一 `EvidenceBundle` と同一
`ResearchDecision` 契約を使用します。

| Profile | フロー |
| --- | --- |
| Fast | 並列 analysts → 最終リサーチ委員会 |
| Standard | 並列 analysts → bull/bear case → Debate Agenda → 1 回の targeted cross-rebuttal → Research Judge → 単一 Risk Reviewer → Final Committee |
| Deep | 並列 analysts → bull/bear case → Debate Agenda → 必須 1 回と最大 2 回の追加 targeted rebuttal → Research Judge → aggressive/neutral/conservative の risk lenses → Final Committee |

Deep の追加ラウンドは、重要な未解決 issue が残り、新しい evidence、因果
mechanism、または具体的な claim の有効な棄却がある場合だけ実行します。
各 analyst は独立した state channel を使用します。生データと provenance は
封印済み Evidence Ledger に保存し、人が読む report と deliberation は
Markdown と軽量な検証済み audit navigation を使用します。Trader node は
ありません。

## アーキテクチャと lifecycle

`AnalysisService` が request 正規化、run 作成、memory 検索、graph 実行、
event/report/decision 永続化、checkpoint cleanup、outcome scheduling を
一元管理します。Graph node はファイルや application table を直接書きません。

```text
queued → running → succeeded | failed | cancelled
```

worker は database lease で run を原子的に claim します。lease が期限切れに
なった場合は LangGraph checkpoint から復旧できます。

- `retry` は同じ run に attempt を追加し、互換 checkpoint を再利用可能。
- 「この実行をもとに新規作成」は編集可能な New Run フォームを開き、
  確認後に関連 run と新しい evidence snapshot を作成。
- cancel は node 境界で協調的に処理し、実行中の provider request は強制終了
  しない。
- success/cancel 後は checkpoint を削除し、failure 時は次の判断まで保持。

終端状態の run は Runs ページからゴミ箱へ移動・復元できます。移動後は
Dashboard、Memory、outcome 評価、最近の銘柄候補から直ちに除外されます。
Web 起動時に一度、worker は work claim 前と成功後 24 時間ごとに期限切れを
確認し、失敗時は 1 時間後に再試行します。既定の保持期間は 30 日で、
`TRADINGAGENTS_TRASH_RETENTION_DAYS=0` にすると完全削除を無効化します。

Research Chain 更新には、手動でのみ開始する日本株向けの内部増分リサーチ
実験があります。既定値は `off` です。`shadow` は限定評価の候補を保持しつつ
フル分析を正とし、`experimental` は完全な coverage と意味的不変性を満たす
No Material Change 評価だけを、Required Source が完全な対応 `.T` 銘柄で
analyst report や deliberation を再生成せず Revision にできます。重要な変更、
coverage 不足、互換性不良、無効、novelty、不確定な結果は、同じ更新内で
自動的にフル分析へ移行します。米国株と中国本土株の Research Chain は、
引き続き手動のフル分析でのみ更新できます。

Revision Role、Execution Strategy、Change Conclusion は別々に表示されます。
フル再評価でも Material Change と No Material Change のどちらも正当化できない
場合は、読取可能な Indeterminate head を作成し、次回更新にもフル再評価を
要求します。

```dotenv
TRADINGAGENTS_RESEARCH_UPDATE_MODE=experimental
```

mode、source qualification、metrics、opt-in live validation の詳細は
[実験ガイド](../incremental-research-experiment.md) を参照してください。
この機能は更新を schedule せず、production automation や口座別 advice を
提供しません。

event はクライアント送信前に database へ commit されます。SSE は
`Last-Event-ID` から欠落イベントを replay するため、ページ更新でも進捗を
失いません。

詳細は [architecture](../architecture.md) を参照してください。

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

`AnalysisResult` は `run_id`、status、正規化 instrument、typed reports、
`ResearchDecision`、metrics、warnings を返します。別 worker に渡す場合は
`TradingAgents.enqueue(request, idempotency_key=...)` を使用します。

root package が公開するのは `TradingAgents`、`AnalysisRequest`、
`AnalysisResult`、`ResearchDecision`、`RunProfile`、`__version__` のみです。
Evidence、deliberation、numeric audit の内部型は、それぞれの所有 module
から import します。

旧 `TradingAgentsGraph` 公開 export と `(final_state, decision)` tuple は
削除されました。移行方法は
[breaking migration guide](../migration-independent-platform.md) を参照して
ください。

## CLI

CLI は非対話型です。

```text
tradingagents run TICKER [options]
tradingagents start [--color auto|always|never] [--log-dir PATH]
tradingagents serve
tradingagents worker [--once] [--log-level LEVEL]
tradingagents runs list|show|cancel|retry
tradingagents export RUN_ID [--format markdown|json] [-o PATH]
tradingagents db backup PATH
```

Markdown/JSON は明示的な export 形式であり、SQLite が唯一の source of
truth です。

## API とセキュリティ

バージョン化 API は run の作成・参照、event SSE、cancel/retry、
export、memory 参照、Reflection の再試行、Feedback の廃止、capabilities、
health を提供します。run 作成時に
`Idempotency-Key` を送ることで、ブラウザ再送による重複を防げます。
確認済みテンプレートからの作成では、終端 run の `source_run_id` も送信
できます。OpenAPI は `/openapi.json` です。

API key は process environment からのみ読み取り、SQLite、SSE、browser
storage には保存しません。デフォルトは loopback bind です。LAN へ公開する
場合は明示的に設定します。

```dotenv
TRADINGAGENTS_LAN_ENABLED=true
TRADINGAGENTS_LAN_TOKEN=<long-random-token>
TRADINGAGENTS_SESSION_SECRET=<different-long-random-secret>
TRADINGAGENTS_HOST=0.0.0.0
TRADINGAGENTS_PUBLISH_HOST=0.0.0.0
```

Web login は token を署名済み `HttpOnly`、`SameSite=Strict` cookie に交換
します。mutation API は same-origin も検証します。これはローカル単一
ユーザー向けの境界であり、マルチテナント認証ではありません。

## 市場・日付・Evidence

| 市場 | 例 | 専用パス |
| --- | --- | --- |
| US/default | `NVDA`, `SPY` | yfinance default |
| Japan | `7203.T` | J-Quants、EDINET、TDnet、日本の news/macro |
| China A-share | `600519.SS`, `000001.SZ` | Tencent/AkShare、CNINFO、Sina、Eastmoney、中国 macro |

Crypto、明示的な非株式、未対応、または曖昧な instrument は data routing 前に明示的に失敗します。

historical analysis の cutoff は instrument market のローカル日付です。
Evidence は requested/effective date、timezone 付き availability、実際の
source、quality、fallback、provenance を保持し、seal 時に未来可視データを
拒否します。データ欠落は unknown であり、中立・弱気シグナルではありません。

ticker と benchmark に 6 個の共通 completed close が揃うと、worker は 5
trading interval を形成し、versioned market-local Outcome Observation、raw
return、alpha、availability、horizon limitation を先に独立保存します。収益の
baseline は source Decision または関連 Research Revision の cutoff と同日でも
よい一方、それより前にはできず、Observation end は cutoff より後でなければ
なりません。Reflection は独立生成され、失敗しても Observation を削除しません。
Feedback は `outcome_feedback_qualification.v1` の PIT、schema、source、
applicability、content、method、horizon qualification をすべて通過した場合のみ
eligible になります。`available_at` は Observation data availability、Reflection
generation、qualification completion のうち最も遅い時刻です。履歴上の
unversioned status は再計算しません。最初の Research Chain 実験はこれらの
historical Feedback を注入せず、長期 thesis の証明・反証にも使いません。

## 開発・リリースゲート

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .

npm ci --prefix frontend
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

CI は uv 0.12.1 を固定し、Python 3.10–3.13、Ruff、frontend unit test、
Playwright、OpenAPI/TypeScript drift、wheel 内容とその pip fresh install、
Docker Web+worker smoke を検証します。

offline test は application、graph、Evidence、provenance、PIT の契約を
network や LLM call なしで検証します。通過しても model research quality、
latency、token consumption の改善を証明するものではありません。

## 移行・バックアップ・ライセンス

- [Breaking migration guide](../migration-independent-platform.md)
- オンライン backup:
  `tradingagents db backup /path/to/backup.db`
- 旧 report directory は読み取り専用 archive とし、旧 checkpoint は移行
  しません。
- reports、events、decisions、Outcome Observations、Reflections、審査済み
  Feedback は長期保持します。
- 初版には permanent-delete API がありません。

TradingAgentsX は Apache-2.0 で提供されます。[LICENSE](../../LICENSE) と
[NOTICE](../../NOTICE) を参照してください。元の TradingAgents プロジェクト
と論文への帰属表示を維持します。
