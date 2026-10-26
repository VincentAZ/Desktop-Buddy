# Security and user control

Desktop Buddy runs as your normal desktop account and refuses root startup. It opens no network listener. Its browser uses an inherited pipe rather than a debug port. Test servers bind only 127.0.0.1 and are closed after tests.

## Approval modes

**Just discuss** blocks mutations. **Help me do it** asks before sensitive operations. **YOLO — do anything** is an explicit user grant for actions within the requested task, including shell commands, deletion, outside-folder access, app/desktop control and cloud transmission. It bypasses Buddy approval dialogs and persists until changed. The approval dialog can enable it for the current and future tasks without cancelling the waiting action. Changes through the ordinary mode selector still stop an active task.

YOLO does not override the independent offline setting, protected credential/app-state paths, tool validation, cancellation, or OS permissions. Stop everything remains available. External content cannot enable YOLO. The host-policy table below describes the default Help me do it mode; YOLO satisfies its approval requirements automatically.

## Host policy

The model cannot approve an action, change modes, grant itself a tool, modify provider settings or turn off privacy controls. A small registry validates arguments; the host checks policy before every effect. Tool outputs, files, pages, memories and skills are explicitly untrusted. Prompting alone is not a complete prompt-injection defense: the authorization boundary is implemented outside the model.

| Operation | Default behavior |
|---|---|
| Read ordinary workspace files / list / inspect system / AT-SPI | Allowed in Planning and Execution |
| Desktop screenshot | OS consent; local observation or explicit cloud task disclosure |
| Write/create/move workspace files | Execution only; atomic write/readback, no move overwrite |
| Delete one regular file | Execution + exact action approval |
| Read/write outside workspace | Exact action approval; writes require Execution |
| General host shell/script | Execution + exact argv approval; bounded time/output |
| Desktop keyboard/pointer/accessibility action | Execution + action approval + OS grant where applicable |
| Browser navigation/action, HTTP request, app launch | Execution + action approval |
| Run/cancel a local ComfyUI workflow | Execution + one exact job approval |
| Clipboard read / persistent-memory change | Explicit sensitive-action approval |
| Credential paths / Buddy state through model filesystem tools | Denied |

Shell approval is powerful: a command executes as your user and can access that user's resources. Inspect complete scripts, not just their executable name. Privileged operations need approval of the precise command and the OS's own authorization; the app does not collect sudo passwords or configure root access. Commands run with a minimal environment: PATH, HOME, LANG and the desktop-session identity variables (display, session bus, XDG session) so that `pkexec` can show GNOME's authentication dialog. API keys and the rest of Buddy's environment are never passed. Commands may run up to one hour before the process group is killed; cancellation still kills them immediately. Docker operations deserve the same care because daemon access can be root-equivalent. No shell-command “safe regex” grants broad execution automatically.

Mode and privacy selections belong to the human UI. Changing them cancels an active task. Each task snapshots the selected model and policy. Only one process can use a given state profile at a time.

## Local-only mode

- Blocks cloud provider calls, including connection tests.
- Restricts local-model endpoints to numeric loopback, disables proxy/redirect routes, and rejects known Ollama remote/cloud model metadata.
- Disables Buddy's arbitrary HTTP and browser networking. Approved application launches, clipboard writes, and GUI/accessibility actions remain available so a local model can operate the desktop. The approval dialog warns that a target application may use its own network connection; Buddy cannot sandbox an already-running third-party GUI application.
- Runs approved shell commands under bubblewrap with a separate network/PID/IPC namespace, hidden home/session/socket directories, read-only system files, and only the selected workspace writable. No unisolated fallback is permitted.
- Keeps local reads, filesystem edits in Execution, screenshots, accessibility observations, local-model calls, and the numeric-loopback ComfyUI integration available. Each ComfyUI run still needs approval. Custom nodes remain part of the trusted local ComfyUI service and may have their own network behavior.

A local service can itself proxy requests or provide remote models. Local-only is enforceable for Buddy's own network/tool paths; it cannot prove a user-configured inference server is honest. Use a known local model/service. This is not a security boundary against another malicious process running as the same OS user.

## Secrets and state

GNOME Keyring stores API keys through libsecret. If keyring storage fails, the operation fails; no plaintext fallback exists. Inference workers receive keys over anonymous pipes, not process arguments/environment. Logs and model-visible tool data redact registered keys, common key/token patterns and structured sensitive fields. Detection cannot identify every possible unknown secret, and screenshots may contain visible secrets: review cloud disclosure and avoid displaying sensitive content during a shared-screen task.

Images selected through the composer are explicit user inputs. Buddy rejects links and non-regular files, caps source size and decoded pixels, converts them into bounded private PNG copies, and deletes unsent copies when removed, when changing conversations, or on clean shutdown. Up to eight may be attached to one request. A cloud task's disclosure covers these image bytes.

`.buddy/` is mode 0700; the database, memory and images use private files. SQLite stores redacted conversations, tool arguments/results, usage, task status and checkpoints. Confirmed conversation deletion uses SQLite secure deletion and truncates the active WAL after atomically removing its messages, tasks and audit records. It then removes image artifacts under Buddy's private image/screenshot directories when no other conversation references them. It never removes workspace files or shared images and cannot erase copies in filesystem snapshots or external backups. Memory is a separate readable `memory.json`, editable/deletable in the Memory dialog. App state and obvious credential paths are excluded from filesystem tools. Approved unrestricted shell commands can exceed those filesystem-tool protections.

## Stop and recovery

Stop sets a cancellation latch, denies pending approvals, kills tracked process groups and the active inference subprocess, and prevents later tool calls. Emergency Stop additionally closes the portal session and Buddy's isolated browser. Button and app-focused Ctrl+Shift+Escape remain available during tasks/approval dialogs. A new explicit task resets the latch; desktop control needs a fresh grant.

Completed effects cannot be undone. An already-dispatched OS action may finish. A deliberately detached daemon launched through an approved unrestricted command may escape process-group tracking; do not use shell tools to create unmanaged services. Cancellation does not promise rollback or a refund from a remote provider.

A checkpoint is written before and after each effect, and tool operations are audited. On restart running tasks become interrupted. Missing tool results become explicit “outcome unknown” observations; no pending effect is automatically replayed. Resume asks the model to inspect actual state first. External side effects are not exactly-once transactions.

## Desktop permissions

GNOME's screen/input portal handles user consent. No input-group membership, /dev/uinput permission, GNOME extension, insecure compositor flag or system setting was changed. A locked session blocks capture/input. Unlock through the ordinary desktop login and focus Buddy before requesting access. AT-SPI availability depends on application accessibility support. Exact window focus/geometry and Unicode injection vary by application/compositor and remain subject to verification.
