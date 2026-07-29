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
- **Memory:** 完全な decision、outcome、reflection を検索し、catalyst、
  risk、invalidation と銘柄名を表示して元の run の判断へ戻る。
- **Settings:** provider/model capability、安全なデフォルト値、API key の
  設定有無を読み取り専用で表示。

UI は `ja`、`en`、`zh-CN` に対応します。UI locale とレポート出力言語は
独立しています。New Run は設定済み provider のみを表示し、選択時に現在の
model catalog を取得します。取得失敗時も環境のデフォルト値と quick/deep
それぞれの custom model ID を利用できます。Markdown は raw HTML を無効化し、
表示前に sanitize します。

## クイックスタート

Python 3.10–3.13 をサポートします。Node.js が必要なのはフロントエンド開発時
だけで、リリース wheel にはビルド済み Web assets が含まれます。

```bash
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x
python -m venv .venv
source .venv/bin/activate
pip install .
cp .env.example .env
```

`.env` に利用する LLM provider を設定し、ローカルの Web と worker を
まとめて起動します。

```bash
tradingagents start
```

両方は独立した子プロセスのまま動作し、統合出力には `[web]` と
`[worker]` の色付き接頭辞が付き、`NO_COLOR` も尊重します。最初の Ctrl+C
は協調停止を要求し、もう一度押すか 30 秒経過すると残りの子プロセスを
強制終了します。中断された分析は次回の worker が checkpoint から再開
します。ローテーションログが必要な場合だけ `--log-dir PATH` を指定します。
個別のプロセス管理が必要な場合は従来のコマンドも使用できます。

```bash
tradingagents serve
tradingagents worker
```

ブラウザで <http://127.0.0.1:8000> を開きます。Web は run の受付・表示を
行い、デフォルトで同時実行数 1 の worker がキューを処理し、outcome を
バックグラウンドで確定します。

Web を使わない同期実行:

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
| Standard | 並列 analysts → bull/bear 並列レビュー → Research Judge → 単一 Risk Reviewer → Final Committee |
| Deep | 並列 analysts → bull/bear と最大 2 ラウンドの targeted rebuttal → Research Judge → aggressive/neutral/conservative の risk lenses → Final Committee |

Deep は新しい evidence ref も claim rebuttal も追加されなければ早期終了
します。各 analyst は独立した state channel を使用し、provenance を prose
経由で受け渡しません。Trader node はありません。

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
tradingagents memory import PATH [--apply] [--no-backup]
tradingagents export RUN_ID [--format markdown|json] [-o PATH]
tradingagents db backup PATH
```

`memory import` はデフォルトで dry-run です。apply 時は content hash により
冪等で、通常は元ファイルを先にバックアップします。Markdown/JSON は明示的な
export 形式であり、SQLite が唯一の source of truth です。

## API とセキュリティ

バージョン化 API は run の作成・参照、event SSE、cancel/retry、
export、memory、capabilities、health を提供します。run 作成時に
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
| Crypto/FX | `BTC-USD`, `EURUSD=X` | 互換 default route |

historical analysis の cutoff は instrument market のローカル日付です。
Evidence は requested/effective date、timezone 付き availability、実際の
source、quality、fallback、provenance を保持し、seal 時に未来可視データを
拒否します。データ欠落は unknown であり、中立・弱気シグナルではありません。

ticker と benchmark に 6 個の共通 completed close が揃うと、worker は
5 trading interval の raw return と alpha、および短期 reflection を保存
します。これは長期 thesis や graph 品質の唯一の正解ではありません。

## 開発・リリースゲート

```bash
pip install -e ".[dev]"
pytest -q
ruff check .

npm ci --prefix frontend
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

CI は Python 3.10–3.13、Ruff、frontend unit test、Playwright、
OpenAPI/TypeScript drift、wheel/fresh install、Docker Web+worker smoke を
検証します。

US/JP/CN/crypto の固定 fixtures は evidence refs、PIT、source attribution、
research-only decision、rating consistency、risk recall の契約テストです。
実モデルの性能測定ではありません。quality、token、latency、Deep risk-recall
のリリース基準には、同一モデル・各 scenario 3 回の記録が必要です。詳細は
[graph evaluation](../graph-evaluation.md) を参照してください。

## 移行・バックアップ・ライセンス

- [Breaking migration guide](../migration-independent-platform.md)
- オンライン backup:
  `tradingagents db backup /path/to/backup.db`
- 旧 report directory は読み取り専用 archive とし、旧 checkpoint は移行
  しません。
- reports、events、decisions、outcomes、reflections は長期保持します。
- 初版には permanent-delete API がありません。

TradingAgentsX は Apache-2.0 で提供されます。[LICENSE](../../LICENSE) と
[NOTICE](../../NOTICE) を参照してください。元の TradingAgents プロジェクト
と論文への帰属表示を維持します。
