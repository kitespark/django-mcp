"""
django_mcp/asgi_patch_fastmcp.py

Patches FastMCP.sse_app to handle dynamic paths and ASGI connection details
"""

import asyncio
import collections
import contextvars
import json
import typing

from django.conf import settings
from django.core.cache import cache
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request

from .log import logger
from .interop_django_fastapi import _interpolate_starlette_path_with_url_params
from .asgi_interceptors import make_intercept_sse_send
from .mcp_sdk_session_replay import SseReadStreamProxy

# Context variable to store MCP path parameters captured during the ASGI SSE connection
mcp_connection_path_params: contextvars.ContextVar[dict[str, typing.Any] | None] = contextvars.ContextVar(
    "mcp_connection_path_params", default=None
)


# Override FastMCP.sse_app() to support nested paths (e.g. /mcp/sse instead of /sse)
# This monkey patch addresses a limitation in modelcontextprotocol/python-sdk.
# Related issue: https://github.com/modelcontextprotocol/python-sdk/issues/412
# Source code reference (original method):
# https://github.com/modelcontextprotocol/python-sdk/blob/70115b99b3ee267ef10f61df21f73a93db74db03/src/mcp/server/fastmcp/server.py#L480
def FastMCP_sse_app_patch(_self: FastMCP, starlette_base_path: str, *, enable_cache_persist_sessions: bool = True):
    '''
    Patched version of FastMCP.sse_app

    Initializes the SseServerTransport and provides a custom `handle_sse`
    ASGI endpoint that captures path parameters, stores them in Context,
    and intercepts the outgoing SSE 'endpoint' event to inject the correctly
    resolved message posting URL.
    '''

    # Only enable caching if the path is dynamic AND caching is enabled by config
    is_dynamic_path = "{" in starlette_base_path
    should_cache_sessions = is_dynamic_path and enable_cache_persist_sessions
    logger.info(
        f"Dynamic path detected: {is_dynamic_path}, Caching enabled by config: {enable_cache_persist_sessions}, Session caching active: {should_cache_sessions}"
    )

    # Initialize SseServerTransport - message URL here is just a template
    sse = SseServerTransport(f'{starlette_base_path}/messages/')

    async def handle_sse(request: Request) -> None:
        token = None  # Initialize token for context variable reset
        resolved_base_url_from_params = "" # Initialize to avoid potential UnboundLocalError in error logs

        if settings.MCP_LOG_HTTP_HEADERS_ON_SSE_CONNECT:
            logger.info(f'SSE connection headers:')
            for header, value in request.headers.items():
                logger.info(f'\t{header}: {value}')

        # Step 1) Capture path parameters from the request and store them in Context
        try:
            # Extract path parameters from the request
            path_params = request.path_params

            # Calculate the actual base URL using the captured parameters
            resolved_base_url_from_params = _interpolate_starlette_path_with_url_params(
                starlette_base_path, path_params
            )
            logger.info(f"Resolved base URL for SSE: {resolved_base_url_from_params}")

            # Set the context variable for the duration of this connection
            # so that URL params can be accessed by mcp.tool-decorated functions
            token = mcp_connection_path_params.set(path_params)
            logger.debug(f"Set mcp_connection_path_params: {path_params}")
        except Exception as e:
            logger.exception(f"Error processing path parameters or setting context var: {e}")
            # Reset context var if set before error occurred during setup
            if token:
                mcp_connection_path_params.reset(token)
            raise # Re-raise the exception

        # Step 2) Intercept the original ASGI send callable to be able to rewrite SSE payloads
        intercepted_send = make_intercept_sse_send(
            sse,
            request._send,
            resolved_base_url_from_params
        )
        try:
            # Use the intercepted send when connecting
            async with sse.connect_sse(
                request.scope,
                request.receive,
                intercepted_send,
            ) as (read_stream, write_stream):
                # Wrap read_stream in proxy to intercept messages, passing caching flag
                read_stream_proxied = SseReadStreamProxy(
                    read_stream,
                    resolved_base_url_from_params,
                    enable_cache_persist_sessions=should_cache_sessions
                )
            # Run the MCP server loop
                await _self._mcp_server.run(
                    read_stream_proxied,
                    write_stream,
                    _self._mcp_server.create_initialization_options(),
                )
        finally:
            # Ensure the context variable is reset when the connection closes
            if token:
                mcp_connection_path_params.reset(token)
                logger.debug("Reset mcp_connection_path_params")

    # Return the handler and transport instance
    return (handle_sse, sse)
