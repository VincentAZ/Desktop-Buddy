# Installation and troubleshooting

## This workstation

Run `./launch.sh` from the project. It uses `/usr/bin/python3` and existing distribution libraries. No packages were installed during implementation. No Hermes installation, Docker configuration, unrelated repository, personal settings or personal files were modified.

For a standalone installation and GNOME launcher:

```bash
python3 scripts/install.py
```

This builds an offline wheel with the installed setuptools/pip, creates `~/.local/share/desktop-buddy/venv` with access to system GTK libraries, and creates `~/.local/share/applications/io.desktopbuddy.App.desktop`. XDG_DATA_HOME is respected. It does not download dependencies, install services, edit shell profiles, or copy credentials. Existing installations/launchers are not overwritten. `--prefix` and `--desktop-dir` allow alternative or isolated test locations. The installer has been exercised inside `.test-data`, including a path containing spaces; it has not installed into your personal application directory. Do not use sudo.

Source-checkout state defaults to `.buddy/`, with `workspace/` as its working folder. An installed package uses `$XDG_DATA_HOME/desktop-buddy` (normally `~/.local/share/desktop-buddy`) for state and its `workspace/` subdirectory for work. Use `--data-dir` to reuse a source profile after closing the source app. State is never automatically moved. Back up state while the app is closed. Remove keys through Providers & settings → Delete saved key. Edit or remove memory entries through Memory → Save changes. Conversation deletion is under the top-right ⋮ menu; its confirmation explains what is retained.

## Another Ubuntu/GNOME workstation

Check dependencies with `./launch.sh --doctor`. Distribution packages provide GI libraries, which pip alone will not supply. The following is an installation recipe for the user to review and authorize; it is not automatically executed:

```bash
sudo apt install python3 python3-pip python3-setuptools python3-venv python3-gi python3-requests python3-pil \
  gir1.2-gtk-4.0 gir1.2-atspi-2.0 gir1.2-secret-1 gir1.2-gstreamer-1.0 \
  gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  xdg-desktop-portal xdg-desktop-portal-gnome gnome-keyring bubblewrap
```

A maintained Chrome/Chromium installation is needed for the isolated browser. An existing local model service or your own API credential is needed for inference. Installing or downloading those applications/models requires your separate authorization. Package names may vary by distribution; this build has only been exercised on the discovered Ubuntu 26.04 GNOME system.

Skills are bundled inside the wheel. Installed launches have been tested outside the checkout, including all seven skill loads and a native-window smoke check. GTK/portal libraries remain system dependencies; this is not a self-contained binary distribution. No Node.js/Electron build is needed. The generic `DesktopBuddy.desktop` is a template for a `desktop-buddy` executable on PATH; the installer generates an entry with the actual executable path.

To update an existing installation from this checkout, close Buddy and run the following with your actual installation path. It updates the application package; the separate state directory and keyring remain:

```bash
python3 -m pip --python ~/.local/share/desktop-buddy/venv/bin/python \
  install --no-deps --no-build-isolation --no-index --upgrade .
```

## Troubleshooting

**“GNOME session is locked” / “Session creation inhibited”**: unlock normally, focus Buddy, then use the monitor menu → Connect desktop control. The session is now unlocked, but the latest physical probe timed out awaiting consent. Use the app's buttons so portal requests include the exported parent window; a background CLI may be refused because only a focused app can show the consent dialog. No compositor security changes are needed.

**Screenshot dialog denied / cancelled**: no fallback bypass is attempted. Reinvoke only when you intend to grant screen access. A shared stream disconnects at Emergency Stop. If no frame arrives, reconnect the portal and confirm the monitor selection. Input coordinates use the returned logical monitor size; images may be resized to 1920 pixels on their longest side.

**No accessible controls**: some Electron/browser/custom-canvas apps expose incomplete AT-SPI trees. Inspect a named application/window, increase depth (up to 24), or use an authorized screenshot and vision model. Deep traversals are bounded by node and time limits. Window titles can be available even when control text is not.

**Provider connection refused**: verify the existing local service is running and the base URL includes the right prefix. LM Studio defaults to `/v1`; native Ollama does not. No service was running during discovery. Buddy does not start/download one automatically.

**401 / 403 / 404 / 400**: check key, account/model access, endpoint/model ID, and model-specific tool/reasoning settings respectively. Models listed by discovery are not necessarily usable by your account. Streaming errors discard incomplete tool calls. Cloud provider calls are blocked while Local only is checked.

**Keyring error**: unlock the desktop/login keyring and retry. No insecure alternate file is used. A live key-write roundtrip was not performed during implementation to avoid changing the user's keyring; the service was available and the API contract is tested with mocks.

**Local-only shell fails**: bubblewrap/user namespaces must be permitted. No fallback is used. The discovered workstation passed a real network namespace test, including inability to reach a host loopback listener. Do not disable privacy to hide an isolation error; decide explicitly whether broader execution is appropriate.

**Task cannot execute**: select Execution and a model with tool support. Chat-only models are allowed in Planning. Sensitive tools still need an exact approval. A denial is a tool error; it is not permission to use another tool to bypass the restriction.

**Context budget exceeded**: raise the limit only if the model supports it, shorten the request, or start a new conversation. Compaction conservatively budgets text by UTF-8 bytes, retains complete tool exchanges and archives excerpts without inventing facts. Full redacted history is retained locally. Images consume a separate reserve; exact provider tokenization varies.

**Browser errors**: ensure Chrome/Chromium is installed. The browser's profile belongs to Buddy; personal tabs/cookies are intentionally unavailable. Stop closes only Buddy's process group. Browser navigation, selectors and page inspection are tested, but arbitrary sites can require additional waits or vision. No remote debug port is enabled.

**Stop**: wait for “Task cancelled” before submitting a new task. Inference sockets are killed through their worker process. Already-completed or externally detached operations cannot be reversed by Stop. Review the audit and actual state before resuming.

## Validation commands

```bash
./scripts/run-tests.sh                   # deterministic unit/protocol tests + real namespace test
./scripts/run-tests.sh --desktop         # native UI fixture + dedicated Chrome/Ptyxis workflow
./launch.sh --smoke-test --data-dir .test-data/smoke
python3 scripts/check-desktop.py          # accessible application/window inventory
python3 scripts/test-portal-desktop.py --control  # real GNOME consent + isolated physical input/readback
```

For a physical screenshot/input session use the application after unlocking. `scripts/check-desktop.py --connect --observe` is an optional manual probe; GNOME may refuse consent from a CLI without a focused parent. It never injects input. Do not label such a failure a successful desktop test.
