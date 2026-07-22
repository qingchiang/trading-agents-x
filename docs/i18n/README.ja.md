# TradingAgentsX

<div align="center">

[English](../../README.md) · [简体中文](README.zh-CN.md) · **日本語**

</div>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg"/>
</div>

米国上場銘柄、日本株（`.T`）、上海・深圳 A 株（`.SS`/`.SZ`）のデータフローを
第一級でサポートする、マルチエージェント LLM 投資リサーチフレームワークです。
市場別のデータソースを共通のエージェントグラフへ供給し、分析日の境界、出典、
監査可能なフォールバックを明示します。

> **フォークについて。** 本プロジェクトは
> [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
> の独立管理フォーク（Apache-2.0）であり、上流リリースとは一対一で同期しません。
> 上流への帰属とライセンス情報は [LICENSE](../../LICENSE) と
> [NOTICE](../../NOTICE) に保持されています。

<div align="center">

[概要](#概要) · [対応市場](#対応市場) · [インストール](#インストール) ·
[CLI](#cli-の使い方) · [日本市場](#日本市場) · [中国-a-株](#中国-a-株) ·
[Python API](#python-api) · [開発](#開発) ·
[アーキテクチャ](../architecture.md) · [変更履歴](../../CHANGELOG.md)

</div>

## 概要

TradingAgentsX は小規模な投資チームをモデル化します。市場、ファンダメンタルズ、
ニュース、センチメントの 4 種類のアナリストが根拠を用意し、強気・弱気の研究員が
議論し、トレーダーがポジションを提案します。最後にリスクチームとポートフォリオ
マネージャーが最終判断を行います。

```text
アナリスト → 強気/弱気討論 → リサーチマネージャー → トレーダー
           → リスク討論 → ポートフォリオマネージャー → 振り返り
```

<p align="center">
  <img src="../../assets/schema.png" style="width: 100%; height: auto;">
</p>

対話型 CLI と Python からの直接利用に対応しています。アナリストは個別に選択でき、
LLM プロバイダーも設定可能です。完了した判断は実行をまたぐ振り返りログへ保存でき、
任意の LangGraph チェックポイントによって中断した分析を再開できます。

> TradingAgentsX はリサーチ用フレームワークです。出力は金融、投資、売買の助言を
> 構成しません。結果はモデルの挙動、データ品質、時点、設定に依存します。

## 対応市場

内部では Yahoo 互換の正規化済みシンボルを使用します。対応する別名は市場ルーティング
の前に正規化され、たとえば `600519` は `600519.SS`、`000001` は
`000001.SZ`、`600519.SH` は `600519.SS` になります。未対応または曖昧な
中国本土の 6 桁コードは明示的にエラーとなり、誤って米国市場ルートへ流れません。

| 市場 | 例 | データフロー |
| --- | --- | --- |
| 米国/デフォルト | `NVDA`、`SPY` | yfinance ベースのデフォルトルート |
| 日本 | `7203.T` | J-Quants と日本の開示ソース、および設定済みフォールバック |
| 中国 A 株 | `600519.SS`、`000001.SZ` | Tencent/AkShare と、中国のファンダメンタルズ、ニュース、マクロソース |
| その他の Yahoo 市場 | `0700.HK`、`AZN.L`、`RELIANCE.NS` | Yahoo 互換のデフォルト動作。専用の現地市場データフローはなし |
| 暗号資産/FX | `BTC-USD`、`EURUSD=X` | Yahoo 互換のデフォルトルートと対応別名 |

中国市場の専用対応範囲は、現在、上海・深圳の個別 A 株です。北京 `.BJ`、香港
`.HK`、ETF、ファンド、オプション、中国市場の日中高頻度データは本フェーズの
対象外です。

### デフォルトのデータソースルーティング

矢印は順序付きフォールバック、括弧は複数ソースの組み合わせを表します。

| 市場 | 価格と指標 | ファンダメンタルズと財務諸表 | 個別銘柄ニュース |
| --- | --- | --- | --- |
| 米国/デフォルト | yfinance | yfinance | yfinance |
| 日本 `.T` | J-Quants → yfinance | 日本向け assembler → J-Quants → yfinance | (EDINET + TDnet + Google News) → yfinance |
| 中国 `.SS`/`.SZ` | Tencent qfq → Eastmoney qfq → yfinance | (CNINFO + Sina) → yfinance | (CNINFO + Eastmoney Research + Google News) → yfinance |

グローバルニュースアナリストには、地域横断のマクロパネルも提供されます。米国・
グローバルのセルは FRED、日本のセルは日本銀行、e-Stat、財務省、FRED、中国の
セルは国家統計局、Eastmoney、国家外貨管理局、および意図的に限定した ChinaMoney
フォールバックを使用します。あるソースが欠けても、そのソースが担当するセルだけが
無効になります。

ルーティング、キャッシュ、point-in-time、失敗時の契約については
[アーキテクチャ文書](../architecture.md)を参照してください。

### データ完全性と出典

- グラフ向けツールはワークフロー状態から分析日を受け取ります。過去時点の分析へ、
  現在時点専用のスナップショットを暗黙に混入させません。
- ベンダーチェーンは明示的です。ルーターが未設定のフォールバックを追加することはなく、
  複数ソースを意図的に統合する処理は assembler が担当します。
- 結果は構造化 provenance として、要求日、有効日、実際のソース、時点または
  フォールバック状態を保持します。
- 重要なフォールバック、古いデータ、欠損または部分的なカバレッジ、切り捨て、
  non-PIT/non-vintage の制約は `Data Quality Warnings` に表示されます。
  ニュースウィンドウが正常に空だった場合は警告しません。
- `provenance_appendix = True` または
  `TRADINGAGENTS_PROVENANCE_APPENDIX=true` を設定すると、詳細な英語の
  `Data Provenance` 表を追加します。この表を無効にしても重要な警告は表示されます。

## インストール

Python 3.10 以降が必要です。

```bash
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x

python -m venv .venv
source .venv/bin/activate
pip install .
```

開発時は `dev` extra をインストールしてください。

```bash
pip install -e ".[dev]"
```

### Docker

```bash
cp .env.example .env  # 使用するキーを追加
docker compose run --rm tradingagents
```

同梱の Ollama サービスを利用する場合：

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

## 設定

環境変数テンプレートをコピーし、LLM プロバイダーを 1 つ設定します。

```bash
cp .env.example .env
```

OpenAI、Anthropic、Google、Azure OpenAI、Amazon Bedrock にはネイティブクライアントが
あります。OpenAI 互換レジストリは xAI、DeepSeek、Qwen、GLM、MiniMax、
OpenRouter、Mistral、Kimi、Groq、NVIDIA NIM、Ollama、および vLLM や
LM Studio など任意の互換エンドポイントに対応します。プロバイダー別の変数は
[.env.example](../../.env.example)を参照してください。

Amazon Bedrock には `pip install ".[bedrock]"` が必要です。Azure ユーザーは
`.env.enterprise.example` から設定を開始できます。Ollama のデフォルトは
`http://localhost:11434/v1` で、`OLLAMA_BASE_URL` により変更できます。

主な例：

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
OPENROUTER_API_KEY=...
```

任意の互換エンドポイントでは、`llm_provider = "openai_compatible"` とし、
`backend_url`（または `TRADINGAGENTS_LLM_BACKEND_URL`）を設定します。
`OPENAI_COMPATIBLE_API_KEY` はエンドポイントが認証を要求する場合にだけ指定します。

### 任意の市場データキー

中国市場の初期データフローはキー不要です。日本の各ソースは独立して縮退するため、
次のキーはいずれも必須ではありません。

```dotenv
JQUANTS_API_KEY=...  # 価格、サマリー、プラン依存のポジショニングデータ
EDINET_API_KEY=...   # 法定開示、保有状況、公開買付け
ESTAT_APP_ID=...     # 日本 CPI 系列
FRED_API_KEY=...     # 米国/グローバルマクロと一部の日本向けフォールバック系列
```

## CLI の使い方

```bash
tradingagents
# または
python -m cli.main
```

CLI では、銘柄、分析日、アナリスト、調査深度、LLM プロバイダーを選択できます。
CLI と Python グラフは同じ正規化済みシンボルと市場ルートを使用します。

<p align="center">
  <img src="../../assets/cli/cli_init.png" width="100%" style="display: inline-block;">
</p>

## 日本市場

東京市場の銘柄では、Yahoo の比較的薄い英語圏カバレッジだけに依存せず、4 種類の
アナリストすべてが現地ソースを利用します。

| 分野 | 主な根拠 |
| --- | --- |
| 市場/テクニカル | J-Quants v2 の調整済み日足と検証済みスナップショット |
| ファンダメンタルズ | J-Quants サマリー、開示時点に安全な比率、TOPIX 週次 beta、選別済みの直近財務明細 |
| ニュース | EDINET 書類、TDnet 適時開示、Google News Japan、ラベル付き市場区分コンテキスト |
| センチメント | 銘柄別信用・空売りデータ、EDINET 大量保有・公開買付け書類、ライブ専用レーティング |
| マクロ | 日銀政策金利/Tankan、e-Stat CPI、財務省の日次国債利回り、FRED フォールバック |

J-Quants Light は価格、サマリー、市場区分別フローをカバーします。Standard では
銘柄別の信用・空売りポジションシグナルも利用でき、Premium は不要です。J-Quants
キーがない場合、価格と直近ファンダメンタルズは yfinance にフォールバックできます。
フォールバック先に提出時刻がない過去財務諸表は fail closed となります。EDINET が
なくても、ほかのニュースソースは独立して継続します。

日本の履歴データフローは、利用可能な場合に公表日の境界を適用します。当日の EDINET
一覧は短期キャッシュ、確定済み書類一覧は上限付きディスクキャッシュを使います。
財務省の利回りデータは、翌営業日 09:30 JST の公表境界を過ぎてから分析へ入ります。

## 中国 A 株

現在の中国市場対応は、上海・深圳の個別株を対象とする低頻度分析です。

| 分野 | 主な根拠 |
| --- | --- |
| 市場/テクニカル | Tencent の前方調整（`qfq`）OHLCV。Eastmoney、yfinance の順にフォールバック |
| ファンダメンタルズ | CNINFO 企業プロフィールと、公表日でフィルタした Sina 財務サマリー・諸表 |
| ニュース | コード完全一致の CNINFO 公告、Eastmoney リサーチ、中国語 Google News |
| センチメント | 上海・深圳取引所の信用取引、持株変動、レーティング/目標株価、重要公告 |
| マクロ | 1 年 LPR、中国 10 年国債、CPI、GDP、失業率、製造業 PMI、USD/CNY 基準値 |

価格、検証済みスナップショット、指標は同じ qfq 履歴を共有します。通常のテクニカル
ウォームアップは上限付きの Tencent リクエスト 1 回に収まり、より長い履歴が必要な
場合だけページングします。プロバイダー間で調整係数が異なる可能性があるため、
フォールバック時は要求ウィンドウ全体を置き換えます。

企業情報はソース別に組み立て、それぞれの provenance を保持します。信頼できる公開
時点メタデータがない履歴データは fail closed とするか、non-PIT と明示します。
低頻度の CNINFO と Eastmoney の候補データは、銘柄と分析締切を厳密に分離した上限付き
キャッシュを共有し、ニュースとセンチメントが分析日の境界を越えずに再利用できます。

中国マクロの時点意味論はソースごとに異なります。国家統計局のリリースは公表日と観測
期間を保持し、GDP は年初来累計の前年比です。CPI、GDP、PMI は、条件を満たす直近の
国家統計局リリースがない場合に限り、観測期間でフィルタした Eastmoney 系列へ
フォールバックし、non-vintage と明示します。USD/CNY 基準値は国家外貨管理局を主ソース
とします。ChinaMoney は最新イールドカーブのスナップショットに限定し、履歴クローラー
には拡張しません。

AkShare とキー不要の中国向け assembler は公開 Web エンドポイントに依存します。
schema、ページング、bot 対策は予告なく変わる可能性があります。本番利用では HTTP
成功を鮮度の証明とみなさず、有効日、実際のソース、品質警告を監視してください。

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

`propagate()` は `(final_state, decision)` を返します。全設定項目は
[`tradingagents/default_config.py`](../../tradingagents/default_config.py)を
参照してください。

### ロール別の推論強度

`quick_reasoning_effort` と `deep_reasoning_effort` は 2 つのモデルロールを
個別に設定します。対応する環境変数は次のとおりです。

```dotenv
TRADINGAGENTS_QUICK_REASONING_EFFORT=low
TRADINGAGENTS_DEEP_REASONING_EFFORT=high
```

ロール固有値は従来のプロバイダー全体設定より優先されます。`provider_default` を
指定するとネイティブ SDK パラメーターを省略し、従来設定へのフォールバックも止めます。
対応レベルはプロバイダーとモデルにより異なり、選定済み候補については CLI カタログが
正となります。

## 永続化と復旧

完了した実行は判断を `~/.tradingagents/memory/trading_memory.md` に追記します。
同じ銘柄の後続実行では、実現した素のリターンとベンチマーク相対リターンを比較し、
ポートフォリオマネージャーのコンテキストへ短い振り返りを挿入できます。パスは
`TRADINGAGENTS_MEMORY_LOG_PATH` で変更できます。

チェックポイントからの再開は任意です。

```bash
tradingagents analyze --checkpoint
tradingagents analyze --clear-checkpoints
```

銘柄別 SQLite チェックポイントは `~/.tradingagents/cache/checkpoints/` に保存され、
ベースディレクトリは `TRADINGAGENTS_CACHE_DIR` で変更できます。正常終了した実行は
対応するチェックポイントを削除します。

## 再現性

LLM サンプリングとライブデータのため、再実行をバイト単位で同一にすることは困難です。
過去時点の実行ではライブ専用のソーシャル、企業同定、財務スナップショットを除外し、
大きなドリフト要因の一つを抑えます。取得済み payload が同じであれば、厳密な企業同定、
正規化済みシンボル、検証済み市場スナップショット、出典、日付締切は決定的です。

`temperature` を下げる効果があるのは、その設定を尊重するモデルだけです。推論モデルは
対応しないことが多いため、本フレームワークは固定的で再現可能な収益を持つ戦略ではなく、
リサーチの足場として扱ってください。

## 開発

デフォルトテストはプロジェクトの dotenv 読み込みを無効にし、実キーをプレースホルダーへ
置き換え、すべてのライブネットワーク契約をスキップします。

```bash
PYTHON_DOTENV_DISABLED=1 uv run --extra dev pytest -q
PYTHON_DOTENV_DISABLED=1 uv run --extra dev ruff check .
```

市場横断ライブデータ契約はオプトインで、直列実行します。

```bash
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --extra dev pytest -q -m live_data
```

ライブスイートは schema、完了日締切、広い妥当値範囲、実際のソース、監査可能な
フォールバックを検証し、正確な価格や行数は固定しません。デフォルトの pytest と CI は
これらを収集しますがスキップします。

DeepSeek の wire-level 統合テストは別途オプトインです。

```bash
RUN_LIVE_LLM_TESTS=1 DEEPSEEK_API_KEY=... \
  uv run --extra dev pytest -q tests/test_deepseek_reasoning.py -m integration
```

コントリビューションを歓迎します。共通開発ルールは [AGENTS.md](../../AGENTS.md)、
長期的な設計契約は[アーキテクチャ文書](../architecture.md)、リリース履歴は
[CHANGELOG.md](../../CHANGELOG.md)を参照してください。

## 引用

本フレームワークが研究を支援した場合は、元の TradingAgents 論文を引用してください。

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
