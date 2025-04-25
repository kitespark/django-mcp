import asyncio
import collections
import json
import logging
from uuid import UUID

from django.core.cache import cache
from mcp.server.sse import SseServerTransport
from mcp.types import JSONRPCNotification, JSONRPCMessage

from .log import logger


async def try_replay_session_initialize(sse: SseServerTransport, session_id: str, cache_slug: str):
    # Replay any cached initialize messages in case of server restart / non-sticky load balancing
    # Some clients like @modelcontextprotocol/inspector and Cursor may not send `initialize` message
    # on a re-connect even when new session_id is returned, so we need to replay cached messages
    logger.debug(f'(try_replay_session_initialize) Attempting to replay initialize for session {session_id} with cache slug {cache_slug}')
    writer = sse._read_stream_writers.get(UUID(session_id))
    if writer is None:
        logger.warning(f"(try_replay_session_initialize) No stream writer found for session {session_id}")
        logger.debug(str(sse._read_stream_writers))
        return

    for method in ('initialize', 'notifications/initialized'):
        cache_key = f'mcp:{cache_slug}:{method}'
        try:
            cached_json = cache.get(cache_key)
            if cached_json:
                payload = json.loads(cached_json)
                payload['_synthetic'] = True
                payload['_replay'] = True
                from mcp.types import JSONRPCMessage
                replay_msg = JSONRPCMessage.model_validate_json(json.dumps(payload))
                await writer.send(replay_msg)
                logger.debug(f"Replayed cached '{method}' for: {cache_slug}\n\t{payload}")
            else:
                logger.debug(f"No cached '{method}' from previous session found for: {cache_slug}")
        except Exception as e:
            logger.error(f"Unexpected error getting cache key '{cache_key}' for MCP session replay: {e}")


class SseReadStreamProxy:
    def __init__(self, wrapped, cache_slug: str, enable_cache_persist_sessions: bool = True, ttl_seconds: int = None):
        self._wrapped = wrapped
        self._cache_slug = cache_slug
        self._initialized = False
        self.enable_cache_persist_sessions = enable_cache_persist_sessions
        self.ttl_seconds = ttl_seconds  # value of None means forever

    async def receive(self):
        msg = await self._wrapped.receive()
        if self.enable_cache_persist_sessions:  # workaround for client reconnects to re-init sessions
            if hasattr(msg, "root"):
                method = getattr(msg.root, "method", None)
                if method in ("initialize", "notifications/initialized"):
                    try:
                        data = msg.model_dump(mode="json", by_alias=True, exclude_none=True)
                        if data.get("_synthetic", False):
                            logger.debug(f"Skipping caching synthetic {method} message")
                            return msg
                        key = f"mcp:{self._cache_slug}:{method}"
                        cache.set(key, json.dumps(data), timeout=self.ttl_seconds)
                        logger.debug(f"Cached {method} request under key: {key}")
                    except Exception as e:
                        logger.error(f"Unexpected error setting cache key '{cache_key}' for MCP session replay: {e}")
        return msg

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            message = await self.receive()
        except (StopAsyncIteration, GeneratorExit):
            raise StopAsyncIteration
        return message

    async def aclose(self):
        await self._wrapped.aclose()

    async def __aenter__(self):
        await self._wrapped.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._wrapped.__aexit__(exc_type, exc_val, exc_tb)
