"""Bounded app-tool discovery, without partial or invented input schemas."""
import json

from .security import InvalidArguments


TOOL_PAGE_BYTES = 24000
TOOL_PAGE_LIMIT = 20
# Output schemas, icons (often data URLs), and vendor metadata are not needed to
# construct a call. Keep the full description, input schema and safety hints.
DISCOVERY_FIELDS = ('name', 'title', 'description', 'inputSchema', 'annotations')


def tool_page(tools, query='', offset=0, limit=TOOL_PAGE_LIMIT):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= TOOL_PAGE_LIMIT:
        raise InvalidArguments('limit must be between 1 and 20 tools.')
    terms = query.casefold().split()
    matches = [tool for tool in tools if all(
        term in (tool['name'] + ' ' + tool.get('description', '')).casefold()
        for term in terms)]
    offset = max(0, offset)
    result = {'tools': [], 'total': len(matches), 'next_offset': None}
    for index in range(offset, min(offset + limit, len(matches))):
        tool = {key: value for key, value in matches[index].items() if key in DISCOVERY_FIELDS}
        following = index + 1 if index + 1 < len(matches) else None
        candidate = {**result, 'tools': [*result['tools'], tool], 'next_offset': following}
        # Use ASCII escaping and default JSON spacing: budget the serialized
        # response, not character counts or just the input schemas. Leave ample
        # room for the host's result envelope and redaction/formatting overhead.
        if len(json.dumps(candidate, ensure_ascii=True).encode('utf-8')) > TOOL_PAGE_BYTES:
            if result['tools']:
                result['next_offset'] = index
                return result
            # Never return a cut-off schema as a usable tool. This is a real
            # source-read error; the usual change pause must remain in force.
            return {'isError': True, 'type': 'DiscoveryItemTooLarge',
                    'error': 'One matching app tool has a description or input schema too large to read safely. No schema was supplied; do not call this tool.',
                    'tools': [], 'total': len(matches), 'next_offset': index}
        result = candidate
    return result
