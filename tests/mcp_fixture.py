"""A real SDK stdio server used only by MCP integration tests."""
import asyncio
import os
from pathlib import Path
from mcp.server import MCPServer

server=MCPServer('Buddy test server')

@server.tool()
def echo(message: str) -> str:
    return message

@server.tool()
def environment() -> str:
    return os.environ.get('BUDDY_TEST_SECRET','missing')+'|'+os.environ.get('BUDDY_PARENT_SECRET','absent')

@server.tool()
async def slow(started_path: str = '') -> str:
    if started_path:Path(started_path).write_text('started')
    await asyncio.sleep(30)
    return 'finished'

@server.resource('fixture://hello')
def resource() -> str:
    return 'Hello resource'

if __name__=='__main__':server.run()
