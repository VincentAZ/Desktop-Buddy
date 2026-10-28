Run and verify a user-supplied ComfyUI API workflow using the existing local server.

Use `comfyui.status` first; it connects only to the configured numeric-loopback service and reports version, devices and queue counts.
Read or create the exported API-format workflow JSON in the workspace. Confirm required nodes and models exist; do not install custom nodes automatically.
Call `comfyui.run` with the workflow path, a workspace output directory, and optional overrides keyed by node ID. One host approval covers that exact workflow path, override set and output destination.
The tool supplies a unique job ID, waits with bounded polling, verifies history completion, and copies image outputs into the workspace with non-overwriting names and SHA-256 hashes. A queue response alone is not success.
If the task is stopped or times out, the tool attempts to cancel its specific ComfyUI job. Use `comfyui.cancel` for a known in-flight job; it also requires approval.
For a list of up to 50 prompt strings, use `comfyui.run_batch` with the text node ID and input name. One approval covers the visible prompt list. It runs sequentially to limit GPU pressure and returns each completed prompt ID, output hash, and failure without resubmitting successful items in the same run.
Checkpoint completed prompt IDs after the batch returns. Never resubmit an unknown/in-flight prompt on resume; inspect the activity log and ComfyUI history first.
Create a summary of successes, failures and verified outputs. Local-only mode permits status and cancellation, but blocks new workflows: custom nodes may use their own network connections. The user must explicitly turn off local-only mode and approve the exact workflow before execution. Never change privacy settings on the user's behalf.
