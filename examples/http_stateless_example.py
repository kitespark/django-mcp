"""
Example demonstrating Django HTTP stateless MCP server implementation.

This example shows how to use the new HTTP stateless transport alongside
the existing SSE transport, demonstrating the unified API.
"""

import os
import sys
import django
from django.core.asgi import get_asgi_application

# Add the parent directory to Python path so Django can find the examples module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal Django configuration for the example
from django.conf import settings
if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='example-secret-key-for-demo-only',
        ROOT_URLCONF=None,
        INSTALLED_APPS=[
            'django_mcp',
        ],
        MCP_SERVER_TITLE='Django HTTP Stateless Example Server',
        MCP_SERVER_INSTRUCTIONS='Example Django MCP server using HTTP stateless transport',
        MCP_SERVER_VERSION='1.0.0',
        MCP_LOG_LEVEL='INFO',
        ALLOWED_HOSTS=["*"],
        
        # HTTP Stateless Transport Settings
        MCP_HTTP_REQUEST_TIMEOUT=60.0,
        MCP_HTTP_CORS_ENABLED=True,
        MCP_HTTP_CORS_ORIGINS=['http://localhost:3000', 'http://127.0.0.1:3000'],
        MCP_HTTP_JSON_RESPONSE=True,
        MCP_HTTP_HEALTH_CHECK_ENABLED=True,
    )

# Initialize Django
django.setup()

# Import the transport functions after Django is configured
from django_mcp import mount_mcp_server, mount_http_stateless_mcp_server

# Get Django ASGI application
django_app = get_asgi_application()

# Example 1: Using the unified mount_mcp_server with transport selection
print("=== Example 1: Unified API with transport selection ===")

# SSE transport (default, existing behavior)
sse_app = mount_mcp_server(
    django_app, 
    '/mcp-sse',
    transport_type='sse',
    enable_cache_persist_sessions=True
)

# HTTP stateless transport
http_app = mount_mcp_server(
    django_http_app=django_app, 
    mcp_base_path='/mcp-http',
    transport_type='http_stateless',
    json_response=True
)

# Example 2: Using the dedicated HTTP stateless function
print("=== Example 2: Dedicated HTTP stateless function ===")

http_stateless_app = mount_http_stateless_mcp_server(
    django_app,
    mcp_base_path="/mcp",
    json_response=True
)

if __name__ == "__main__":
    import uvicorn
    
    print("\n=== Starting Django MCP HTTP Stateless Server ===")
    print("Server features:")
    print("- HTTP stateless transport using StreamableHTTPSessionManager")
    print("- JSON response mode enabled")
    print("- CORS support enabled")
    print("- Health check endpoint enabled")
    print("- Django tool auto-discovery")
    print("- Dynamic path parameter support")
    print("")
    print("Available endpoints:")
    print("- MCP HTTP stateless endpoint: http://localhost:8000/mcp/http")
    print("- Health check: http://localhost:8000/mcp/health")
    print("- Django app: http://localhost:8000/")
    print("")
    print("Test with:")
    print("curl -X POST http://localhost:8000/mcp/http \\")
    print("  -H 'Content-Type: application/json' \\")
    print("  -H 'Accept: application/json, text/event-stream' \\")
    print("  -d '{\"jsonrpc\": \"2.0\", \"method\": \"tools/list\", \"id\": 1}'")
    print("")
    print("Health check:")
    print("curl http://localhost:8000/mcp/health")
    print("")
    print("Test session search tool:")
    print("curl -X POST http://localhost:8000/mcp/http \\")
    print("  -H 'Content-Type: application/json' \\")
    print("  -H 'Accept: application/json, text/event-stream' \\")
    print("  -d '{\"jsonrpc\": \"2.0\", \"method\": \"tools/call\", \"params\": {\"name\": \"find_sessions\", \"arguments\": {\"app_id\": \"test_app\", \"query\": \"user login\"}}, \"id\": 2}'")
    
    # Use the HTTP stateless app for this example
    uvicorn.run(http_stateless_app, host="0.0.0.0", port=8000)