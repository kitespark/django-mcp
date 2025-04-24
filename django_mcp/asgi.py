"""
django_mcp/asgi.py

ASGI configuration for django-mcp, allowing mounting the MCP server
with a Django application.
"""

from django.conf import settings
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport

from .log import logger
from .interop_django_fastapi import _convert_django_path_to_starlette
from .asgi_patch_fastmcp import FastMCP_sse_app_patch

from .asgi_interceptors import make_intercept_sse_send

mcp_app = FastMCP()

def apply_django_settings(fastmcp_obj: FastMCP):
    fastmcp_obj._mcp_server.title = settings.MCP_SERVER_TITLE
    fastmcp_obj._mcp_server.instructions = settings.MCP_SERVER_INSTRUCTIONS
    fastmcp_obj._mcp_server.version = settings.MCP_SERVER_VERSION

def mount_mcp_server(django_http_app: ASGIApp, mcp_base_path: str = '/mcp', *, enable_cache_persist_sessions: bool = True) -> ASGIApp:
    """
    Mounts the MCP server alongside a Django ASGI application.

    Args:
        django_http_app: The main Django ASGI application.
        mcp_base_path: The base path for MCP endpoints. Can contain
                       Django-style path parameters (e.g., '/mcp/<uuid:session_id>')
                       which will be converted to Starlette format for routing.
        enable_cache_persist_sessions: If True, enables caching of MCP initialization messages for client reconnects.

    Returns:
        A combined Starlette ASGI application.
    """
    # Apply project settings.py overrides to the MCP application
    apply_django_settings(mcp_app)

    # Convert the base path for Starlette routing
    starlette_base_path = _convert_django_path_to_starlette(mcp_base_path)
    logger.debug(f"Converted Django-style base path for Starlette: {starlette_base_path}")

    # Call the patched FastMCP.sse_app() method, passing the Starlette path and caching flag.
    (handle_sse, sse) = FastMCP_sse_app_patch(
        mcp_app,
        starlette_base_path=starlette_base_path,
        enable_cache_persist_sessions=enable_cache_persist_sessions
    )

    # Register the patched SSE handler and mount the messages endpoint
    # using the Starlette-compatible path.
    combined_app = Starlette(routes=[
        # Route for SSE connections (handled by patched handle_sse)
        Route(f'{starlette_base_path}/sse', endpoint=handle_sse),
        # Route for message POSTing (handled by SseServerTransport instance returned by patch)
        Mount(f'{starlette_base_path}/messages/', app=sse.handle_post_message),
        # Mount the main Django app at the root
        Mount('/', app=django_http_app),
    ])

    logger.info(
        f"Serving '{settings.MCP_SERVER_TITLE}' MCP SSE at "
        f"{starlette_base_path}/sse (Django base: {mcp_base_path})"
    )

    return combined_app
