# Desktop Buddy 0.4.5 — what works, what was verified, what is left

October 27, 2026. This replaces the running log that used to live here; the older evidence directories under `.test-data/` are untouched.

## The product in one paragraph

Desktop Buddy is a local GTK app with no Buddy account. Whoever sets up the computer picks an AI service once (OpenAI, Anthropic, xAI, Moonshot, OpenRouter, or LM Studio/Ollama on the machine) under Settings → IT setup. Everyone else types what they want. Buddy works with files, runs commands, opens apps, clicks and types on the real screen, browses in its own Chrome window (visible or in the background), and uses connected apps such as Notion, Stripe, Vercel or Gmail. Three choices beside Send: Discuss only, Ask before acting, Just do it.

## What changed in 0.4.0–0.4.5

| Requirement | Before | Now |
|---|---|---|
| Connect MCP servers in one click | 6 services enabled; Vercel, Figma, GitLab, Google and others showed "Not available yet" | 26 services self-register and open sign-in on one click; 5 public services connect instantly; GitHub takes a pasted token; any server address or local command can be pasted and Buddy detects whether it needs sign-in |
| Gmail / Calendar / Google | Blocked behind a JSON "registration bundle" import; Google's MCP servers refuse every call until a project is enrolled in Google's preview programme | Google (and Slack, Asana, HubSpot, Zendesk, Box, Figma) need a publisher-registered client because those providers refuse self-registration. The client ships in `desktop_buddy/publisher.py` or is pasted once under Settings → IT setup → App sign-in keys (numbered steps, link to the provider page, two boxes). After that they show a normal Connect button. Gmail and Calendar are served by Buddy's own tools on Google's regular APIs (0.4.5), so they work for any signed-in account with no preview enrolment; a real mailbox search was verified live |
| Native control of Linux apps | Input only through accessibility-pinned controls; apps without accessibility "unsupported"; remembered GNOME grant appeared broken | Two paths: verified accessibility control when available, otherwise click/drag/type at positions read from a screenshot. Screen sharing connects itself when GNOME remembered the grant (it does, see below) |
| Browser or headless | Visible only; browser refused for every catalog website even when nothing was connected | Settings → "Show the browser window while Buddy browses" toggles background browsing. Browser is refused only for websites of apps that are actually connected |
| YOLO as an option | "Fewer routine prompts" skipped only app-launch and window prompts | "Just do it" approves everything without prompts, after one plain-language confirmation. Stop, Discuss only, Keep Buddy offline and credential-path protection are unchanged |
| Assistant understands Buddy | Help text described the old gates | Help topics, status report and system guidance rewritten for the new behaviour |

The version is shown under "Desktop Buddy" at the top of the main window and at the bottom of Settings; older windows must be closed and reopened to run this code.

## Verified on this workstation (October 27, 2026)

- **Unit and protocol tests:** 313 pass (`./scripts/run-tests.sh`), including new tests for catalog sign-in kinds, shipped and pasted publisher clients, the server-address probe, browser routing for connected apps only, Just do it, and screenshot-to-screen coordinate conversion.
- **Native UI fixtures:** all 17 scripts under `scripts/test-*-ui.py`, `test-accessibility-control.py`, `test-pointer-target-ui.py` and `test-physical-workflow.py` pass on a private X display (`xvfb-run`), including the updated Connect apps, Just do it and first-use flows.
- **One-click sign-in, live:** for all 26 self-registering services the real connect path registered Buddy with the provider, stored the issued client, and produced the authorization URL with PKCE S256 and state (browser deliberately not opened). Figma was found to refuse self-registration (HTTP 403) and was moved to the publisher-client group.
- **Public servers, live:** Microsoft Learn, DeepWiki, Context7 and Hugging Face connected through the real manager and answered a tool call each.
- **GNOME desktop control, live:** inside the app's own systemd scope the remembered grant restored silently (no dialog), a real 5120×1440 screenshot was captured, and a pointer click computed from screenshot pixels landed in a dedicated test window (the window became active). The earlier "RemoteDesktop.Start timed out" results came from probes run outside the app scope, where GNOME cannot match the remembered grant; the app itself always runs in its scope.
- **Physical keyboard:** the earlier consented check (`.test-data/portal-desktop/run-lcaj_4qb`) typed and read back a phrase through the same portal call the new path uses. A fresh live typing readback was not completed in this pass because GNOME reported the screen saver active at the time; the pointer path above was.

## What needs a person

- **Google sign-in key.** Create a Desktop-app OAuth client in Google Cloud Console (steps are shown in the app under IT setup → App sign-in keys) and either paste it there or put it in `desktop_buddy/publisher_local.py` (gitignored) before building a release. Until then the nine Google cards say "needs a one-time setup". The same applies to Slack, Asana, HubSpot, Zendesk, Box and Figma if those are wanted.
- **Completing a sign-in.** Every OAuth handoff ends in the person's browser with their own account; that step cannot be automated and was not.
- **First screen-sharing grant on a new computer.** GNOME shows its own dialog once; ticking "remember" makes it silent afterwards.

## Known limits

- Screenshot-based control sends events where the model says; it verifies nothing about the target control. The model is told to re-screenshot after acting. Just do it removes the human check on that too.
- The browser proxy enforces routing for Buddy's own browser only; shell commands and other apps are outside it.
- Google's official MCP endpoints are a developer preview. The shipped client's project (`desktop-buddy-app`) has the MCP APIs enabled; Google still requires enrolment in the Workspace Developer Preview Program before tool calls work, and its terms limit preview features to the enrolled organisation's own users until general availability.
- Live tests here used this workstation's GNOME 50 / Ubuntu 26.04 session; other compositors were not tested.

## Repeat validation

```bash
./scripts/run-tests.sh                                                   # 313 unit/protocol tests
GDK_BACKEND=x11 xvfb-run -a .venv/bin/python scripts/test-mcp-ui.py       # any of the native fixtures, off-screen
.venv/bin/python scripts/verify.py --desktop                              # everything, with evidence under .test-data/verification
```
