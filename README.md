# django-mcp

django-mcp adds MCP tool hosting to Django.

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) specification is relatively new and has been changing rapidly. This library provides an abstraction layer between Django and the upstream [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) as well as utility functions and decorators to simplify development of MCP services in Django applications.

## Installation

Available on PyPI:

```bash
pip install django-mcp
```

Add `'django_mcp'` to your `INSTALLED_APPS` setting like this:

```python
# settings.py
INSTALLED_APPS = [
    ...
    'django_mcp',
]
```

## Usage

To use this library, you need to mount the MCP ASGI application to a route in your existing Django ASGI application.

### ASGI setup

First, configure your Django ASGI application entrypoint `asgi.py`. Use `mount_mcp_server` to mount the MCP server using Django-style URL path parameters. These URL path parameters will be available in the MCP [Context](https://github.com/modelcontextprotocol/python-sdk/blob/58b989c0a3516597576cd3025a45d194578135bd/README.md#context) object to any `@mcp.tool` decorated functions.

```python
# asgi.py
import os
import django
from django.core.asgi import get_asgi_application

# new import
from django_mcp import mount_mcp_server

# configure settings module path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'my_project.settings')

# initialize django
django.setup()

# get the django http application
django_http_app = get_asgi_application()

# Mount MCP server dynamically using a URL parameter (e.g., user_uuid)
application = mount_mcp_server(django_http_app=django_http_app, mcp_base_path='/mcp/<slug:user_uuid>')

# for django-channels ASGI:
# from channels.routing import ProtocolTypeRouter
# application = ProtocolTypeRouter({
#     "http": mount_mcp_server(django_http_app=django_http_app, mcp_base_path='/mcp/<slug:user_uuid>')
# })
```

Alternatively, if you don't need dynamic mounting, you can provide a static path:

```python
# Simpler setup with a static path
application = mount_mcp_server(django_http_app=django_http_app, mcp_base_path='/mcp')
```

To start your server:

```bash
uvicorn my_project.asgi:application --host 0.0.0.0 --port 8000
```

Now the `mcp_app` FastMCP object can be accessed in your project files with the same interface as defined in the upstream [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) SDK.

### MCP decorators

This library exports `mcp_app` which corresponds to the upstream [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) `FastMCP` object instance. You can use any of the upstream API decorators like `@mcp_app.tool` to define your tools, prompts, resources, etc.

```python
from django_mcp import mcp_app as mcp

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"
```

As shown in the [ASGI setup](#asgi-setup) section, `mount_mcp_server` allows you to define Django-style URL path parameters (e.g., `/mcp/<slug:user_uuid>`). These parameters are captured and made available within your tool functions via the `path_params` attribute on the `Context` object.

```python
# Example demonstrating access to path parameters (e.g., user_uuid from /mcp/<slug:user_uuid>)
from mcp.server.fastmcp import Context
# Assuming User model exists
# from my_app.models import User

@mcp.tool()
async def get_user_info_from_path(ctx: Context) -> str:
    """Retrieves user info based on user_uuid from the URL path."""
    # Access path parameters captured during mounting
    path_params = getattr(ctx, 'path_params', {})
    user_uuid = path_params.get('user_uuid', None)

    if not user_uuid:
        await ctx.error("User UUID not found in path parameters.")
        return "Error: User UUID missing."

    # Example: Fetch user from database (requires async ORM or sync_to_async)
    # try:
    #     user = await User.objects.aget(uuid=user_uuid)
    #     return f"User found: {user.username}"
    # except User.DoesNotExist:
    #     await ctx.warning(f"User with UUID {user_uuid} not found.")
    #     return f"Error: User {user_uuid} not found."

    # Simplified example returning the uuid
    await ctx.info(f"Retrieved user_uuid: {user_uuid}")
    return f"Retrieved user_uuid: {user_uuid}"

```

## Configuration

This library allows customization through Django settings. The following settings can be defined in your project's `settings.py`:

| key                          | description                                             | default                |
|------------------------------|---------------------------------------------------------|------------------------|
| `MCP_LOG_LEVEL`              | Controls the MCP logging level                          | `'INFO'`               |
| `MCP_LOG_TOOL_REGISTRATION`  | Controls whether tool registration is logged at startup | `True`                 |
| `MCP_LOG_TOOL_DESCRIPTIONS`  | Controls whether tool descriptions are also logged      | `False`                |
| `MCP_SERVER_INSTRUCTIONS`    | Sets the instructions provided by the MCP server        | `'Provides MCP tools'` |
| `MCP_SERVER_TITLE`           | Sets the title of the MCP server                        | `'MCP Server'`         |
| `MCP_SERVER_VERSION`         | Sets the version of the MCP server                      | `'0.1.0'`              |
| `MCP_DIRS`                   | Additional search paths to load MCP modules             | `[]`                   |
| `MCP_PATCH_SDK_TOOL_LOGGING` | Adds debug and exception logging to @tool decorator     | `True`                 |
| `MCP_PATCH_SDK_GET_CONTEXT`  | Adds URL path parameters to @tool Context object        | `True`                 |

If a setting is not found in your project's `settings.py`, the default value will be used.

## Server-side Tool Logging

`django-mcp` provides several ways to log the execution of your MCP tools:

1.  **Automatic Logging (Default):**
    By default, `django-mcp` automatically applies basic logging to all functions decorated with `@mcp_app.tool()`. This is controlled by the `settings.MCP_PATCH_SDK_TOOL_LOGGING` setting which defaults to `True`. This logging includes:
    *   DEBUG level message upon tool entry.
    *   DEBUG level message upon successful tool exit, including the return value.
    *   WARNING level message if the tool raises an exception, including the exception details and traceback.

It's important to note that the standard `@tool` decorator provided by the underlying `mcp-python-sdk` does *not*, by itself, log exceptions raised within the tool function to the server's standard output or error streams. Exceptions are typically just passed back to the client. However, when `settings.MCP_PATCH_SDK_TOOL_LOGGING` is enabled in `django-mcp` (the default), the enhanced decorator applied by `django-mcp` *does* intercept these exceptions, logs the details and traceback to the configured Django logger (visible in the server's console/stderr) at the WARNING level, and then allows the error to be passed back to the client as usual.

2.  **Manual Decorator Logging:**
    If you disable `settings.MCP_PATCH_SDK_TOOL_LOGGING = False` in your Django settings, you can still apply the same logging behavior to specific tools using the `@log_mcp_tool_calls` decorator. This decorator must be placed *below* the `@mcp_app.tool()` decorator:

    ```python
    from django_mcp import mcp_app as mcp
    from django_mcp.decorators import log_mcp_tool_calls  # Import the decorator

    @mcp.tool()
    @log_mcp_tool_calls  # Apply below @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b
    ```

## Using MCP python-sdk Context

For more control over client-side logging *within* your tool's execution, or to report progress, you can request the `Context` object provided by the underlying MCP `python-sdk`. Add a parameter type-hinted as `Context` to your tool function's signature. The `Context` object provides methods like `ctx.info()`, `ctx.debug()`, `ctx.warning()`, `ctx.error()`, and `ctx.report_progress()` which send structured messages back to the MCP client.

    ```python
    from django_mcp import mcp_app
    from mcp.server.fastmcp import Context  # Import Context from the SDK
    import asyncio # Added missing import for example

    @mcp_app.tool()
    async def a_very_long_task(input_data: str, ctx: Context):
        await ctx.info("Starting a long task task...")

        for i in range(10):
            await ctx.report_progress(i*10, 100)  # Report progress
            await asyncio.sleep(3)  # Sleep for 3 seconds
            await ctx.info('... doing work...')

        await ctx.info("Long task finished.")
        return "Success"
    ```

## Asynchronous Django ORM

When writing asynchronous MCP tools (using `async def`) that interact with the Django ORM (version 4.1 or later), you should use the native asynchronous ORM methods provided by Django. For example, use `await YourModel.objects.aget(pk=...)` instead of `YourModel.objects.get(pk=...)`, and `await YourModel.objects.acreate(...)` instead of `YourModel.objects.create(...)`. Refer to the [official Django documentation on asynchronous support](https://docs.djangoproject.com/en/stable/topics/async/) for a complete list of async ORM methods and usage details.

For synchronous functions or operations that need to be called from an asynchronous context (like interacting with synchronous third-party libraries or performing operations that require a synchronous transaction block), use the `sync_to_async` adapter from `asgiref.sync`. For example:

```python
from asgiref.sync import sync_to_async
from django_mcp import mcp_app
# Assuming Counter is a Django model with a sync method 'increment_sync'
from .models import Counter

@mcp_app.tool()
async def increment_counter_tool(counter_id: int) -> None:
    """Increment a counter, demonstrating sync_to_async"""
    try:
        counter = await Counter.objects.aget(pk=counter_id)
        # Assume counter.increment_sync() is a synchronous method
        await sync_to_async(counter.increment_sync)()
    except Counter.DoesNotExist:
        # Handle error appropriately
        pass
```

---

## MCP Inspector

This library includes a convenient management command to run the MCP Inspector tool against your Django application.

Start the inspector by running the following command in your project's root directory (where `manage.py` is located):

```bash
python manage.py mcp_inspector [url]
```

Replace `[url]` with the URL of your running MCP server, typically `http://localhost:8000/mcp/sse`. If you omit the URL, it defaults to `http://127.0.0.1:8000/mcp/sse`.

The command will start the inspector and output the URL (usually `http://127.0.0.1:6274`) where you can access it in your web browser.

---

## Session Caching and Re-initialization

MCP clients often assume persistent session state and do not resend initialization handshakes after disconnects. However, the upstream MCP SDK stores session state in RAM, which is lost on server restarts, redeployments, or when routing changes across load-balanced instances.

To work around this, `django-mcp` caches the client's initialization (`initialize` and `notifications/initialized`) messages and replays them transparently when a client reconnects. This ensures server-side session objects are restored to an initialized state, preventing common errors like:

```python
RuntimeError: Received request before initialization was complete
```

This behavior is implemented in `django_mcp/mcp_sdk_session_replay.py` and ensures a smoother experience with clients that do not reinitialize automatically.

## Server Affinity in Production Deployments

For the same foregoing reason that MCP sessions are persisted in RAM, you must implement *server affinity* in any load-balanced environment so that clients always connect to the same Django node. This ensures that any client with an MCP `session_id` sends successive requests to the same Django node with its session state in RAM. A common pattern is to inspect the `X-Forwarded-For` header in your load balancer to retrieve the client's WAN IP and route to specific load-balanced nodes based on this.

```
# haproxy.cfg

frontend http_in
    # ...

    # extract the client WAN ip
    # use `X-Forwarded-For` or `src` if not behind another load balancer
    http-request set-var(req.client_wan_ip) hdr_ip(X-Forwarded-For)
    http-request set-header X-Sticky-Identifier %[var(req.client_wan_ip)]

    use_backend django_cluster

backend django_cluster
    # choose a backend node based on hash of client WAN ip
    balance hdr(X-Sticky-Identifier)
    hash-type consistent

    # persist backend node stickiness for 60 minutes
    stick-table type string size 1m expire 60m
    stick on req.fhdr(X-Sticky-Identifier)

    # ...
```

In this example, the haproxy `stick-table` entry should assure successive requests are sent to the same Django node when scaling up or scaling down.

Clients already connected to a Django container that is being terminated may have a period of time when connections are draining where the MCP client remains connected to `/mcp/sse` but new messages posted to `/mcp/messages/` are routed to a different load-balanced Django container. This can be mitigated by passing `--timeout-graceful-shutdown 0` to `uvicorn`.

## Future roadmap

* Streamable HTTP transport
* Authentication and authorization

---

## Development

```bash
# Set up virtualenv (replace path)
export VIRTUAL_ENV=./.venv/django-mcp
uv venv --python 3.8 --link-mode copy ${VIRTUAL_ENV}
uv sync
```

---

## License

This project is licensed un the MIT License.

By submitting a pull request, you agree that any contributions will be licensed under the MIT License, unless explicitly stated otherwise.
