# LLM provider support levels

TradingAgentsX can construct clients for several LLM providers, but a provider
appearing in Settings or New Run does not mean that the complete Research Graph
has the same reliability on every model. Provider APIs differ in reasoning,
tool calling, structured output, schema subsets, response metadata, and
truncation behavior.

This document describes project validation, not the general quality of a model
or provider. A model catalog response proves only that a model can be selected;
it does not prove end-to-end Research Graph compatibility.

## Levels

| Level | Meaning |
| --- | --- |
| **Validated** | The current graph has repeated end-to-end live coverage, including tool collection, reasoning Markdown, structured Agenda, Final core and numeric serialization, local semantic validation, recovery, usage metrics, and persistence. |
| **Preview** | A native client, model discovery, configuration, reasoning controls, and mocked/offline contracts exist, but the complete current graph has not received equivalent live validation. Provider-specific failures may still occur. |
| **Compatibility** | The provider uses an OpenAI-compatible or optional adapter. Basic transport is supported, but capabilities vary by endpoint and model; successful model discovery or chat completion is not an end-to-end guarantee. |

## Current matrix

| Provider setting | Level | Scope and known limits |
| --- | --- | --- |
| `deepseek` with `deepseek-v4-flash` | **Validated** | Primary validated configuration. Thinking JSON Output is used for semantic structured tasks such as Debate Agenda and Final numeric generation; schema-focused calls, bounded recovery, and local Pydantic/Evidence/formula/date validation are covered by repeated live runs. Validation is specific to the listed V4 model and current prompts, not every future DeepSeek model ID. |
| `deepseek` with other model IDs | **Preview** | Some known DeepSeek models have explicit capability rules, but they have not received the same current end-to-end regression coverage as V4 Flash. Unknown future IDs deliberately do not inherit V4 behavior. |
| `openai` | **Preview** | Native Responses/Chat transport, reasoning-effort mapping, structured output, discovery, and offline tests exist. The complete current Research Graph has not yet been live-qualified on a fixed OpenAI model pair. |
| `anthropic` | **Preview** | Native client, effort mapping, structured output through the LangChain adapter, discovery, and offline tests exist. Serializer clients currently reuse the selected reasoning clients; the complete graph has not yet been live-qualified. Native structured-output availability also depends on the selected Claude model. |
| `google` | **Preview** | Native Gemini client, thinking-level mapping, structured output through the LangChain adapter, discovery, and offline tests exist. Thinking/function-calling behavior and thought-signature handling vary by Gemini generation; the complete graph has not yet been live-qualified. |
| `azure` | **Preview** | Azure OpenAI transport and reasoning mapping exist. Compatibility depends on the model behind the deployment, while a custom deployment name may not identify model capabilities precisely. No equivalent end-to-end live matrix exists yet. |
| `xai`, `qwen`, `qwen-cn`, `glm`, `glm-cn`, `minimax`, `minimax-cn`, `openrouter`, `mistral`, `kimi`, `groq`, `nvidia` | **Compatibility** | These use OpenAI-compatible endpoints. Several known model quirks are handled, but tool choice, JSON mode/schema support, reasoning parameters, token metadata, and model routing may differ by provider and model. They are best-effort until a specific model configuration is qualified. |
| `ollama`, `openai_compatible` | **Compatibility** | Intended for Ollama, vLLM, LM Studio, llama.cpp servers, and compatible relays. The client avoids forcing an object-form `tool_choice`, but the server and model must still implement sufficiently reliable tool/structured output. Local chat success alone is insufficient. |
| `bedrock` | **Compatibility** | Optional `langchain-aws` adapter using the Converse API. Availability and structured-output/schema support are model-specific, and Bedrock supports only a subset of JSON Schema features. It has no current end-to-end live qualification in this project. |

## What remains provider-independent

After a provider response has passed the typed boundary, the following
application rules are the same for every provider:

- Evidence sealing, provenance, point-in-time checks, and future-data rejection.
- Local Pydantic and semantic validation.
- Evidence and Memory reference allowlists.
- Restricted formula evaluation, numeric-date resolution, and audit status.
- SQLite persistence, checkpoints, retry boundaries, Web rendering, and export.

These rules prevent an accepted response from bypassing the application
contract. They cannot make an unsupported provider transport emit a valid
response in the first place.

The manually triggered Japanese incremental-research experiment uses the
configured quick serializer for one bounded semantic Change Assessment. A
provider/model's support level therefore still applies to that call. Invalid or
indeterminate structured output receives at most one repair and then escalates
to Full Analysis; `experimental` mode never turns a provider failure into No
Material Change. The experiment is disabled by default and restricted to
source-qualified supported Japanese equities. US and mainland-China Research
Chains remain Full-only, as described in
[incremental-research-experiment.md](incremental-research-experiment.md).

## Known portability boundaries

The most likely provider-specific failures are:

1. A model rejects `tool_choice`, JSON mode, JSON Schema keywords, or a
   reasoning parameter accepted by another provider.
2. A thinking model returns a different tool-call or reasoning-content shape
   than the adapter expects.
3. `include_raw` exposes invalid candidates in a provider-specific shape, so a
   bounded repair cannot recover the candidate as precisely.
4. Finish reasons, reasoning-token usage, cache usage, or truncation metadata
   are missing or named differently.
5. A proxy or model catalog advertises a model but silently routes it to a
   backend with different capabilities or context limits.

For this reason, custom model IDs remain selectable, but unknown capabilities
must not be interpreted as validated support.

## Qualification policy

Promoting a provider/model configuration to **Validated** requires an explicit,
opt-in contract run followed by at least one complete fixed-input Standard run.
The checks must cover:

- reasoning Markdown and analyst tool-call round trips;
- shallow schema-focused serialization;
- thinking structured output for Debate Agenda and Final numeric generation;
- strict Final core validation and optional numeric degradation;
- invalid-candidate recovery and truncation behavior;
- usage, reasoning-token, node timing, and provider-reported USD cost
  attribution when available (never a token-count estimate);
- checkpoint persistence and successful export.

Live qualification is never part of default pytest because it incurs external
cost and depends on mutable provider services. It requires explicit approval
and records the provider, exact model IDs, reasoning settings, prompt versions,
and date.

Provider-native references:

- [OpenAI function calling and Structured Outputs](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Amazon Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
