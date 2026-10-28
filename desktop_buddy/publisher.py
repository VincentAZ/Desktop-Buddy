"""OAuth clients registered by the Desktop Buddy team and shipped with the app.

Some services (Google, Slack, Asana, HubSpot, Zendesk, Box) do not let an app
register itself on the fly; the product owner registers once with the provider
and ships the resulting client here. Every installation then offers a plain
Connect button for those services, and nobody using Buddy ever sees a client ID.

Fill in ``CLIENTS`` in ``publisher_local.py`` (gitignored, merged on import) before
building a release, or let IT paste the same values
under Settings → IT setup → App sign-in keys, which stores them in the keyring
of that one installation instead.

A desktop app cannot keep a client secret from its owner, which is why Google
and the others treat these as "installed app" clients; the real protection is the
sign-in itself, which always happens in the person's browser.

Format::

    CLIENTS = {
        'google': {'client_id': '1234-abc.apps.googleusercontent.com',
                   'client_secret': 'GOCSPX-...'},
        'slack':  {'client_id': '...', 'client_secret': '...'},
    }

The ``google`` key covers every Google service in the catalog. Other keys are
catalog IDs. ``scope`` is optional and overrides the catalog default.
"""

CLIENTS = {
    # Intentionally empty in the repository: the registered clients live in
    # publisher_local.py (gitignored) on the build machine, or IT pastes them in.
}

try:
    from .publisher_local import CLIENTS as _LOCAL_CLIENTS
except ImportError:
    _LOCAL_CLIENTS = {}
CLIENTS.update(_LOCAL_CLIENTS)

CALLBACK_PORT = 8765


def group_of(service_id):
    """The publisher key that covers a catalog service."""
    return 'google' if service_id.startswith('google-') and service_id != 'google-workspace' else service_id


def builtin_client(service_id):
    """Client information shipped with the app for this service, or None."""
    value = CLIENTS.get(group_of(service_id))
    if not value or not value.get('client_id'):
        return None
    return dict(value)


def client_record(client_id, client_secret='', port=CALLBACK_PORT):
    """OAuth client information in the shape the MCP SDK expects."""
    client = {'client_id': client_id,
              'redirect_uris': [f'http://127.0.0.1:{port}/oauth/callback'],
              'token_endpoint_auth_method': 'client_secret_post' if client_secret else 'none'}
    if client_secret:
        client['client_secret'] = client_secret
    return client
