> October 26 update: capability tests are optional. Unknown tool/image support is attempted automatically; explicit off remains available in Advanced settings. Legacy unchecked flags migrate on startup. GPT-6.1 Sol uses Responses for tools and low/medium/high/xhigh/max thinking. See [the audit](AUDIT_2026-10-26.md). Older setup terminology below is historical.

# Provider setup

Desktop Buddy runs locally and requires no Buddy account, registration or sign-in. IT connects it directly to an existing local model or a cloud endpoint through **Settings → IT setup**. Leave the API key blank when the endpoint requires none; the endpoint decides whether credentials are needed. Service permissions for connected apps are separate from model setup.

All providers are configured through the application. Never paste API keys into chat, source files, shell arguments or Git. Keys are stored through libsecret in GNOME Keyring. There is no plaintext fallback. An optional local-server API key can also be saved in the keyring.

| Provider | Default base URL | Default protocol | Discovery |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | Responses | `/models` |
| Anthropic | `https://api.anthropic.com/v1` | Messages | paginated `/models` |
| xAI | `https://api.x.ai/v1` | Responses | `/models` |
| Moonshot / Kimi | `https://api.moonshot.ai/v1` | Chat Completions | `/models` |
| OpenRouter | `https://openrouter.ai/api/v1` | Chat Completions | `/models` |
| LM Studio | `http://127.0.0.1:1234/v1` | Chat Completions | `/models` |
| Ollama | `http://127.0.0.1:11434` | native NDJSON chat | `/api/tags` |

Models are discovered from the configured account/service, or supplied manually. No model names are hardcoded: availability, capabilities and prices change. Discovery success proves endpoint access, not inference or tool capability. Use the separate inference and tool-calling tests.

Capabilities are explicitly saved per model. When choosing an unfamiliar model, tool and vision flags reset. Set context and output limits from that exact model's documentation/service configuration. The defaults (32,768 for local servers, 128,000 for cloud providers) are configuration values, not claims about a model; a context set too small forces Buddy to compact history every step, which makes it re-inspect state it already saw. Models without tools can chat in Planning; Execution refuses them. A non-vision model can use accessibility text but cannot request screenshot/image interpretation or receive new composer attachments. Historical image attachments are omitted when switching to a non-vision model; textual history remains.

Reasoning configuration is protocol-specific: OpenAI/xAI use an effort string; Moonshot/OpenRouter/compatible endpoints use `reasoning_effort` when supplied; Ollama accepts `true`, `false`, or a model-supported string; Anthropic accepts `adaptive` or an integer thinking-token budget supported by that model. Leave blank for provider defaults. Unsupported combinations return an understandable error rather than silently dropping capabilities. Native reasoning/signature blocks are replayed only to the same provider/model. Stateless OpenAI and xAI Responses calls explicitly request encrypted reasoning and replay the completed output items around function calls.

Anthropic defaults to API version `2026-10-21`, the version recommended by the official documentation inspected on the implementation date. It uses bearer authentication and avoids removed temperature parameters. Version can be configured for compatibility. Keys that can access multiple workspaces may also need the optional workspace ID. Some models no longer accept manual thinking budgets; choose `adaptive` where documented.

Input/output token counts are saved from provider responses. OpenRouter requests usage data explicitly and retains reported costs where returned. Enter per-million input/output prices for an **estimate**; cache, reasoning, image and provider-specific pricing may make this differ from your bill. No stale price table is bundled. Inspect Activity log for per-request usage and elapsed time; the conversation footer aggregates the current task.

HTTP uses TLS verification, disables ambient proxy/.netrc credentials and redirects, and bounds retries for 429/502/503/504. It does not retry ambiguous connection failures after submission or incomplete streamed function calls. Maximum three attempts; capped backoff. Inference I/O is a killable short-lived subprocess with anonymous pipes; no secret appears in its argv or environment.

Local services must bind numeric loopback addresses. Connect to an existing service; this app does not install LM Studio/Ollama or load a duplicate inference engine. An inference request may ask that service to load the selected model using its normal behavior. Local-only mode rejects Ollama models reporting remote delegation or using cloud identifiers. You must trust the local server itself to run inference locally.

## Official protocol references inspected

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) and [Responses](https://developers.openai.com/api/docs/guides/responses).
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create) and [version history](https://platform.claude.com/docs/en/api/versioning).
- [xAI text generation](https://docs.x.ai/developers/model-capabilities/text/generate-text); its [Chat Completions documentation](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions) recommends Responses for new integrations.
- [Kimi thinking and tool-call retention](https://platform.kimi.ai/docs/guide/use-thinking-models), which documents the Moonshot base URL above.
- [OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming).
- [LM Studio compatibility endpoints](https://lmstudio.ai/docs/developer/openai-compat) and [tools](https://lmstudio.ai/docs/developer/openai-compat/tools).
- [Ollama chat](https://docs.ollama.com/api/chat).

Wire compatibility is covered with local fixture tests. No actual cloud account or local inference model was available for live validation during this build.
