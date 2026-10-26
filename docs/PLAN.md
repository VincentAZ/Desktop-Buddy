# Implementation plan

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
