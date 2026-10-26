YOLO follow-up: 109 deterministic tests pass. `scripts/test-yolo-ui.py` verifies a real approval dialog switches the current task into YOLO without cancellation, subsequent app/file/cloud requests do not prompt, the setting survives reopening, and the 760-pixel layout fits. Native agent, approval-denial, restart and emergency-stop tests also pass.

# Current acceptance evidence

The October 26 rebuild and audit supersede earlier UI descriptions and test totals. See [the current audit](AUDIT_2026-10-26.md) for scope, 103-test results, native UI evidence and remaining live-provider/portal limitations. Historical evidence follows.

# Validation and acceptance report

Date: 2026-10-25. Host: Ubuntu 26.04.1, GNOME Wayland, Python 3.14.4, RTX PRO 6000 Blackwell. Tests used dedicated project-owned files/profiles and harmless commands. No dependency installation, root execution, desktop security setting change, or existing Hermes modification occurred.

**Status: working alpha; full end-to-end acceptance remains incomplete.** LM Studio is configured on loopback with `qwen/qwen3-vl-8b`; real text, image, tool-call, persistence, and full UI agent-loop checks pass. The desktop is unlocked, but the latest physical screen/input probe timed out awaiting normal GNOME consent and sent no input. No LM Studio graphical launcher/window was found.

## Interface rebuild and hardening

### Model setup and switching correction

- The conversation now has a persistent **MODEL** bar with the model/provider name and **Change model**. The picker searches saved models across providers and preserves history and unsent drafts.
- Setup is **Connect provider → Choose model → Use model**. Discovery accepts an empty model ID; inference checking and Use model stay disabled until a selection exists. Connection fields collapse after discovery. Advanced API options and ComfyUI settings are collapsed separately. Local-only can be changed in the setup dialog itself, without silently enabling cloud requests.
- **Use for next message** leaves the running task on its existing provider snapshot. **Stop task & switch** waits for cancellation before changing selection; it never automatically replays a task.
- **81 automated tests passed**, including discovery without a model/capability flags for all seven provider adapters and deterministic model-catalog classification/sorting. `scripts/test-model-ui.py` passed actual GTK and local HTTP fixture checks for blank-model discovery, no implicit first selection, immediate case-insensitive search, full-catalog search of initially hidden model types, saved-model search, tool verification, credential-save flow with a keyring fixture, same-conversation cross-provider history, draft preservation, switching while running, stop-and-switch, stale discovery cancellation, visible privacy controls, and thinking-level persistence/request encoding. No paid/cloud request was made by these tests.
- The provider's raw dropdown was replaced after reproducing its broken filter behavior. The browser now shows at most 16 undated text/reasoning candidates initially, ordered by model family/version and then natural name. Search scans every reported model; **Show all provider models** exposes the complete catalog. Against the saved 145-entry OpenAI catalog, `gpt-5.6 sol` returned exactly `gpt-5.6-sol`, `embedding` returned the three embedding models, and `gpt-5.4 mini` returned the alias plus its dated snapshot.
- Thinking level is now a visible provider-aware control beside the active model and in model setup. The GTK integration test selected **High**, verified it persisted with that model, and verified the next Responses request contained `reasoning: {effort: high}`.
- The corrected source was restarted against the existing profile. In the live accessibility tree, entering uppercase `QWEN` immediately left `qwen/qwen3-vl-8b` visible and removed the embedding model from the results, confirming case-insensitive filtering in the running app.
- Native conversation lifecycle and compact 940 × 700 checks passed after the change. The agent/UI workflow, denial, emergency stop (~0.20 s), and separate-process restart checks also passed. A main-window snapshot was unavailable in one workflow run; foreground main-window and dialog renders were captured separately and inspected. This is application-render evidence, not physical-screen acceptance.
- Updated UI artifacts: `.test-data/model-ui/{setup,connected,picker}.png`, `.test-data/model-ui/live-render-1-window.png`, `.test-data/model-ui/result.json`, and `.test-data/conversation-ui/{welcome,compact,history-image}.png`.
- Updated wheel: `.test-data/model-ui/dist/desktop_buddy-0.1.0-py3-none-any.whl`, SHA-256 `cd4681d31834442ff8c8a337a0fe5ea214605d6e3a03ce9350c8015f86e5e76b`. Installed offline into an isolated project-owned target; native smoke launch passed from outside the checkout with the installed package on `PYTHONPATH`.
- Model-browser fix wheel: `.test-data/model-ui/dist-search/desktop_buddy-0.1.0-py3-none-any.whl`, SHA-256 `7a75908d171145007ab717b0458ed7790d290394e623fd85723dde12e0b52ca0`. Its isolated offline install and native smoke launch passed from `/tmp`.
- The old process was left open while changes were built to preserve its unsaved input. After it closed, the updated source was launched with the existing `.buddy` profile. AT-SPI found the new **Change model** control, invoked it, and verified the live picker, search, add-model action, Use model action, and saved Qwen model. Evidence: `.test-data/model-ui/running-app.json`. No API key was read from the old window.

- Replaced the plain transcript console with conversation cards, expandable action details, a compact composer, searchable history, saved-model selection, grouped provider settings, and readable memory/activity windows.
- `scripts/test-conversation-ui.py` exercised rename, title search, cancellation of deletion, confirmed deletion of messages/tasks/audit, memory creation, cancelled memory removal, and saved memory removal through actual native widgets. All passed in an isolated profile.
- Saved visual observations now render as bounded transcript thumbnails after history reload, with an explicit Open action and a clear missing-file state. The native fixture rendered and inspected this persisted-history path.
- The provider/settings dialog exposes the loopback-only ComfyUI endpoint, saves it through the native UI, and offers a direct status test; non-loopback validation remains enforced by the client.
- Native renders of the welcome view, settings, memory form, populated conversation, and 940 × 700 compact window were inspected. These are application renders, not a physical desktop screenshot or vision-model acceptance.
- Repeated application launch now activates the existing window. A second actual process exited successfully without opening the locked database. SQLite exclusivity protection remains.
- The latest `./scripts/run-tests.sh --desktop` passed **77 automated tests in 6.04 seconds**, then the native agent workflow, UI lifecycle, compact render, real AT-SPI action check, and real browser/file/terminal workflow. Both inference and model discovery cancel through killable workers.
- Composer image attachments are normalized, previewed, removable, discarded with abandoned drafts, persisted with sent messages, and rejected before inference for models without image support. Conversation deletion removes an exclusively referenced private image while preserving a shared image and an outside artifact.
- `scripts/install.py` built and installed an offline wheel into a project-owned path containing spaces. From outside the source checkout, the installed package imported from site-packages, loaded all seven bundled skills, selected XDG state paths, and opened its native window successfully. No personal-menu or system dependency installation was performed.
- ComfyUI is reachable on `127.0.0.1:8188`: `/system_stats` reported version 0.42.0 and the NVIDIA RTX PRO 6000. A real model-free 96 × 64 image workflow completed and its output was copied into `.test-data/comfyui-live/outputs/` with matching hash. A two-job batch then passed through the actual tool registry with one approval, two verified completion records, and two copied outputs. Stop propagation to a specific ComfyUI job is fixture-tested.
- LM Studio's installed `lms` service is reachable only on `127.0.0.1:1234`. Buddy discovered `qwen/qwen3-vl-8b` and the embedding model. Its real provider adapter returned the exact requested text, identified the application in a project-owned 1180 × 860 image as `Desktop Buddy`, and produced a valid native `connection_probe` tool call with `{"value":"buddy-test"}`. After saving the verified model and restarting Buddy, a request sent through the actual GTK composer completed in 0.1 seconds and rendered `live UI works.`; the task record is complete. The other application performed the installation and model download; this project did not duplicate or alter that setup.
- `scripts/test-portal-desktop.py --control` exported a real parent-window handle and requested GNOME control consent. It exited with **“GNOME consent dialog timed out”**. Physical capture/input is still not claimed passed.
- The redesigned UI used approximately 283 MiB RSS in the latest fixture run; emergency stop prevented the next action in approximately 0.20 seconds. No overhead improvement over the old UI is claimed.
- After the cancellation change, the native UI workflow passed again. The rebuilt wheel was installed into an isolated path containing spaces; all 20 provider tests passed against its site-packages code from outside the source checkout.
- The old idle Buddy process was verified to have no running task and restarted with the redesigned source using the existing `.buddy` profile. Re-launching activated that window successfully. No conversations were deleted from the user's actual profile.
- The real AT-SPI backend inspected a dedicated GTK window, found the button's exposed `click` action, invoked it, and then observed the changed “Accessibility action verified” state. This verifies accessible GUI action and outcome checking on the physical session without claiming portal pointer/keyboard control.
- Docker CLI 29.1.3 and the active system daemon were detected. The ordinary user cannot access the root-owned Docker socket and is not in the `docker` group, so no container operation is claimed and no privilege configuration was changed.

Artifacts: `.test-data/conversation-ui/{welcome,compact,providers,memory}.png`, `.test-data/conversation-ui/result.json`, `.test-data/ui-integration/{ui.png,result.json}`, and `.test-data/portal-desktop/result.json`.

## Initial implementation evidence (before the rebuild)

- `./scripts/run-tests.sh --desktop`: **60 automated tests passed** in approximately **3.15 seconds**, followed by native UI and real desktop application workflow checks.
- `python3 scripts/test-ui.py`: actual GTK app → local HTTP model fixture → provider worker → agent loop → verified filesystem write/read. Switched through the actual UI from the LM Studio Chat protocol fixture to the Ollama native protocol fixture and back without restarting. This validates integration/switching, not live model reasoning.
- The UI permission dialog was constructed and its **Deny** button activated; the action was denied.
- A harmless approved `sleep` task was started through the UI. The actual **EMERGENCY STOP** button cancelled it and prevented a subsequent marker-file action. Latest measured stop latency: **0.198 seconds** (200 ms polling resolution).
- The UI profile was closed, a **separate app process** launched with the same data directory, and settings/history checked again. Provider/model, local-only setting, workspace and conversation count persisted.
- `python3 scripts/test-physical-workflow.py`: real isolated Chrome window → local test form fill/click → verified report write/read → real standalone Ptyxis window → matching command readback. A deliberately missing selector failed safely; a correct selector recovered. No AI model made those decisions.
- Separate real browser navigation to `https://example.com` returned final URL `https://example.com/`, title **Example Domain**, ready state **complete**, and page text through CDP. Its isolated browser process was then closed.
- Standalone Ptyxis ran `printf "Desktop Buddy terminal verified\n"` into a designated test file. File readback matched. AT-SPI exposed the terminal window/control, but not the terminal's text on this session; output verification used the command's dedicated test file.
- Real bubblewrap execution succeeded. A process inside its network namespace could **not reach a listening host-loopback socket**, establishing that local-only shell isolation worked on this host.
- `./launch.sh --smoke-test` and `python3 -m compileall -q desktop_buddy scripts tests` passed. The native app window was instantiated on the real desktop session. A separate widget render was visually inspected; this is not a screenshot of the physical desktop.
- GNOME Secret Service was present. Credential calls and failure-without-plaintext-fallback were tested with mocks. No live keyring-write test was performed, to avoid changing the user's keyring without a supplied credential.

Automated coverage includes seven HTTP provider adapters, multiline SSE/comment handling, fragmented tool JSON, signed/encrypted reasoning replay, provider switching, stream truncation, context compaction with whole exchanges, image normalization, checkpoint repair, repeated-error limits, workspace/symlink boundaries, Planning restrictions, destructive/network approvals, redaction, singleton state locking, namespace isolation, process cancellation and stalled-inference cancellation. Portal input packing/button release is covered with mocks; physical input is not claimed tested.

## Required acceptance tests

| Test | Status | Evidence / remaining work |
|---|---|---|
| 1. Desktop observation and correct description | **Incomplete** | AT-SPI identified Cursor, Chrome and Ptyxis. A vision-capable local model is now configured, but the latest portal probe timed out awaiting consent; no physical screenshot-description claim. |
| 2. Terminal interaction | **Passed at tool level** | Real Ptyxis ran harmless command; dedicated output file read back exactly. GUI terminal text was not exposed through AT-SPI. No model-driven demonstration. |
| 3. LM Studio GUI interaction | **Partial** | Installed `lms` CLI/service, model discovery, exact response, structured tool call, Buddy restart persistence, and a real UI-agent response pass. No graphical LM Studio executable, desktop entry, or window was found, so graphical interaction is not claimed. |
| 4. Browser navigation | **Passed at tool level** | Physical Chrome window loaded public example.com; URL/title/content verified. Uses a separate profile, not the personal browser. |
| 5. File manipulation | **Passed** | Create/read/modify/read tests and real native UI agent loop with deterministic model fixture; bytes and hashes verified. |
| 6. Two-provider switch | **Partial** | Native UI switched between two protocol fixtures without restart; seven adapter fixtures pass. Two real configured providers/models still needed. |
| 7. Multi-application workflow | **Passed at tool level** | Real Chrome form, filesystem report, real Ptyxis readback. A separate real ComfyUI → filesystem workflow and two-job batch also passed. The local model now passes Buddy's agent loop, but it has not yet driven this cross-application workflow. |
| 8. Error recovery | **Passed with stated scope** | Scripted agent recovered from missing file; physical browser workflow recovered from missing selector. Real-model diagnosis remains untested. |
| 9. Emergency stop | **Passed with stated scope** | Actual UI button cancelled running harmless task in ~0.20 s; no subsequent marker file. Separate test killed a stalled inference worker. Active physical input session revocation remains untested. |
| 10. Persistence | **Passed** | Separate app-process restart retained history and settings; crash-state test marked unfinished tasks interrupted without replay. |

## Earlier locked-session evidence

During the initial implementation, `loginctl show-session 2` reported **LockedHint=yes**. The portal logs reported:

- Screenshot access dialog: only the focused app may show the system dialog.
- RemoteDesktop: session creation inhibited.

The app checks the GNOME lock state and exports its Wayland window handle for consent dialogs. It does not unlock the workstation, change security policy or use privileged input as a fallback. The later session is unlocked; accepting GNOME's normal consent dialog remains necessary to finish validation.

## Measurements and artifacts

The redesigned native UI process used approximately **285 MiB RSS** in the latest fixture run (291,340 KiB, including GTK/GPU renderer initialization). This excludes the separately launched Chrome and model services. The report does not infer an overhead advantage over Hermes or Codex: those alternatives were inspected but not installed/benchmarked. Another application installed LM Studio and the local Qwen model; Buddy connected to that existing service without starting a duplicate runtime.

Full local artifacts, excluded from Git:

- `.test-data/final-tests.log`: automated suite and initial integration output.
- `.test-data/ui-integration/result.json`: latest UI workflow, switching, stop latency and restart checks.
- `.test-data/physical-workflow/result.json`: browser/form/file/terminal evidence.
- `.test-data/physical-workflow/workflow-report.txt` and `terminal-readback.txt`: matching workflow output.
- `.test-data/acceptance/browser-result.json`: public-site observation.
- `.test-data/acceptance/terminal-result.txt`: terminal command output.
- `.test-data/doctor.json`: environment capabilities.
- `.test-data/accessibility-control/result.json`: real AT-SPI action and changed-state evidence.
- `.test-data/comfyui-live/` and `.test-data/comfyui-batch-live/`: workflow JSON, copied outputs and local audit evidence.
- `.test-data/lmstudio-live/result.json`: redacted live model discovery, response, tool-call, UI-loop, and persistence evidence.
- `.test-data/final-dist-v2/desktop_buddy-0.1.0-py3-none-any.whl`: final tested wheel, SHA-256 `6e478f1c681f1c58398e282c395aa04bc7f6162677f536be3aa3df8f6ebc7ac7`.

Whole-window native renders now succeed for normal and compact layouts, provider settings, memory, and a populated conversation. These validate application rendering only and do not substitute for physical desktop capture.

## Remaining limitations

1. Live LM Studio text, project-image vision, and structured tool calling pass. Physical screenshot vision, longer autonomous tool quality, cloud credentials, a second real provider, and provider-specific reasoning settings still need live checks.
2. Portal/PipeWire capture and physical mouse/keyboard/drag/clipboard/window outcomes still need GNOME consent. The session is unlocked; the focused safe-input probe timed out waiting for consent.
3. GNOME has no universal unprivileged arbitrary-window geometry API. The build uses accessibility focus and GNOME window shortcuts; no extension/uinput setup is installed.
4. Local-only blocks Buddy's cloud providers, browser, arbitrary HTTP, and shell networking. Exact-approved local GUI/app actions remain available; Buddy cannot prevent a third-party GUI app or ComfyUI custom node from using its own network connection. It also depends on the selected inference service actually staying local.
5. Command exit status is not proof of an arbitrary higher-level goal. The agent is instructed to verify meaningful outcomes; model judgment cannot be guaranteed by the harness.
6. Cancellation cannot undo completed effects, guarantee rollback, or stop deliberately detached services outside the managed process groups.
7. ComfyUI execution and batch output handling are verified with a model-free image workflow. A real model-based generation workflow still needs a user-selected workflow/model. Docker is installed but inaccessible to this user under the current no-root policy. LM Studio's backend works, but its graphical application is not installed or exposed by the current setup; Ollama remains unavailable.
8. An offline user installer and bundled wheel now work independently of the checkout. System GTK dependencies are still required. Global unfocused hotkey, voice input, and a general MCP-client runtime are not included.

## Finish live acceptance

Grant GNOME desktop consent and run the safe physical-screenshot/input checks. Identify or finish installing the graphical LM Studio application for its GUI test, and configure a second real provider for the live switch test. The local Qwen endpoint, exact response, project-image vision, and tool calling are already verified. Remaining prerequisites were not bypassed or marked passed.
