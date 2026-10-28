"""Curated MCP services and what each one needs before Connect works.

Every endpoint and its sign-in behaviour was checked live on 2026-10-27 against
the provider's own OAuth metadata. ``sign_in`` records what Connect needs:

* ``dcr``     the service registers Buddy automatically (one click, then sign in)
* ``none``    public service, no account needed
* ``token``   the person pastes a token from the service's own settings page
* ``client``  the service only accepts a client registered by the Desktop Buddy
              team (Google, Slack, ...). Connect appears once that client exists.
* ``install`` a local program that must be installed by IT first

Catalog entries never install packages or authorize an account on their own.
"""

import ipaddress
import shutil
from urllib.parse import urlsplit

GOOGLE_MCP_ENDPOINTS = {
    'gmail': 'https://gmailmcp.googleapis.com/mcp/v1',
    'drive': 'https://drivemcp.googleapis.com/mcp/v1',
    'docs': 'https://docsmcp.googleapis.com/mcp/v1',
    'sheets': 'https://sheetsmcp.googleapis.com/mcp/v1',
    'slides': 'https://slidesmcp.googleapis.com/mcp/v1',
    'calendar': 'https://calendarmcp.googleapis.com/mcp/v1',
    'chat': 'https://chatmcp.googleapis.com/mcp/v1',
    'contacts': 'https://people.googleapis.com/mcp/v1',
    'classroom': 'https://classroommcp.googleapis.com/mcp/v1',
}

# Scopes requested when the product's Google client signs a person in. These are
# the permissions Google's own MCP servers advertise; they cover reading and
# ordinary day-to-day changes without administrative powers.
GOOGLE_SCOPES = {
    'gmail': 'https://www.googleapis.com/auth/gmail.modify',
    'calendar': 'https://www.googleapis.com/auth/calendar',
    'drive': 'https://www.googleapis.com/auth/drive',
    'docs': 'https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/drive.readonly',
    'sheets': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.readonly',
    'slides': 'https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.readonly',
    'chat': 'https://www.googleapis.com/auth/chat.spaces https://www.googleapis.com/auth/chat.messages https://www.googleapis.com/auth/chat.memberships',
    'contacts': 'https://www.googleapis.com/auth/contacts.readonly',
    'classroom': 'https://www.googleapis.com/auth/classroom.courses https://www.googleapis.com/auth/classroom.coursework.me https://www.googleapis.com/auth/classroom.announcements https://www.googleapis.com/auth/classroom.rosters.readonly',
}


def entry(id, name, description, docs, *, url='', command=(), auth='oauth', sign_in='dcr', domains=(),
          publisher='Official', note='', legacy_urls=(), token_url='', scope='', group='Work'):
    if command and sign_in == 'dcr':sign_in = 'install'
    elif auth == 'none' and sign_in == 'dcr':sign_in = 'none'
    elif auth == 'bearer' and sign_in == 'dcr':sign_in = 'token'
    return dict(id=id, name=name, description=description, docs=docs, url=url,
                command=list(command), auth=auth, sign_in=sign_in, domains=list(domains), publisher=publisher,
                note=note, legacy_urls=list(legacy_urls), token_url=token_url, scope=scope, group=group)


CATALOG = [
    *[entry('google-'+service, 'Google '+name, description,
            'https://developers.google.com/workspace/guides/configure-mcp-servers',
            url=GOOGLE_MCP_ENDPOINTS[service], domains=domains, sign_in='client', scope=GOOGLE_SCOPES[service],
            group='Google',
            note='Google only accepts sign-in through a client registered by the Desktop Buddy team.')
      for service,name,description,domains in (
          ('gmail','Gmail','Search and read mail, create drafts, send, label and bin conversations.',('mail.google.com',)),
          ('calendar','Calendar','List, create, change and answer events on your calendars.',('calendar.google.com',)),
          ('drive','Drive','Find, read and organise files.',('drive.google.com',)),
          ('docs','Docs','Read and edit documents.',('docs.google.com',)),
          ('sheets','Sheets','Read and edit spreadsheets.',('sheets.google.com',)),
          ('slides','Slides','Read and edit presentations.',('slides.google.com',)),
          ('chat','Chat','Spaces and messages.',('chat.google.com',)),
          ('contacts','Contacts','People and contact details.',('contacts.google.com',)),
          ('classroom','Classroom','Classes and coursework.',('classroom.google.com',)),
      )],
    entry('google-workspace', 'Google Workspace (community connector)',
          'Gmail, Calendar, Drive, Docs, Sheets, Slides, Forms, Tasks and Chat through a locally installed connector.',
          'https://github.com/taylorwilsdon/google_workspace_mcp',
          command=('workspace-mcp',), auth='none', publisher='Community · taylorwilsdon', group='Google',
          domains=('mail.google.com','calendar.google.com','drive.google.com','docs.google.com'),
          note='IT installs workspace-mcp and gives it a Google client through the secret environment field. The connector handles Google sign-in itself.'),
    entry('notion', 'Notion', 'Search, read and edit workspace pages.',
          'https://developers.notion.com/docs/get-started-with-mcp', url='https://mcp.notion.com/mcp', domains=('notion.so','notion.com')),
    entry('linear', 'Linear', 'Issues, projects and comments.',
          'https://linear.app/docs/mcp', url='https://mcp.linear.app/mcp', domains=('linear.app',)),
    entry('atlassian', 'Atlassian · Jira and Confluence', 'Jira issues, Confluence pages and other Atlassian tools.',
          'https://atlassian.github.io/atlassian-mcp-server/', url='https://mcp.atlassian.com/v2/mcp', domains=('atlassian.net',),
          legacy_urls=('https://mcp.atlassian.com/v1/mcp',)),
    entry('asana', 'Asana', 'Tasks, projects and team work.',
          'https://developers.asana.com/docs/integrating-with-asanas-mcp-server', url='https://mcp.asana.com/v2/mcp', domains=('app.asana.com',),
          sign_in='client', note='Asana only accepts a client registered by the Desktop Buddy team.'),
    entry('monday', 'monday.com', 'Boards, items and updates.',
          'https://developer.monday.com/apps/docs/mondaycom-mcp-integration', url='https://mcp.monday.com/mcp', domains=('monday.com',)),
    entry('clickup', 'ClickUp', 'Tasks, docs and spaces.',
          'https://help.clickup.com/hc/en-us/articles/36197059091479', url='https://mcp.clickup.com/mcp', domains=('clickup.com',)),
    entry('todoist', 'Todoist', 'Tasks, projects and due dates.',
          'https://www.todoist.com/help/articles/use-todoist-with-ai-assistants', url='https://ai.todoist.net/mcp', domains=('todoist.com',)),
    entry('airtable', 'Airtable', 'Bases, tables and records.',
          'https://support.airtable.com/docs/airtable-mcp-server', url='https://mcp.airtable.com/mcp', domains=('airtable.com',)),
    entry('dropbox', 'Dropbox', 'Files and folders in Dropbox.',
          'https://help.dropbox.com/integrations/dropbox-mcp', url='https://mcp.dropbox.com/mcp', domains=('dropbox.com',)),
    entry('canva', 'Canva', 'Designs, templates and brand assets.',
          'https://www.canva.dev/docs/connect/canva-mcp-server-setup/', url='https://mcp.canva.com/mcp', domains=('canva.com',)),
    entry('figma', 'Figma', 'Design files and components.',
          'https://developers.figma.com/docs/figma-mcp-server/', url='https://mcp.figma.com/mcp', domains=('figma.com',),
          sign_in='client', note='Figma only accepts a client registered by the Desktop Buddy team.'),
    entry('webflow', 'Webflow', 'Sites, pages and CMS content.',
          'https://developers.webflow.com/data/docs/ai-tools', url='https://mcp.webflow.com/mcp', domains=('webflow.com',)),
    entry('wix', 'Wix', 'Wix sites, content and business tools.',
          'https://dev.wix.com/docs/sdk/articles/work-with-the-sdk/wix-mcp', url='https://mcp.wix.com/mcp', domains=('wix.com',)),
    entry('intercom', 'Intercom', 'Conversations, contacts and help articles.',
          'https://developers.intercom.com/docs/guides/mcp', url='https://mcp.intercom.com/mcp', domains=('intercom.com','intercom.io')),
    entry('zapier', 'Zapier', 'Run Zapier actions across thousands of apps.',
          'https://zapier.com/mcp', url='https://mcp.zapier.com/api/mcp/mcp', domains=('zapier.com',)),
    entry('slack', 'Slack', 'Messages, channels, search and canvases.',
          'https://docs.slack.dev/ai/slack-mcp-server/', url='https://mcp.slack.com/mcp', domains=('slack.com',),
          sign_in='client', note='Slack only accepts a client registered by the Desktop Buddy team.'),
    entry('hubspot', 'HubSpot', 'Contacts, deals and CRM records.',
          'https://developers.hubspot.com/mcp', url='https://mcp.hubspot.com/', domains=('app.hubspot.com','hubspot.com'),
          sign_in='client', note='HubSpot only accepts a client registered by the Desktop Buddy team.'),
    entry('zendesk', 'Zendesk', 'Tickets, users and help center articles.',
          'https://developer.zendesk.com/documentation/ai/mcp/', url='https://mcp.zendesk.com/mcp', domains=('zendesk.com',),
          sign_in='client', note='Zendesk only accepts a client registered by the Desktop Buddy team.'),
    entry('box', 'Box', 'Files and folders in Box.',
          'https://developer.box.com/guides/box-mcp/remote/', url='https://mcp.box.com/mcp', domains=('box.com',),
          sign_in='client', note='Box only accepts a client registered by the Desktop Buddy team.'),
    entry('stripe', 'Stripe', 'Payments, customers and invoices.',
          'https://docs.stripe.com/mcp', url='https://mcp.stripe.com', domains=('dashboard.stripe.com',), group='Business'),
    entry('paypal', 'PayPal', 'Invoices, payments and disputes.',
          'https://developer.paypal.com/tools/mcp-server/', url='https://mcp.paypal.com/mcp', domains=('paypal.com',), group='Business'),
    entry('square', 'Square', 'Orders, customers and catalog.',
          'https://developer.squareup.com/docs/mcp', url='https://mcp.squareup.com/mcp', domains=('squareup.com',), group='Business'),
    entry('vercel', 'Vercel', 'Projects, deployments, logs and documentation.',
          'https://vercel.com/docs/agent-resources/vercel-mcp', url='https://mcp.vercel.com', domains=('vercel.com',), group='Developer'),
    entry('netlify', 'Netlify', 'Sites, deploys and forms.',
          'https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/', url='https://netlify-mcp.netlify.app/mcp', domains=('netlify.com','netlify.app'), group='Developer'),
    entry('github', 'GitHub', 'Repositories, issues, pull requests and workflows.',
          'https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md',
          url='https://api.githubcopilot.com/mcp/', auth='bearer', domains=('github.com',), group='Developer',
          token_url='https://github.com/settings/personal-access-tokens/new',
          note='GitHub asks for a personal access token. Create one on GitHub, copy it, and paste it into Buddy.'),
    entry('gitlab', 'GitLab', 'Repositories, merge requests, issues and CI/CD.',
          'https://docs.gitlab.com/user/model_context_protocol/mcp_server/', url='https://gitlab.com/api/v4/mcp', domains=('gitlab.com',), group='Developer',
          note='Your GitLab group must have MCP turned on.'),
    entry('supabase', 'Supabase', 'Projects, database and development tools.',
          'https://supabase.com/docs/guides/ai-tools/mcp', url='https://mcp.supabase.com/mcp', domains=('supabase.com',), group='Developer'),
    entry('neon', 'Neon', 'Postgres projects, branches and queries.',
          'https://neon.com/docs/ai/neon-mcp-server', url='https://mcp.neon.tech/mcp', domains=('neon.tech','neon.com'), group='Developer'),
    entry('prisma', 'Prisma', 'Prisma Postgres databases.',
          'https://www.prisma.io/docs/postgres/integrations/mcp-server', url='https://mcp.prisma.io/mcp', domains=('prisma.io',), group='Developer'),
    entry('sentry', 'Sentry', 'Errors, projects, traces and debugging.',
          'https://mcp.sentry.dev/', url='https://mcp.sentry.dev/mcp', domains=('sentry.io',), group='Developer'),
    entry('cloudflare', 'Cloudflare', 'Workers, KV, R2, D1 and other account resources.',
          'https://github.com/cloudflare/mcp-server-cloudflare', url='https://bindings.mcp.cloudflare.com/mcp', domains=('dash.cloudflare.com',), group='Developer'),
    entry('cloudflare-observability', 'Cloudflare logs and analytics', 'Workers logs and observability data.',
          'https://github.com/cloudflare/mcp-server-cloudflare', url='https://observability.mcp.cloudflare.com/mcp', group='Developer'),
    entry('cloudinary', 'Cloudinary', 'Images, videos and media assets.',
          'https://cloudinary.com/documentation/cloudinary_llm_mcp', url='https://asset-management.mcp.cloudinary.com/mcp', domains=('cloudinary.com',), group='Developer'),
    entry('huggingface', 'Hugging Face', 'Models, datasets, Spaces and papers.',
          'https://huggingface.co/settings/mcp', url='https://huggingface.co/mcp', auth='none', domains=('huggingface.co',), group='Developer'),
    entry('context7', 'Context7', 'Up-to-date documentation for programming libraries.',
          'https://context7.com/', url='https://mcp.context7.com/mcp', auth='none', group='Reference'),
    entry('exa', 'Exa web search', 'Search the web and read pages.',
          'https://docs.exa.ai/reference/exa-mcp', url='https://mcp.exa.ai/mcp', auth='none', group='Reference'),
    entry('microsoft-learn', 'Microsoft Learn', 'Microsoft technical documentation. Not Outlook or Microsoft 365 account access.',
          'https://learn.microsoft.com/en-us/training/support/mcp', url='https://learn.microsoft.com/api/mcp', auth='none', domains=('learn.microsoft.com',), group='Reference'),
    entry('deepwiki', 'DeepWiki', 'Explanations of public GitHub repositories.',
          'https://docs.devin.ai/work-with-devin/deepwiki-mcp', url='https://mcp.deepwiki.com/mcp', auth='none', group='Reference'),
    entry('docker', 'Docker MCP Gateway', 'Servers configured in Docker’s MCP Toolkit.',
          'https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/',
          command=('docker','mcp','gateway','run'), auth='none', group='Developer',
          note='Needs Docker Desktop with the MCP Toolkit. Docker manages the downstream sign-ins.'),
]

CATALOG_BY_ID = {item['id']: item for item in CATALOG}


def search_catalog(query=''):
    terms=query.casefold().split()
    return [dict(item) for item in CATALOG if all(t in (item['name']+' '+item['description']+' '+item['group']).casefold() for t in terms)]


def canonical_host(host):
    host=host.lower().rstrip('.')
    try:return ipaddress.ip_address(host).compressed
    except ValueError:
        try:return host.encode('idna').decode('ascii')
        except UnicodeError:return host


def host_matches(host,domain):
    host=canonical_host(host);domain=canonical_host(domain)
    return bool(host and domain) and (host==domain or host.endswith('.'+domain))


def loopback_host(host):
    host=canonical_host(host)
    if host=='localhost' or host.endswith('.localhost'):return True
    try:
        address=ipaddress.ip_address(host)
        return address.is_loopback or bool(getattr(address,'ipv4_mapped',None) and address.ipv4_mapped.is_loopback)
    except ValueError:return False


def endpoint_matches(url,endpoint):
    """Reserve the configured endpoint's origin even without a domain mapping.

    Port-scoped matching leaves other services on a shared host usable. Treat
    loopback names/addresses alike so localhost cannot bypass a numeric endpoint.
    """
    destination=urlsplit(url);saved=urlsplit(endpoint)
    host=canonical_host(destination.hostname or '');known=canonical_host(saved.hostname or '')
    if not host or not known:return False
    same_host=host==known or loopback_host(host) and loopback_host(known)
    port=destination.port or (443 if destination.scheme in ('https','wss') else 80)
    saved_port=saved.port or (443 if saved.scheme=='https' else 80)
    return same_host and port==saved_port


def matching_services(host):
    """Catalog services whose website or MCP endpoint lives at this host."""
    return [item for item in CATALOG if any(host_matches(host,domain) for domain in
            [*item['domains'],*[urlsplit(url).hostname or '' for url in [item['url'],*item.get('legacy_urls',[])]]])]


def consumer_available(item, registrations=None):
    """True when Connect can be offered to an everyday person right now."""
    kind=item.get('sign_in','dcr')
    if kind in ('dcr','none','token'):return True
    if kind=='client':return bool(registrations and registrations.available(item))
    if kind=='install':return bool(item['command']) and shutil.which(item['command'][0]) is not None
    return False


def availability_text(item, registrations=None):
    """One plain sentence for a card that cannot be connected yet."""
    kind=item.get('sign_in','dcr')
    if consumer_available(item,registrations):return ''
    if kind=='client':
        return 'Needs a one-time setup by the Desktop Buddy team before you can sign in. There is nothing for you to install.'
    if kind=='install':
        return 'Needs a program installed by IT first.'
    return 'Not available yet.'


def catalog_item(config):
    # An ID is a label, not authority to give this connection product credentials.
    # Editing a preset's destination turns it into a custom connection.
    for item in CATALOG:
        if not (config.id==item['id'] or config.id.startswith(item['id']+'-')) or config.auth!=item['auth']:continue
        if config.transport=='http' and item['url']:
            if config.url==item['url']:return item
            if config.url in item.get('legacy_urls',[]):
                # Keep the saved destination. Registration availability and
                # credential binding must still match this exact older URL.
                return {**item,'url':config.url}
        elif config.transport=='stdio' and item['command'] and config.command==item['command']:return item
    return None


def consumer_name(item):
    return item['name']
