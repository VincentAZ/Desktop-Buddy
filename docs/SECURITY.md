# Security and user control

Desktop Buddy runs as your normal desktop account and refuses root startup. MCP OAuth temporarily opens a listener on 127.0.0.1 during user-initiated sign-in; it closes after connection setup. There is no permanent app server. Its browser uses an inherited pipe rather than a debug port. While the isolated browser is running, a private HTTP proxy on an ephemeral 127.0.0.1 port enforces MCP service routing before upstream connections. TLS tunnels remain encrypted with Chrome's ordinary certificate validation. Closing the browser also closes the proxy and its connections; system proxy settings remain unchanged. Test servers bind only 127.0.0.1 and are closed after tests.

## Approval modes

**Discuss only** blocks every change. **Ask before acting** performs ordinary work-folder file operations on its own and asks before anything else: commands, keyboard/mouse input, deletion, outside-folder access, connected-app operations, memory changes and sending data to an online assistant. **Just do it** approves all of those without a prompt. The person confirms Just do it once in a plain-language dialog (or chooses **Allow and stop asking** in an approval dialog); the setting is remembered, visible beside Send, and reversible at any time.

No mode overrides Keep Buddy offline, protected credential/app-state paths, argument validation, cancellation, or OS permissions. External content cannot change modes or approve actions. Stop and Stop everything remain available. The table below describes Ask before acting; Just do it skips the "approval" column only.

## Host policy

The model cannot approve an action, change modes, grant itself a tool, modify provider settings or turn off privacy controls. A small registry validates arguments; the host checks policy before every effect. Tool outputs, files, pages, memories and skills are explicitly untrusted. Prompting alone is not a complete prompt-injection defense: the authorization boundary is implemented outside the model.

| Operation | Default behavior |
|---|---|
| Read ordinary workspace files / list / inspect system / AT-SPI | Allowed in every mode |
| Desktop screenshot | OS consent; local observation or explicit cloud task disclosure |
| Write/create/move workspace files | Not in Discuss only; atomic write/readback, no move overwrite |
| Delete one regular file | Exact action approval |
| Read/write outside workspace | Exact action approval |
| General host shell/script | Exact argv approval with a plain-language purpose; bounded time/output |
| Desktop keyboard/pointer/accessibility action | Action approval + GNOME screen-sharing grant (remembered by GNOME) |
| Browser navigation/action, HTTP request, app launch | Action approval |
| MCP tool invocation | Exact server/tool/arguments approval; server annotations do not waive it |
| MCP discovery and resource reads | Network approval; offline mode blocks them |
| Run/cancel a local ComfyUI workflow | Ask before acting + one exact job approval |
| Clipboard read / persistent-memory change | Explicit sensitive-action approval |
| Credential paths / Buddy state through model filesystem tools | Denied |

Shell approval is powerful: a command executes as your user and can access that user's resources. Inspect complete scripts, not just their executable name. Privileged operations need approval of the precise command and the OS's own authorization; the app does not collect sudo passwords or configure root access. Commands run with a minimal environment: PATH, HOME, LANG and the desktop-session identity variables (display, session bus, XDG session) so that `pkexec` can show GNOME's authentication dialog. API keys and the rest of Buddy's environment are never passed. Commands may run up to one hour before the process group is killed; cancellation still kills them immediately. Docker operations deserve the same care because daemon access can be root-equivalent. No shell-command “safe regex” grants broad execution automatically.

Mode and privacy selections belong to the human UI. Changing privacy cancels an active task. Model and ordinary mode changes apply to the next task; the approval dialog's Allow and stop asking switches the active task to Just do it. Each task snapshots its model and policy. Only one process can use a given state profile at a time.

## Local-only mode

- Blocks cloud provider calls, including connection tests.
- Blocks all MCP operations and sign-in, including local stdio servers that may access the network. Enabling offline closes existing MCP sessions.
- Restricts local-model endpoints to numeric loopback, disables proxy/redirect routes, and rejects known Ollama remote/cloud model metadata.
- Disables HTTP, browser networking, application launch, clipboard writes, pointer/keyboard input, window control and accessibility actions. Those actions can otherwise transmit data through another application's connection. Approvals cannot override this restriction; the user must explicitly turn off local-only mode.
- Runs approved shell commands under bubblewrap with a separate network/PID/IPC namespace, hidden home/session/socket directories, read-only system files, and only the selected workspace writable. No unisolated fallback is permitted.
- Keeps workspace files, local system inspection, screenshots, accessibility observations and trusted local-model calls available. ComfyUI status and approved cancellation remain available, but starting workflows is blocked because custom nodes can use their own network connections.

A local service can itself proxy requests or provide remote models. Local-only is enforceable for Buddy's own network/tool paths; it cannot prove a user-configured inference server is honest. Use a known local model/service. This is not a security boundary against another malicious process running as the same OS user.

## Secrets and state

MCP configuration belongs to the human connection UI; the model cannot add a server or change its credentials. Endpoint/command metadata is saved in SQLite, while bearer tokens, secret environment/headers, OAuth tokens, client credentials and discovery context are stored in GNOME Keyring. Local stdio servers run as the user and are trusted software, not workspace-sandboxed tools; they receive a minimal environment plus explicitly configured values. The SDK owns transport lifecycle and process cleanup. A server may separately manage downstream login files. See [MCP setup and persistence](MCP.md).

GNOME Keyring stores API keys through libsecret. If keyring storage fails, the operation fails; no plaintext fallback exists. Inference workers receive keys over anonymous pipes, not process arguments/environment. Logs and model-visible tool data redact registered keys, common key/token patterns and structured sensitive fields. Detection cannot identify every possible unknown secret, and screenshots may contain visible secrets: review cloud disclosure and avoid displaying sensitive content during a shared-screen task.

Images selected through the composer are explicit user inputs. Buddy rejects links and non-regular files, caps source size and decoded pixels, converts them into bounded private PNG copies, and deletes unsent copies when removed, when changing conversations, or on clean shutdown. Up to eight may be attached to one request. A cloud task's disclosure covers these image bytes.

Document attachments share the eight-file limit. Selected `.xlsx`, `.docx`, PDF, CSV/TSV and UTF-8 text files are copied into private storage, capped at 25 MB each, and hashed. Only user-selected files in the active conversation are exposed to attachment tools. Originals are not modified. Readers run in a separate process with CPU, memory, output and archive-expansion bounds; these are resource limits, not a general sandbox. Spreadsheet formulas are reported without execution; cached values are explicitly potentially stale. Text extraction does not claim full document layout or OCR. Making an editable copy refuses overwrite and applies normal destination authorization. Sent snapshots survive restart and are removed on conversation deletion only when no remaining conversation references them. Unsent copies are removed when discarded or on clean shutdown. File names and contents remain untrusted data; online-task disclosure covers the file information read for the task.

`.buddy/` is mode 0700; the database, memory and images use private files. SQLite stores redacted conversations, tool arguments/results, usage, task status and checkpoints. Confirmed conversation deletion uses SQLite secure deletion and truncates the active WAL after atomically removing its messages, tasks and audit records. It then removes image artifacts under Buddy's private image/screenshot directories when no other conversation references them. It never removes workspace files or shared images and cannot erase copies in filesystem snapshots or external backups. Memory is a separate readable `memory.json`, editable/deletable in the Memory dialog. App state and obvious credential paths are excluded from filesystem tools. Approved unrestricted shell commands can exceed those filesystem-tool protections.

## Stop and recovery

Stop sets a cancellation latch, denies pending approvals, kills tracked process groups and the active inference subprocess, and prevents later tool calls. Emergency Stop additionally closes the portal session, MCP sessions and Buddy's isolated browser. MCP teardown is asynchronous and bounded by SDK transport timeouts; local server shutdown may take a grace period before process termination. Button and app-focused Ctrl+Shift+Escape remain available during tasks/approval dialogs. A new explicit task resets the latch; desktop control needs a fresh grant.

Completed effects cannot be undone. An already-dispatched OS action may finish. A deliberately detached daemon launched through an approved unrestricted command may escape process-group tracking; do not use shell tools to create unmanaged services. Cancellation does not promise rollback or a refund from a remote provider.

A checkpoint is written before and after each effect, and tool operations are audited. On restart running tasks become interrupted. Missing tool results become explicit “outcome unknown” observations; no pending effect is automatically replayed. Resume asks the model to inspect actual state first. External side effects are not exactly-once transactions.

Failed source observations pause subsequent content changes before dispatch. This covers Buddy-registered read-only tools (including searches and MCP discovery), browser navigation, HTTP GET and opaque MCP calls. Recovery operations remain subject to normal approval. The exact failed read must succeed to clear its pause; an unrelated successful search cannot clear it. Continue task restores this state from its checkpoint. A new user request can supply replacement information or change scope. While a source remains unread, streamed model text is suppressed and the final response uses a host-generated explanation instead of an unsupported completion claim.

This is a failure-containment check, not factual validation. A successful tool result can still be misleading, a model can misinterpret it, and shell commands are not classified as source reads. Alternate-source recovery can require a new user request. Failed shell exit codes, HTTP error statuses, MCP error results, unsuccessful file/text verification and explicit app/input rejections prevent a clean completion status. Outstanding failures suppress streamed completion claims and replace the final model report with a host-generated explanation. A sent-but-unverified event is distinct from a rejected event; downstream application success still requires observation and is not universally validated by this check. Exact retries of opaque MCP calls still need ordinary authorization and may repeat external effects; no exactly-once guarantee is implied.

## Desktop permissions

GNOME's screen/input portal handles user consent. No input-group membership, /dev/uinput permission, GNOME extension, insecure compositor flag or system setting was changed. A locked session blocks capture/input. Unlock through the ordinary desktop login and focus Buddy before requesting access. AT-SPI availability depends on application accessibility support. Exact window focus/geometry and Unicode injection vary by application/compositor and remain subject to verification.

## Readable command review

The agent must supply a bounded plain-language purpose before requesting a shell command. The UI labels this as Buddy’s proposal, not an independent verification. It shows the actual starting folder, enforced time limit and networking scope; the exact arguments remain under Technical details. The explanation is bound to the current task token and the exact pending tool/arguments. Missing or mismatched command explanations cannot be approved in the UI. No explanation or claimed consent bypasses host authorization; the review context is cleared after the tool returns or fails.


## Desktop input

Two paths exist, both behind the same approval and the same GNOME screen-sharing grant.

**Verified control path.** When an app exposes its controls through accessibility, `desktop.set_text` and `desktop.accessible_action` act on a named control and verify readback, and `desktop.input` can pin an observed control and window before approval: Buddy restores that exact object after the dialog closes, checks focus and geometry, refuses stale targets or wrong windows, and stops typing when focus is lost. Pointer targets use AT-SPI global coordinates; Buddy subtracts the shared monitor origin and rejects points outside it.

**Screenshot path.** For apps that expose nothing useful (custom canvases, games, some Electron or Java apps), the model takes a `desktop.screenshot` and asks `desktop.input` to click, drag or scroll at pixel positions in that image, or to type and press keys. Buddy converts the pixel position to the shared monitor's logical coordinates (clamped to its edge), refuses positions outside the image or when no screenshot exists, and connects screen sharing itself when GNOME has remembered the grant. This path verifies nothing about which control received the event; the model is instructed to take a new screenshot afterwards. It is the universal fallback, so the normal approval (or Just do it) covers the risk that the model misreads the screen.

Both paths send events through the GNOME RemoteDesktop portal only. Buddy never installs input helpers, changes `/dev/uinput` permissions, or adds compositor flags. GNOME remembers the grant only when Buddy runs in its own systemd app scope, which the launcher arranges automatically.

## Visual evidence and history limits

Text-history compaction retains the distinct images observed during the current task, including their dimensions. Tool discovery and text-history cuts cannot silently replace them with a digest of their file paths. Byte-identical image captures can share one transmitted copy; the complete local transcript retains all captures. Images from an earlier task can retire when newer task images are available. If the retained evidence cannot fit, inference stops and progress remains saved instead of asking the model to act on an omitted source. This repairs an observed source-loss path; it does not independently validate every fact a model reads from an image. Real GNOME capture and arbitrary-app compatibility remain separate acceptance requirements.

## Paged reads and oversized results

The file reader returns bounded UTF-8 ranges with byte offsets and a revision derived from file identity, size and modification/change timestamps. It detects a change during a page read and between subsequent pages, including when the model omits the prior revision. Starting from byte zero without an explicit expected revision begins a new read. The in-memory revision cache is bounded to 256 files and is not an immutable filesystem snapshot. Model-facing reads default to 8000 bytes; internal whole-workflow reads retain the existing 100 kB ceiling.

Initial tool results larger than 64 kB of encoded JSON are withheld as explicit `ResultTooLarge` failures. For registered tool executions, the original result remains in the normal tool audit; the assistant receives an explanation rather than incomplete JSON. Content changes stay blocked after an unread source result until a successful reread. A completed effect is not undone by a large returned result, so the error explicitly requires checking before repeating actions. Invalid argument requests did not perform their intended operation; correcting them clears their separate usage error without clearing actual source failures. Text-history compaction remains a separate, bounded summarization mechanism and is not proof of semantic retention of every earlier fact.
