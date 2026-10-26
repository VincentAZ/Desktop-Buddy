# ADR 001 — Small host-native harness

Decision date: 2026-10-25. Build a single-process Python/GTK application with one sequential agent loop, SQLite, Requests, GNOME Secret Service, AT-SPI, and XDG portals. No model runtime, server port, daemon, orchestration platform, or container is required. No system configuration changes are needed.

## Evidence and alternatives

| Criterion | Hermes Blank Slate | Codex app server | Small custom harness |
|---|---|---|---|
| Complexity | Existing broad platform; features can be disabled | Mature Rust service plus client and provider bridges | Small policy/loop/UI; protocol maintenance is ours |
| Resource overhead | 46 declared base requirements in inspected pyproject (platform markers apply); no runtime benchmark taken | Extra subprocess; no runtime benchmark taken | Native GTK and installed Python libraries; measure actual app |
| Reliability | Mature recovery/memory machinery | Mature coding loop, streaming, persistence, approvals | Must prove invariants with tests; lower initial maturity |
| Desktop control | Extend through tools/MCP; host service needed for container | Extend via MCP/dynamic tools | Host portals and AT-SPI directly |
| Provider independence | Good; existing provider integrations | Current WireApi enum only Responses; Chat wire API explicitly removed | Native Responses, Messages, Chat Completions and Ollama protocols |
| Extensibility | Tool/plugin ecosystem | App-server protocol and MCP | Small registry and readable skills |
| Security | Need audit/configuration of broad existing features | Strong coding sandbox; GUI remains separate | Narrow tools, explicit approvals, no inbound ports; host shell is powerful |
| Maintenance | Follow upstream, avoid fork | Follow upstream and maintain bridges | Own loop/adapters; reuse mature OS and HTTP components |
| Operational cost | Same inference costs plus upstream feature surface | Same inference costs plus bridges/service | No subscription or extra model; bounded calls, local inference |

Hermes is MIT licensed. Its inspected Blank Slate functions select file/skills/terminal/vision and disable compression and memory, plus optional features. It is viable and is the fallback if the custom loop grows disproportionately. Blank Slate is a configuration preset, not a separately maintained tiny runtime. There is no local `~/.hermes` or Hermes installation in the inspected usual locations; nothing existing was modified.

Codex is Apache-2.0 licensed. App server can run independently over stdio and provides conversations, approvals and events. The inspected current provider enum only supports Responses. Supporting native Anthropic and other protocols through a bridging service would add operational complexity for this requirement. We do not copy/fork Codex.

Pydantic AI was considered as a mature provider/agent library. Its broader dependency set would require a new environment on this machine (Python 3.14, no pip/venv bootstrap installed). A deliberately small loop is viable because the core needs sequential tool calls, not graphs. Reconsider the library before growing bespoke protocol logic.

## Desktop choice

GNOME Wayland, Ubuntu 26.04.1, kernel 7.0.0-38, actual display :0/wayland-0. GTK4, libadwaita, GI, GStreamer pipewiresrc/pngenc/appsink, libei, AT-SPI and Secret Service are present. GstApp typelib is absent; generic signal-based appsink can be used. GPU: RTX PRO 6000 Blackwell, 97,887 MiB reported, driver 595.99.02. Python 3.14.4; Ollama and `lms` are absent from PATH. Cursor, Chrome and Docker CLI 29.1.3 are installed; Ptyxis is installed. The system Docker daemon is active, but user `vincent` is not in the `docker` group and cannot access the root-owned socket. No permission or group setting was changed. No local inference listener was detected.

Evaluated Linux MCP projects: wayland-control offers extensive AT-SPI/window adapters but some paths require ydotool/uinput or a GNOME extension; portal_use uses consent-based PipeWire/EIS and an MCP server. Their design informed backend selection; neither is installed, imported or executed. Native portal RemoteDesktop version 2 is available here, including keysym/pointer calls. Use this existing interface without root/input-group changes. X11 support is optional through installed xdotool/wmctrl; it is not assumed on Wayland. GNOME does not expose universal arbitrary window geometry control: use accessible focus and compositor keyboard interactions, and report limitations honestly.

## Boundaries

GTK owns the UI; one worker executes tasks. The model only sees discover/run tool envelopes and on-demand schemas. The policy engine checks each actual operation. SQLite stores redacted events/checkpoints before effects; interrupted effects are marked uncertain and never replayed automatically. Memory is separate and explicitly editable. Skills are instructions, not executable permissions.

The screen portal requires compositor approval. Screen content is sensitive and can reach the selected cloud model only after per-task disclosure/approval. Input and general host commands require explicit action approval. Low-risk workspace file operations proceed in execution mode. Planning denies mutating tools. Local-only mode blocks cloud providers, arbitrary HTTP/browser/GUI input; approved shell uses network-isolated bubblewrap and fails closed if isolation is unavailable. Local inference endpoints must be numeric loopback and trusted to actually run locally; known Ollama cloud models are rejected. This is not a sandbox against a malicious same-user process or a dishonest local inference server.

## Sources inspected

- [Hermes source](https://github.com/NousResearch/hermes-agent), inspected tree ff294849385e433dd40a993b4f07673abb95c6f5; pyproject.toml, run_agent.py, hermes_cli/setup_quick.py, setup.py, Blank Slate tests and MIT license.
- [Hermes configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration), [desktop](https://hermes-agent.nousresearch.com/docs/user-guide/desktop).
- [Codex source](https://github.com/openai/codex), inspected tree 970111f6cb62cc02fe36a82ea7d71b37a2889c97; model-provider-info/src/lib.rs, app-server/README.md, protocol config and Apache license.
- [Codex app server](https://learn.chatgpt.com/docs/app-server), [provider configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).
- [Pydantic AI](https://pydantic.dev/docs/ai/models/overview/).
- [Portal protocol source](https://github.com/flatpak/xdg-desktop-portal/tree/main/data), [wayland-control](https://github.com/christian-korneck/wayland-control), [portal_use](https://github.com/johnohhh1/portal_use).
