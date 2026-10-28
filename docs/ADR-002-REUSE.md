# ADR 002 — Retain and harden the existing Desktop Buddy

Decision: October 27, 2026. Continue the existing Python/GTK implementation in this checkout. Do not create a second assistant, fork Hermes/Codex, install a daemon, or commit changes. This decision supplements the comparison in [ADR 001](ARCHITECTURE.md).

## Current evidence

The existing application already implements the sequential loop, seven provider presets across four wire protocols, tools, native UI, SQLite history/checkpoints, keyring credentials, memory, skills and portal/AT-SPI adapters. Replacing it would discard implemented and testable functionality. The remaining work is integration verification and targeted fixes.

Hermes remains a viable reuse option. Current [Blank Slate source](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/setup_quick.py) enables files, terminal, vision and skills, but disables compression, memory and checkpoints. Those could be re-enabled and desktop tools added through MCP; Blank Slate does not remove the broad runtime dependency surface. Hermes is [MIT licensed](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE). No current `~/.hermes` installation was found. A backup under `Desktop/BackUpFromLinux10242026/home/dotfiles/.hermes` has `terminal.backend: local`; it was inspected without changes or reading credentials. Live Docker enumeration is unavailable in this sandbox, so no assertion is made about currently running Hermes containers.

Codex [app-server](https://developers.openai.com/codex/app-server) can run independently and offers threads, events, approvals and tool integration. Its current [WireApi implementation](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs) supports Responses and explicitly rejects the removed Chat protocol. Native Anthropic Messages, Ollama and Chat-compatible providers would require bridging. Its [Apache-2.0 license](https://github.com/openai/codex/blob/main/LICENSE) permits reuse subject to its conditions; licensing is not the reason to reject it here.

[Pydantic AI](https://pydantic.dev/docs/ai/models/overview/) provides mature model abstractions and should be reconsidered if bespoke provider maintenance becomes substantial. Migrating an existing small loop now has no demonstrated reliability or overhead benefit. Reuse SQLite, Requests, GTK, libsecret, AT-SPI, PipeWire and desktop portals; optional existing MCP support uses the MCP SDK. No comparison benchmark was performed, so no numerical performance advantage is claimed.

## Desktop boundary

The actual host is Ubuntu 26.04.1 / GNOME Wayland. A user-launched host probe confirms RemoteDesktop portal v2 and an NVIDIA RTX PRO 6000 Blackwell with 97,887 MiB, driver 595.99.02. X11 tools are absent. Use the [consent-based RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html) and AT-SPI directly. [wayland-control](https://github.com/christian-korneck/wayland-control) and [portal_use](https://github.com/johnohhh1/portal_use) were evaluated as alternatives; no compositor extension, uinput service or new privileged mechanism is required by the current backend. Exact arbitrary Wayland window geometry remains compositor-dependent.

Codex's current sandbox cannot access the physical display/D-Bus, GPU or network sockets. That is a limitation of this development session, not proof of a broken host. Physical tests run through explicit user-launched test commands, with ordinary GNOME consent. The application remains host-native and independently launchable.

## Changes driven by this specification

Restore the model selector to the main window. Keep sensitive actions explicitly authorized even in accelerated mode. Block GUI/app/custom-workflow network side channels in local-only mode. Make diagnostics distinguish blocked access from absent services. Make startup errors fail the smoke check, and make test runs bounded, isolated and attributable to a source fingerprint.

The operational cost remains the selected model's inference cost, normal local-service resources and maintenance of the small harness/adapters. There is no extra inference model, agent swarm, cloud deployment, messaging integration requirement or background autonomous worker.
