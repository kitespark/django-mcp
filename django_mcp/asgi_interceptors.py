"""
django_mcp/asgi_interceptors.py

ASGI interceptor logic for django-mcp.

This module provides an interceptor function to modify Server-Sent Events (SSE)
"""

import json
import logging
from urllib.parse import urlparse, parse_qs

from mcp.server.sse import SseServerTransport

from .mcp_sdk_session_replay import try_replay_session_initialize

logger = logging.getLogger(__name__)

# Interceptor function to modify SSE 'endpoint' event messages
# to interpolate the URL template with actual values correctly
#   original: /mcp/{user_slug:uuid}/messages/?session_id=6d60664594bc48d4b0f7d4362f4d728d
#   desired: /mcp/f9aac356-7d84-4c6f-9bfc-dd7bca4961d9/messages/?session_id=6d60664594bc48d4b0f7d4362f4d728d
def make_intercept_sse_send(
    sse: SseServerTransport,
    original_send,
    resolved_base_url_from_params: str,
):
    """
    Creates an ASGI send interceptor to modify SSE endpoint URLs and replay session init messages on client reconnect.

    Args:
        sse: The SseServerTransport instance.
        original_send: The original ASGI send callable.
        resolved_base_url_from_params: The base URL with path parameters resolved.
    """
    async def intercept_sse_send(message):
        if message["type"] == "http.response.body":
            try:
                # modify /mcp/sse route payload that looks like:
                #   event: endpoint
                #   data: /mcp/{user_slug:uuid}/messages/?session_id=17a8c347cdb144f58863c6759c35e848
                body_bytes = message.get("body", b"")
                body_str = body_bytes.decode('utf-8')
                if body_str.startswith("event: endpoint"):
                    lines = body_str.strip().split('\n')
                    if len(lines) == 2 and lines[1].startswith("data: "):
                        original_data_str = lines[1][len("data: "):]
                        parsed = urlparse(original_data_str)
                        query = parse_qs(parsed.query)
                        session_id = query.get('session_id', [None])[0]
                        if session_id:
                            new_url = f'{resolved_base_url_from_params}/messages/?session_id={session_id}'
                            modified_body_str = f"event: endpoint\ndata: {new_url}\n\n"
                            message["body"] = modified_body_str.encode('utf-8')
                            logger.info(f"New MCP connection. session_id={session_id} endpoint={new_url}")
                            await try_replay_session_initialize(sse, session_id=session_id, cache_slug=resolved_base_url_from_params)
                        else:
                            logger.warning("Could not find session_id in original endpoint event data.")
                    else:
                        logger.warning(f"Could not parse endpoint event body: {body_str!r}")
            except Exception as e:
                logger.error(f"Error modifying SSE endpoint event: {e}", exc_info=True)
        await original_send(message)
    return intercept_sse_send
