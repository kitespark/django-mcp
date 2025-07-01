"""
HTTP Stateless Transport for Django MCP Server

This module provides a stateless HTTP transport implementation for Django
that closely follows the official MCP SDK pattern using StreamableHTTPSessionManager.
It supports both SSE (Server-Sent Events) and JSON response modes while maintaining
full compatibility with Django's existing MCP features.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send
from starlette.responses import JSONResponse

from django.conf import settings
from django.core.asgi import get_asgi_application

from .log import logger
from .loader import register_mcp_modules
from .apps import mcp_app
from .interop_django_fastapi import _convert_django_path_to_starlette, _interpolate_starlette_path_with_url_params


def _create_health_check_response(server_name: str) -> Dict[str, Any]:
    """Create a standardized health check response."""
    tools_count = len(mcp_app._tool_manager._tools) if hasattr(mcp_app, '_tool_manager') else 0
    return {
        "status": "healthy",
        "server": server_name,
        "transport": "http_stateless",
        "tools_count": tools_count,
        "tools": list(mcp_app._tool_manager._tools.keys()) if hasattr(mcp_app, '_tool_manager') else []
    }


def _convert_asgi_headers_to_dict(response_headers: list) -> Dict[str, str]:
    """Convert ASGI headers (list of byte tuples) to dict of strings."""
    headers_dict = {}
    for header_name, header_value in response_headers:
        # ASGI headers are already bytes, decode them to strings
        name = header_name.decode('latin-1') if isinstance(header_name, bytes) else header_name
        value = header_value.decode('latin-1') if isinstance(header_value, bytes) else header_value
        headers_dict[name] = value
    return headers_dict


@dataclass
class HttpStatelessTransportConfig:
    """Configuration class for HTTP stateless transport parameters."""
    
    # Core transport settings
    server_name: str = "django-mcp-http-stateless"
    json_response: bool = False
    event_store: Optional[Any] = None
    
    # HTTP-specific settings
    request_timeout: float = 30.0
    max_request_size: int = 10 * 1024 * 1024  # 10MB
    enable_compression: bool = True
    
    # CORS settings
    cors_enabled: bool = False
    cors_origins: list[str] = field(default_factory=list)
    cors_methods: list[str] = field(default_factory=lambda: ["GET", "POST", "OPTIONS"])
    cors_headers: list[str] = field(default_factory=lambda: ["Content-Type", "Authorization"])
    
    # Health and monitoring
    enable_health_check: bool = True
    health_check_path: str = "/health"
    enable_metrics: bool = False
    metrics_path: str = "/metrics"
    
    # Session management
    session_ttl: int = 3600  # 1 hour
    session_cleanup_interval: int = 300  # 5 minutes
    
    @classmethod
    def from_django_settings(cls, **overrides) -> 'HttpStatelessTransportConfig':
        """Create configuration from Django settings with optional overrides."""
        config_kwargs = {}
        
        # Map Django settings to config attributes with defaults
        setting_mappings = {
            'MCP_HTTP_REQUEST_TIMEOUT': ('request_timeout', 30.0),
            'MCP_HTTP_MAX_REQUEST_SIZE': ('max_request_size', 10 * 1024 * 1024),
            'MCP_HTTP_ENABLE_COMPRESSION': ('enable_compression', True),
            'MCP_HTTP_CORS_ENABLED': ('cors_enabled', False),
            'MCP_HTTP_CORS_ORIGINS': ('cors_origins', []),
            'MCP_HTTP_CORS_METHODS': ('cors_methods', ["GET", "POST", "OPTIONS"]),
            'MCP_HTTP_CORS_HEADERS': ('cors_headers', ["Content-Type", "Authorization"]),
            'MCP_HTTP_JSON_RESPONSE': ('json_response', False),
            'MCP_HTTP_HEALTH_CHECK_ENABLED': ('enable_health_check', True),
            'MCP_HTTP_HEALTH_CHECK_PATH': ('health_check_path', "/health"),
            'MCP_HTTP_ENABLE_METRICS': ('enable_metrics', False),
            'MCP_HTTP_METRICS_PATH': ('metrics_path', "/metrics"),
            'MCP_HTTP_SESSION_TTL': ('session_ttl', 3600),
            'MCP_HTTP_SESSION_CLEANUP_INTERVAL': ('session_cleanup_interval', 300),
        }
        
        for setting_name, (config_attr, default_value) in setting_mappings.items():
            # Use Django setting if available, otherwise use default
            config_kwargs[config_attr] = getattr(settings, setting_name, default_value)
        
        # Apply overrides
        config_kwargs.update(overrides)
        
        logger.debug(f"HTTP stateless transport config loaded from Django settings with overrides: {list(overrides.keys())}")
        
        return cls(**config_kwargs)


class DjangoHttpStatelessServer:
    """
    Django-integrated HTTP stateless MCP server using StreamableHTTPSessionManager.
    
    This implementation follows the official MCP SDK pattern while integrating
    with Django's tool discovery and configuration system.
    """
    
    def __init__(
        self,
        config: Optional[HttpStatelessTransportConfig] = None,
        server_name: Optional[str] = None,
        json_response: Optional[bool] = None,
        event_store: Optional[Any] = None,
    ):
        """
        Initialize the Django HTTP stateless MCP server.
        
        Args:
            config: Transport configuration object
            server_name: Override server name (for backward compatibility)
            json_response: Override JSON response mode (for backward compatibility)
            event_store: Optional event store for session management
        """
        # Create config from Django settings with any overrides
        config_overrides = {}
        if server_name is not None:
            config_overrides['server_name'] = server_name
        if json_response is not None:
            config_overrides['json_response'] = json_response
        if event_store is not None:
            config_overrides['event_store'] = event_store
            
        self.config = config or HttpStatelessTransportConfig.from_django_settings(**config_overrides)
        
        # Use the global mcp_app instead of creating a separate server
        # This ensures Django tools are properly available
        self.mcp_server = mcp_app._mcp_server
        
        # Apply Django settings to the server
        self._apply_django_settings()
        
        # Create the session manager with stateless mode using the global mcp_app
        self.session_manager = StreamableHTTPSessionManager(
            app=mcp_app._mcp_server,
            event_store=self.config.event_store,
            json_response=self.config.json_response,
            stateless=True,
        )

        # Initialize the session manager since lifespan may not be supported by Django runserver/uvicorn/other ASGI servers
        self._session_manager_task: Optional[asyncio.Task] = None
        self._session_manager_ready = asyncio.Event()
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

        # Django tools are already loaded in the global mcp_app, no need to reload
        tools_count = len(mcp_app._tool_manager._tools) if hasattr(mcp_app, '_tool_manager') else 0
        logger.info(f"HTTP stateless transport using global mcp_app with {tools_count} tools")
        
        logger.info(f"Created Django HTTP stateless MCP server: {self.config.server_name}")
        logger.debug(f"HTTP stateless transport config: JSON response={self.config.json_response}, "
                    f"CORS enabled={self.config.cors_enabled}, Health check={self.config.enable_health_check}")
    
    def _apply_django_settings(self):
        """Apply Django settings to the MCP server."""
        if hasattr(settings, 'MCP_SERVER_INSTRUCTIONS'):
            self.mcp_server.instructions = settings.MCP_SERVER_INSTRUCTIONS
        if hasattr(settings, 'MCP_SERVER_VERSION'):
            self.mcp_server.version = settings.MCP_SERVER_VERSION
        if hasattr(settings, 'MCP_SERVER_TITLE'):
            self.mcp_server.title = settings.MCP_SERVER_TITLE

    async def _start_session_manager_background(self):
        """Run the session manager in the background."""
        try:
            async with self.session_manager.run():
                logger.info("MCP session manager started in background task")
                self._session_manager_ready.set()  # Signal that it's ready
                # Keep the context alive indefinitely
                await asyncio.Event().wait()  # Wait forever
        except Exception as e:
            logger.error(f"Session manager background task failed: {e}")
            raise

    async def _ensure_session_manager_started(self):
        """Idempotent session manager startup - safe to call multiple times."""
        async with self._initialization_lock:
            if self._initialized:
                return

            logger.info("Starting MCP session manager...")
            self._session_manager_task = asyncio.create_task(
                self._start_session_manager_background()
            )
            await self._session_manager_ready.wait()
            self._initialized = True
            logger.info("MCP session manager ready")

    async def handle_http_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle HTTP requests using the session manager."""
        await self._ensure_session_manager_started()
        await self.session_manager.handle_request(scope, receive, send)
    
    async def handle_health_check(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle health check requests."""
        response = JSONResponse(_create_health_check_response(self.config.server_name))
        await response(scope, receive, send)


def create_http_stateless_mcp_app(
    django_http_app,
    mcp_base_path: str = "/mcp",
    config: Optional[HttpStatelessTransportConfig] = None,
    **config_overrides
) -> Starlette:
    """
    Create a Django-compatible ASGI application with HTTP stateless MCP transport.
    
    Args:
        django_http_app: The Django ASGI application
        mcp_base_path: Base path for MCP endpoints (supports Django-style parameters)
        config: Transport configuration object
        **config_overrides: Override specific config values
        
    Returns:
        Starlette ASGI application with MCP server mounted
    """
    # Create the HTTP stateless server
    http_server = DjangoHttpStatelessServer(
        config=config or HttpStatelessTransportConfig.from_django_settings(**config_overrides)
    )
    
    # Convert Django-style path to Starlette format
    starlette_base_path = _convert_django_path_to_starlette(mcp_base_path)
    logger.debug(f"Converted Django-style base path for Starlette: {starlette_base_path}")
    
    # Build routes list
    routes = []
    
    # Add health check route if enabled
    if http_server.config.enable_health_check:
        health_path = f"{starlette_base_path.rstrip('/')}{http_server.config.health_check_path}"
        
        # Create a proper endpoint function for Starlette
        async def health_check_endpoint(request):
            from starlette.responses import JSONResponse
            return JSONResponse(_create_health_check_response(http_server.config.server_name))
        
        routes.append(Route(health_path, endpoint=health_check_endpoint, methods=["GET"]))
        logger.info(f"Health check endpoint: {health_path}")
    
    # Create redirect handler for /mcp -> /mcp/ (same pattern as SSE transport)
    async def redirect_mcp_root(request):
        from starlette.responses import RedirectResponse
        return RedirectResponse(url=f"{starlette_base_path}/", status_code=301)
    
    # Add exact path redirect (e.g., /mcp -> /mcp/) - handle all methods
    routes.append(Route(starlette_base_path, endpoint=redirect_mcp_root, methods=["GET", "POST", "OPTIONS"]))
    
    # Create main HTTP stateless endpoint handler for exact path only
    async def http_stateless_endpoint(request):
        """Starlette endpoint that delegates to the HTTP stateless server."""
        scope = request.scope
        receive = request.receive
        
        # Create a custom send function to capture the response
        response_started = False
        response_body = b""
        response_status = 200
        response_headers = []
        
        async def custom_send(message):
            nonlocal response_started, response_body, response_status, response_headers
            
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_body += message.get("body", b"")
        
        # Call the HTTP stateless server
        await http_server.handle_http_request(scope, receive, custom_send)
        
        # Return a proper Starlette response
        from starlette.responses import Response
        return Response(
            content=response_body,
            status_code=response_status,
            headers=_convert_asgi_headers_to_dict(response_headers)
        )
    
    # Add main MCP route with trailing slash - ONLY for exact path, not sub-paths
    routes.append(Route(f"{starlette_base_path}/http", endpoint=http_stateless_endpoint, methods=["GET", "POST", "OPTIONS"]))
    
    # Mount Django app at root
    routes.append(Mount("/", app=django_http_app))
    
    # Create the ASGI application
    starlette_app = Starlette(
        debug=getattr(settings, 'DEBUG', False),
        routes=routes,
    )
    
    logger.info(f"Created Django HTTP stateless MCP app at {mcp_base_path}")
    logger.info(f"HTTP stateless transport endpoints: {mcp_base_path}/http (MCP), {mcp_base_path}/health (health check)")
    return starlette_app


def mount_http_stateless_mcp_server(
    django_http_app,
    mcp_base_path: str = '/mcp',
    *,
    json_response: bool = True
) -> Starlette:
    """
    Mounts the HTTP stateless MCP server alongside a Django ASGI application.
    
    This function provides the same API pattern as mount_mcp_server for easy migration
    between SSE and HTTP stateless transports.
    
    Args:
        django_http_app: The main Django ASGI application.
        mcp_base_path: The base path for MCP endpoints. Can contain
                       Django-style path parameters (e.g., '/mcp/<uuid:session_id>')
                       which will be converted to Starlette format for routing.
        json_response: If True, uses JSON responses instead of SSE streams.
    
    Returns:
        A combined Starlette ASGI application.
    """
    config_overrides = {
        'json_response': json_response,
    }
    
    return create_http_stateless_mcp_app(
        django_http_app=django_http_app,
        mcp_base_path=mcp_base_path,
        **config_overrides
    )


def run_http_stateless_mcp_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    server_name: str = "django-mcp-http-stateless",
    mcp_base_path: str = "/mcp",
    json_response: bool = False,
    log_level: str = "INFO",
    **transport_kwargs
) -> None:
    """
    Run the Django HTTP stateless MCP server using uvicorn.
    
    Args:
        host: Host to bind to
        port: Port to listen on
        server_name: Name of the MCP server
        mcp_base_path: Base path for MCP endpoints
        json_response: Whether to use JSON responses instead of SSE streams
        log_level: Logging level
        **transport_kwargs: Additional transport configuration
    """
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Get Django ASGI application
    django_app = get_asgi_application()
    
    # Create the application
    app = mount_http_stateless_mcp_server(
        django_http_app=django_app,
        mcp_base_path=mcp_base_path,
        server_name=server_name,
        json_response=json_response,
        **transport_kwargs
    )
    
    # Run with uvicorn
    import uvicorn
    logger.info(f"Starting Django HTTP stateless MCP server on {host}:{port}")
    logger.info(f"MCP endpoint available at: http://{host}:{port}{mcp_base_path}")
    
    uvicorn.run(app, host=host, port=port)