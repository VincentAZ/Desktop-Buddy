Inspect and operate an existing Docker installation with explicit command approvals.

Start with system.inspect to verify Docker exists. Use shell.run with argv arrays.
Read-only examples: `docker version`, `docker ps --format '{{json .}}'`, `docker stats --no-stream`.
Docker access can be equivalent to root on the host. Ask for specific authorization before starting containers, changing mounts, publishing ports, installing images, or altering daemon configuration.
Do not modify the user's existing Hermes containers or configuration without an explicit request.
Never use privileged containers or host-root mounts as a workaround for desktop permission failures.
After an approved change inspect container state, health and bounded logs. Do not report success solely from a container ID.
