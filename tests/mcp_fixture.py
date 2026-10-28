"""A real SDK stdio server used only by MCP integration tests."""
import asyncio
import os
from mcp.server import MCPServer

server=MCPServer('Buddy test server')

@server.tool()
def echo(message: str) -> str:
    return message

@server.tool()
def environment() -> str:
    return os.environ.get('BUDDY_TEST_SECRET','missing')+'|'+os.environ.get('BUDDY_PARENT_SECRET','absent')

@server.tool()
async def slow() -> str:
    await asyncio.sleep(30)
    return 'finished'

@server.resource('fixture://hello')
def resource() -> str:
    return 'Hello resource'

if __name__=='__main__':server.run()
