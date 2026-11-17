import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from desktop_buddy.mcp_discovery import TOOL_PAGE_BYTES, tool_page
from desktop_buddy.security import InvalidArguments
from desktop_buddy.tools import Registry


def app_tool(index=0, description='Repository operations', **extra):
    return {'name': f'repository_{index}', 'description': description,
            'inputSchema': {'type': 'object', 'properties': {
                'owner': {'type': 'string'},
                'options': {'type': 'object', 'properties': {
                    'private': {'type': 'boolean', 'default': True}},
                    'additionalProperties': False}},
                'required': ['owner'], 'additionalProperties': False}, **extra}


class MCPDiscoveryTests(unittest.TestCase):
    def assert_bounded(self, result):
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode('utf-8')), TOOL_PAGE_BYTES)

    def test_large_icons_and_output_metadata_do_not_escape_into_discovery(self):
        tools = [app_tool(i, icons=[{'src': 'data:image/png;base64,' + 'A' * 100000}],
                          outputSchema={'description': 'B' * 100000},
                          _meta={'vendor': 'C' * 100000}) for i in range(20)]
        result = tool_page(tools, query='repository')
        self.assertEqual(len(result['tools']), 20)
        self.assertIsNone(result['next_offset'])
        for original, supplied in zip(tools, result['tools']):
            self.assertEqual(supplied['inputSchema'], original['inputSchema'])
            self.assertEqual(supplied['description'], original['description'])
            self.assertNotIn('icons', supplied)
            self.assertNotIn('outputSchema', supplied)
            self.assertNotIn('_meta', supplied)
        self.assert_bounded(result)

    def test_byte_budget_pages_without_losing_or_repeating_matches(self):
        tools = [app_tool(i, description='repository ' + 'details ' * 400) for i in range(37)]
        supplied = []
        offset = 0
        pages = 0
        while offset is not None:
            result = tool_page(tools, query='repository', offset=offset)
            self.assert_bounded(result)
            self.assertEqual(result['total'], len(tools))
            self.assertTrue(result['tools'])
            supplied.extend(result['tools'])
            pages += 1
            following = result['next_offset']
            self.assertTrue(following is None or following > offset)
            offset = following
            self.assertLessEqual(pages, len(tools))
        self.assertGreater(pages, 2)
        self.assertEqual(supplied, tools)

    def test_input_schema_description_title_and_annotations_are_complete(self):
        tool = app_tool(title='Repository', annotations={'readOnlyHint': True, 'destructiveHint': False})
        tool['inputSchema']['$defs'] = {'nested': {'type': 'array', 'items': {'enum': ['yes', 'no']}}}
        before = copy.deepcopy(tool)
        result = tool_page([tool])
        self.assertEqual(result['tools'], [before])
        self.assertEqual(tool, before)
        self.assert_bounded(result)

    def test_serialized_budget_accounts_for_unicode_and_escaping(self):
        tools = [app_tool(i, description='repository ' + '\U0001f680"\\\n' * 400) for i in range(15)]
        result = tool_page(tools)
        self.assert_bounded(result)
        self.assertGreater(len(result['tools']), 0)
        self.assertLess(len(result['tools']), len(tools))
        self.assertEqual(result['next_offset'], len(result['tools']))
        self.assertEqual(result['tools'], tools[:len(result['tools'])])

    def test_oversized_single_schema_is_an_explicit_read_failure(self):
        tool = app_tool()
        tool['inputSchema']['description'] = 'x' * TOOL_PAGE_BYTES
        result = tool_page([tool])
        self.assertTrue(result['isError'])
        self.assertEqual(result['type'], 'DiscoveryItemTooLarge')
        self.assertEqual(result['tools'], [])
        self.assertEqual(result['next_offset'], 0)
        self.assertIn('No schema was supplied', result['error'])
        self.assert_bounded(result)

    def test_oversized_later_tool_is_not_silently_skipped(self):
        tools = [app_tool(), app_tool(1, description='x' * TOOL_PAGE_BYTES), app_tool(2)]
        first = tool_page(tools)
        self.assertEqual(first['tools'], tools[:1])
        self.assertEqual(first['next_offset'], 1)
        second = tool_page(tools, offset=first['next_offset'])
        self.assertTrue(second['isError'])
        self.assertEqual(second['next_offset'], 1)
        self.assertEqual(second['tools'], [])

    def test_limit_query_and_offset(self):
        tools = [app_tool(i) for i in range(8)] + [app_tool(9, description='Unrelated')]
        result = tool_page(tools, query='REPOSITORY operations', offset=2, limit=2)
        self.assertEqual(result, {'tools': tools[2:4], 'total': 8, 'next_offset': 4})
        self.assertEqual(tool_page(tools, offset=100), {'tools': [], 'total': 9, 'next_offset': None})
        self.assertEqual(tool_page(tools, query='does-not-exist'), {'tools': [], 'total': 0, 'next_offset': None})
        for limit in (0, 21, -1, True, '1'):
            with self.subTest(limit=limit), self.assertRaises(InvalidArguments):
                tool_page(tools, limit=limit)

    def test_small_default_page_is_backwards_compatible(self):
        tools = [app_tool(i) for i in range(25)]
        self.assertEqual(tool_page(tools), {'tools': tools[:20], 'total': 25, 'next_offset': 20})

    def test_registry_uses_bounded_discovery_and_forwards_pagination(self):
        manager = SimpleNamespace(tools=Mock(return_value=[app_tool(i) for i in range(4)]))
        registry = SimpleNamespace(mcp_required=lambda: manager)
        token = object()
        result = Registry.mcp_tools(registry, {'server': 'saved-github', 'query': 'operations',
                                             'offset': 1, 'limit': 1}, token)
        manager.tools.assert_called_once_with('saved-github', token)
        self.assertEqual(result, {'tools': [app_tool(1)], 'total': 4, 'next_offset': 2})


if __name__ == '__main__':
    unittest.main()
