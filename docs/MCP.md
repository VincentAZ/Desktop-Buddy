# MCP connections

Desktop Buddy is an MCP client built on the official Python SDK (pinned to 2.3.0). It connects to servers the person chooses, discovers their tools, invokes them through Buddy's approval policy and reads their resources. HTTP (streamable) and stdio transports are supported. Legacy SSE-only endpoints, sampling, elicitation, prompts and subscriptions are not exposed.

## What the person does

Click **Connect apps**, pick an app, click **Connect**. That is the whole flow for most services:

| How it connects | Services (checked live on 2026-10-27) | What the person sees |
|---|---|---|
| Self-registering sign-in (OAuth dynamic client registration, PKCE S256, state) | Notion, Linear, Atlassian (Jira/Confluence), monday.com, ClickUp, Todoist, Airtable, Dropbox, Canva, Webflow, Wix, Intercom, Zapier, Stripe, PayPal, Square, Vercel, Netlify, GitLab, Supabase, Neon, Prisma, Sentry, Cloudflare (account resources), Cloudflare (logs and analytics), Cloudinary | Browser opens the service's sign-in page; approve; back to Buddy |
| Public, no account | Microsoft Learn, DeepWiki, Context7, Exa web search, Hugging Face | Connected immediately |
| Paste a token | GitHub | A small window with a button that opens GitHub's token page and a box to paste into |
| Publisher-registered client | Google (Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, Contacts, Classroom), Slack, Asana, HubSpot, Zendesk, Box, Figma | "Needs a one-time setup by the Desktop Buddy team" until the key exists, then a normal Connect button |
| Local program | Docker MCP Gateway, Google Workspace community connector | Connect appears once the program is installed on the computer |
| Anything else | Paste a server address (or a local command) into the box under **Add an app** | Buddy probes the address, asks for sign-in only if the server demands it |

Every self-registering service above completed the real handoff during verification: Buddy registered itself with the provider, received a client ID, and produced the authorization URL with PKCE and state. The browser step itself needs a person with an account, so it is not automated. Microsoft Learn, DeepWiki, Context7 and Hugging Face were connected end to end and answered tool calls.

**Your apps** lists saved connections. **Turn off** keeps the sign-in but stops Buddy using the app; **Connect** turns it back on. **Remove** forgets the connection and its credentials in Buddy (revoking the grant on the provider's side is done in that provider's account settings).

## Gmail and Calendar are served by Buddy itself

Google's own MCP servers for Workspace are a developer preview that Google restricts to enrolled organisations and their own users. Mail and calendar are what people ask for most, so Buddy serves those two itself on Google's generally available Gmail and Calendar APIs (`desktop_buddy/google_native.py`). The person still clicks Connect on the Gmail or Calendar card and signs in once with the shipped client; the connection record, token storage and refresh are the normal OAuth path. The assistant then sees a fixed set of tools (`search_threads`, `get_thread`, `get_message`, `list_labels`, `create_draft`, `send_message`, `modify_labels`, `trash_thread`; `list_calendars`, `list_events`, `get_event`, `create_event`, `update_event`, `delete_event`, `respond_to_event`) with JSON schemas, approval and redaction exactly as for any MCP tool. Message text is handed over as plain text (HTML stripped, 20 000 characters per message), attachments by name. Drive, Docs, Sheets, Slides, Chat, Contacts and Classroom still go to Google's preview MCP servers.

## Publisher clients (Google and friends)

Google, Slack, Asana, HubSpot, Zendesk, Box and Figma do not let an app register itself. Someone registers Desktop Buddy with the provider once and the resulting client ID (and secret, for "installed app" clients) is shipped with the product. Two ways:

1. **Ship it.** Fill in `desktop_buddy/publisher_local.py` (gitignored; same shape as `CLIENTS` in `publisher.py`) before building a release. Every installation then shows Connect for those services and nobody ever sees a client ID. The `google` key covers all nine Google services.
2. **Paste it on one installation.** Settings → IT setup → **Enter or check app sign-in keys…** shows numbered steps per provider, a button that opens the provider's developer page, and two boxes. The key goes into GNOME Keyring; only nonsecret metadata (endpoint, scope, callback port, a random revision) is kept in SQLite. **Forget this key** removes it.

For Google specifically: create an OAuth client of type **Desktop app** in Google Cloud Console, enable the Gmail/Calendar/Drive/Docs/Sheets/Slides APIs **and** their separate MCP APIs (`gmailmcp.googleapis.com`, `calendarmcp.googleapis.com`, …; without these Google answers `tools/list` with HTTP 403), enrol the project in the [Google Workspace Developer Preview Program](https://developers.google.com/workspace/preview) (without it every tool call returns an error text asking for enrolment), and either add your users as test users or publish the consent screen. Google's loopback redirect `http://127.0.0.1:8765/oauth/callback` is accepted automatically for desktop clients. Buddy requests the scopes listed in `GOOGLE_SCOPES` (for example `gmail.modify`, `calendar`, `drive`) and adds `access_type=offline` and `prompt=consent` so Google issues a refresh token. Google's official MCP endpoints are a developer preview. Google's programme terms say pre-GA features may not be made available to end users outside the enrolled organisation's own domain, so the shipped Google client is fit for in-house use and testing until Google makes the MCP servers generally available; a product sold to the public needs either that GA or Buddy's own Gmail/Calendar tools on the regular APIs.

A registration file (`{"version":1,"registrations":[...]}`) can still be imported under IT setup for scripted deployments; the format is unchanged from earlier versions.

## What survives a restart

Connection name, transport, endpoint or command, enabled flag, service domains and nonsecret OAuth preferences live in the profile's SQLite database. Bearer tokens, custom headers, environment secrets, OAuth client credentials, access and refresh tokens and discovery context live in GNOME Keyring. There is no plaintext secret fallback. Changing endpoint, command, auth type, callback port or scopes creates a new credential binding and clears the previous one.

Buddy creates a fresh protocol session on demand after restart, reuses saved credentials and refreshes expired OAuth tokens where the provider supports refresh. Revoked grants, an unavailable keyring, expired refresh tokens or new permissions may require **Connect** again. Buddy never opens a consent browser in the middle of a task; sign-in always starts from the Connect apps screen.

Stdio servers may manage their own downstream login. They run as the user with a minimal environment plus the configured secret environment; the SDK owns process cleanup.

## Routing: connected app first, browser otherwise

The assistant is instructed to use a connected app before the browser. Buddy's isolated browser runs behind a private loopback proxy; when a website belongs to a saved, enabled connection (its service domains, the catalog's domains for that app, or the endpoint's own origin), browser requests to it are refused before any upstream socket opens, including redirects, pop-ups, fetches and web workers. A site with no saved connection is open to the browser: an unconnected catalog entry is not a reason to refuse the only route that works. Turning a connection off releases its website again.

The proxy does not decrypt TLS, replace certificates or touch system proxy settings; it starts and stops with the isolated browser. Shell commands and other desktop applications are outside this boundary.

## Tools the assistant sees

`mcp.servers` (saved connections and the catalog), `mcp.tools` (paged, filterable discovery), `mcp.call` (argument validation against the advertised JSON schema, no external schema references), `mcp.resources` and `mcp.read_resource`. All MCP calls are treated as able to change external state: Discuss only cannot invoke them, Ask before acting asks for the exact operation, Just do it runs them, Keep Buddy offline blocks them entirely. Server text, descriptions, schemas and results are untrusted data.

## Validation

`./scripts/run-tests.sh` covers real stdio and HTTP peers, pagination, schema validation, persistent settings and credentials, endpoint binding, environment isolation, redaction, resources, cancellation, disabled connections, approval and offline boundaries, routing, OAuth PKCE, refresh after restart, publisher and pasted clients, and the address probe. `scripts/test-mcp-ui.py` drives the real GTK screens against a local OAuth peer. Live evidence for the catalog (handoffs and public tool calls) is summarised in [DELIVERY.md](DELIVERY.md).
