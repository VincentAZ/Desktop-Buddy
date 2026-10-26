# Desktop Buddy

A standalone Linux desktop assistant: one model, one sequential harness, native GTK interface, local state, explicit control. Runs independently of Cursor or Codex. Built for this workstation's GNOME Wayland session.

**Status: pre-alpha.** The task-focused native interface has been rebuilt for everyday use. The October 26 audit fixes the false tool-capability gate, message/draft loss, provider and cancellation issues, and file/browser/ComfyUI defects. The deterministic suite passes 109 tests, with additional native UI, browser and accessibility checks. Live paid-model acceptance remains unverified. See the [audit and evidence](docs/AUDIT_2026-10-26.md).

![Desktop Buddy](docs/screenshots/desktop-buddy-redesign.png)

## Start

From this checkout:

```bash
./launch.sh
```

No installation is needed for a source launch on the inspected workstation. For an independent user installation, run `python3 scripts/install.py`; it builds an offline wheel, installs into a private environment using existing system libraries, and creates a GNOME launcher. This installer has been tested in an isolated project directory; your personal application menu has not been modified. No startup service or background agent is installed.

```bash
./launch.sh --doctor
./scripts/run-tests.sh
./scripts/run-tests.sh --desktop  # opens only dedicated test browser/terminal windows
```

Use `--data-dir /a/path/you/choose` for a separate local profile. One process may use a data directory at a time.

## First use

1. Click **Set up Buddy**. Choose your AI service, paste its API key if needed, and click **Connect**. If you want a cloud service, turn off **Keep Buddy offline** in that dialog. Pick a model and click **Use model**. Your key is stored in GNOME Keyring.
2. Tell Buddy what you need and click **Send**, or press **Ctrl+Enter**. **Help me do it** allows actions, with approval for sensitive steps. **Just discuss** prevents changes. **YOLO — do anything** runs commands, edits files, uses apps and makes online requests for your task without Buddy approval prompts. It is saved until you change it.
3. Use **Task options** (the gear beside the attachment button) to choose a working folder, change thinking level, manage offline mode, connect computer control, or take a screenshot. The initial folder is `workspace/` in this checkout.
4. Click the model name at the top to switch models without losing your conversation. During a task, the selection applies to your next message; **Stop task & switch** cancels before switching.
5. Open **History** to find previous conversations, saved preferences and activity details. The conversation menu provides rename, delete and continue controls. Text drafts belong to their own conversations and survive a clean restart.

Tools and image inputs are tried automatically unless you explicitly turn them off under **Advanced settings**. You do not need to pass a test or certify a model's capabilities before using it. The optional connection test performs no computer action. Unsupported requests display the service's explanation. Existing unchecked capability flags migrate automatically when the rebuilt app opens. GPT-6.1 Sol uses the Responses API for tools; its thinking menu excludes unsupported levels.

The paperclip adds up to eight images. Unsent images are temporary; switching conversations or closing Buddy removes them. Sent images remain in the saved conversation. Cloud tasks ask before sending conversation text, results and requested images to the selected service, unless you selected YOLO.

Approval dialogs offer **Skip action**, **Allow once**, and **Stop everything**. **Use YOLO for this and future tasks** approves the waiting action and continues the current task without cancelling it.

**Stop** cancels the current task. **Stop everything**, also available through **Ctrl+Shift+Escape** while Buddy has focus, additionally disconnects computer control and closes Buddy's separate browser. It cannot undo completed actions. **Continue task** asks Buddy to inspect the saved progress before proceeding; it does not replay actions automatically.

Offline mode blocks Buddy's own cloud and network tools. Approved actions in other apps may still use those apps' connections. Keys, history, models and preferences persist locally. See [security and privacy](docs/SECURITY.md) for the exact boundaries.

If Buddy was already open during the update, close and reopen it to load the rebuilt interface.

## Included

- Sequential streaming agent loop, on-demand tool discovery, bounded retries/steps, repeated-error detection, durable checkpoints and explicit resume.
- OpenAI/xAI Responses, Anthropic Messages, Moonshot/OpenRouter/LM Studio Chat Completions, and native Ollama adapters. Provider-specific reasoning state, image inputs, model discovery, usage and optional estimated cost.
- Workspace filesystem tools, verified atomic writes, image inspection, approved shell/scripts, system inventory, approved HTTP requests, application launching, clipboard, accessibility and browser control.
- Dedicated numeric-loopback ComfyUI status, single-workflow and 1–50 prompt batch tools with one exact approval, bounded polling, cancellation, verified history, and non-overwriting output copies.
- GNOME portal screen/input implementation: movement, click/double/right click, drag, scroll, text and shortcuts. Input returns “sent, unverified” until another observation confirms the outcome.
- AT-SPI application/window/control inspection and accessible actions. GNOME window operations through compositor keyboard shortcuts; universal arbitrary window geometry is not provided.
- Visible Chrome automation via a private inherited CDP pipe and separate profile. No debugging port or personal-browser attachment.
- Persistent SQLite history/audit/tasks, inspectable editable JSON memory and seven readable workflow skills.
- No model downloads, extra inference runtime, Docker requirement, web server, telemetry, cloud deployment or agent swarm.

## Documentation

- [Architecture decision and discovery](docs/ARCHITECTURE.md)
- [Provider setup and protocol sources](docs/PROVIDERS.md)
- [Security and privacy boundaries](docs/SECURITY.md)
- [Installation and troubleshooting](docs/INSTALLATION.md)
- [Acceptance evidence and known limitations](docs/TEST_RESULTS.md)
- [Running implementation plan](docs/PLAN.md)

Source lives in `desktop_buddy/`; bundled readable skills in `desktop_buddy/skills/`; deterministic tests in `tests/`. Test fixtures simulate APIs and model decisions: they are not evidence that a real model completed the same workflow.
