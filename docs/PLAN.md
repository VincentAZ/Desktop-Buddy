# Implementation plan

## Jane UI completion audit — October 27

The selected deployment model is IT-provided assistants. IT must be able to open Buddy and change models and vendors; Jane must not need developer accounts, keys, GitHub or MCP configuration for daily work.

- [x] Native normal screen hides technical model controls; Settings → IT setup exposes vendor setup and saved assistant switching. First-use and model-switch fixtures verify these paths and preserve drafts/history.
- [x] Readable control modes, work-folder access, document attachments and original preservation; saved preferences can be edited without JSON.
- [x] App connection cards keep custom server fields separate; unavailable Google integrations do not send customers into technical setup.
- [x] Error recovery, stop, permissions and restart persistence pass native regression checks.
- [x] Stable-source verification: `run-gg2d6skf` passed all 29 checks, including 179 unit tests and 11 desktop fixtures.
- [x] Command review now requires a plain-language proposal tied to the exact pending step, shows folder/time/network scope, and retains exact instructions under Technical details. Native allow/skip tests verify real file effects and denial; missing explanations and claimed approvals cannot bypass host checks.
- [x] Agent guidance now names the actual controls and directs vendor/model setup to IT. Computer-access text makes remembering permission conditional. Final command-review renders were inspected; file-picker failures have readable recovery messages.

UI completion verified by `.test-data/verification/run-y3c7n0o9/report.json`: 181 unit tests, display preflight and all 12 native/desktop scenarios passed; source fingerprints match before and after. Desktop and sidebar launchers still point to this checkout. This closes the Jane/IT UI goal. It does not certify the product’s unfinished Google integration or universal live-model/desktop-control acceptance; the separate release gates below remain open.

## Current delivery pass — October 27

- [x] Reinspect this workstation and preserve the existing dirty checkout; no commits.
- [x] Recheck current Hermes Blank Slate, Codex provider/app-server source, provider APIs and native Wayland/MCP alternatives.
- [x] Retain the implemented host-native harness rather than start a competing project.
- [x] Reinstate the main-window model selector and narrow accelerated approvals.
- [x] Close local-only GUI/custom-workflow network paths; retain observation, files and isolated shell.
- [x] Distinguish blocked diagnostics from absent services; fix false-success GUI startup.
- [x] Add bounded test execution with unique evidence paths and source fingerprints; use fresh physical-workflow output directories.
- [x] Verify an isolated offline installation outside the checkout, including all eight skills.
- [ ] Finish host-side native UI validation, GNOME screen/input consent, LM Studio GUI and live two-provider acceptance. This Codex session cannot access display/D-Bus or network sockets. Host test run requested; no security restriction was bypassed.

See [current delivery](DELIVERY.md). The previous milestones and test counts below are historical, not proof of current acceptance.

## Active hardening and interface rebuild

- [x] Replace the hidden model-name button with a persistent MODEL bar and explicit Change model action.
- [x] Separate connection, model selection, and Use model; disable inference checks until a model is selected.
- [x] Search saved models across providers, preserve the current chat/draft, and expose next-message and stop-and-switch choices.
- [x] Verify the redesigned setup and switching windows with native integration tests and inspected renders. After the old process closed, reopen the updated application using the existing profile; invoke Change model through accessibility and verify the live picker and saved local model.
- [x] Replace the provider's raw model dropdown with a deterministic browser: no implicit first choice, 16-item default candidate view, complete-catalog search, and an explicit show-all control. Use immediate text notifications so filtering updates reliably.

- [x] Replace the text console with native conversation cards, expandable actions, a compact composer, and a quieter sidebar.
- [x] Add history search, conversation rename, and confirmed deletion of associated database records.
- [x] Add saved-model selection and group provider settings with advanced options collapsed.
- [x] Validate conversation controls through native dialogs; inspect normal, compact, settings, and populated renders.
- [x] Replace raw memory/audit editors with readable native forms and expandable details.
- [x] Complete the first-use model connection flow, provider save/selection behavior, working-folder picker, and core keyboard navigation checks.
- [x] Bundle skills in the wheel; verify installed launch outside the checkout and user-installer paths containing spaces.
- [x] Make repeated launch activate the existing window without reopening the database.
- [ ] Verify physical screenshot and input with GNOME consent on the now-unlocked session.
- [ ] Finish physical-screenshot vision and two-provider switching; real LM Studio text, image, and tool calling now pass.
- [ ] Finish the LM Studio GUI check; its installed local service, CLI, model discovery, inference, and tool calling pass. ComfyUI status, one-job, and batch workflows are verified live.
- [ ] Re-run the ten acceptance tests, document the actual scope of each result, and fix failures.

Current evidence: the full desktop suite passes **77 automated tests** plus the native UI and real browser/file/terminal workflows. The redesigned UI passes provider save/selection, image attachment add/remove/cleanup, keyboard actions, conversation/memory lifecycle, secure transcript and exclusive-image deletion, permission denial, emergency stop (~0.20 s), repeat launch, and separate-process restart checks. A real AT-SPI action changed and re-read state in a dedicated GTK window. Normal, compact, settings, memory and populated renders were inspected. Installed wheel launch and all seven bundled skills pass outside the checkout. The live ComfyUI 0.42.0 service completed a one-job workflow and a two-job batch through the actual registry, with one approval per operation, verified history, output copies, and hashes. LM Studio's loopback service and `qwen/qwen3-vl-8b` were installed by another application; Buddy discovered both models, passed exact-response, image-understanding, and native tool-call probes, saved the model configuration, restarted, and completed a real UI task in 0.1 seconds. The vision probe correctly identified a project-owned 1180 × 860 UI image as Desktop Buddy. GNOME is unlocked, but the physical portal input probe timed out awaiting consent. No LM Studio graphical launcher/window was found, so the GUI-specific check remains open.

## Initial implementation (not full acceptance)

- [x] Inspect workstation and existing installations without modification.
- [x] Inspect Hermes Blank Slate, Codex app server, provider protocols, Linux MCP options.
- [x] Select architecture and record tradeoffs.
- [x] Build runnable core: provider adapters, agent loop, filesystem and shell.
- [x] Implement consent-based Wayland observation/input and accessibility.
- [x] Implement native UI, seven providers, secure configuration and switching.
- [x] Complete persistence, checkpoints, memory, skills, approvals, cancellation.
- [x] Run deterministic protocol/security tests, native UI workflow, physical browser/terminal/file tests.
- [ ] Finish live-provider and physical screenshot/input acceptance after workstation unlock and provider setup.
- [x] Deliver launch script, setup guide, evidence, limitations and troubleshooting.

Remaining live prerequisites: physical screenshot/input requires GNOME portal consent. Live screenshot vision, a second real provider, and LM Studio's graphical interface still need validation. No existing system settings will be changed.

The locked-session and 60-test notes in the initial report are historical. Current evidence and remaining work are listed above and in TEST_RESULTS.md.
