"""
Django management command to run MCP HTTP stateless server.

This command provides an easy way to start the MCP HTTP stateless server
from Django's manage.py interface.
"""

import logging
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from django_mcp.http_stateless_transport import run_http_stateless_mcp_server


class Command(BaseCommand):
    help = 'Run MCP HTTP stateless server integrated with Django'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            type=str,
            default='127.0.0.1',
            help='Host to bind to (default: 127.0.0.1)'
        )
        parser.add_argument(
            '--port',
            type=int,
            default=8000,
            help='Port to listen on (default: 8000)'
        )
        parser.add_argument(
            '--server-name',
            type=str,
            default='django-mcp-http-stateless',
            help='Name of the MCP server (default: django-mcp-http-stateless)'
        )
        parser.add_argument(
            '--mcp-path',
            type=str,
            default='/mcp',
            help='Base path for MCP endpoints (default: /mcp)'
        )
        parser.add_argument(
            '--json-response',
            action='store_true',
            help='Use JSON responses instead of SSE streams'
        )
        parser.add_argument(
            '--log-level',
            type=str,
            default='INFO',
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            help='Logging level (default: INFO)'
        )
        parser.add_argument(
            '--enable-cors',
            action='store_true',
            help='Enable CORS support'
        )
        parser.add_argument(
            '--cors-origins',
            type=str,
            nargs='*',
            default=[],
            help='Allowed CORS origins (space-separated)'
        )
        parser.add_argument(
            '--request-timeout',
            type=float,
            default=30.0,
            help='Request timeout in seconds (default: 30.0)'
        )
        parser.add_argument(
            '--disable-health-check',
            action='store_true',
            help='Disable health check endpoint'
        )

    def handle(self, *args, **options):
        try:
            # Extract options
            host = options['host']
            port = options['port']
            server_name = options['server_name']
            mcp_path = options['mcp_path']
            json_response = options['json_response']
            log_level = options['log_level']
            enable_cors = options['enable_cors']
            cors_origins = options['cors_origins']
            request_timeout = options['request_timeout']
            enable_health_check = not options['disable_health_check']

            # Display startup information
            self.stdout.write(
                self.style.SUCCESS(f'Starting Django MCP HTTP Stateless Server...')
            )
            self.stdout.write(f'Server name: {server_name}')
            self.stdout.write(f'Host: {host}')
            self.stdout.write(f'Port: {port}')
            self.stdout.write(f'MCP path: {mcp_path}')
            self.stdout.write(f'JSON response: {json_response}')
            self.stdout.write(f'CORS enabled: {enable_cors}')
            if enable_cors and cors_origins:
                self.stdout.write(f'CORS origins: {", ".join(cors_origins)}')
            self.stdout.write(f'Request timeout: {request_timeout}s')
            self.stdout.write(f'Health check: {enable_health_check}')
            self.stdout.write(f'Log level: {log_level}')
            self.stdout.write('')
            self.stdout.write(f'MCP endpoint: http://{host}:{port}{mcp_path}')
            if enable_health_check:
                self.stdout.write(f'Health check: http://{host}:{port}{mcp_path}/health')
            self.stdout.write(f'Django app: http://{host}:{port}/')
            self.stdout.write('')

            # Prepare transport kwargs
            transport_kwargs = {
                'cors_enabled': enable_cors,
                'cors_origins': cors_origins,
                'request_timeout': request_timeout,
                'enable_health_check': enable_health_check,
            }

            # Run the server
            run_http_stateless_mcp_server(
                host=host,
                port=port,
                server_name=server_name,
                mcp_base_path=mcp_path,
                json_response=json_response,
                log_level=log_level,
                **transport_kwargs
            )

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.SUCCESS('\nShutdown requested by user')
            )
        except Exception as e:
            raise CommandError(f'Error starting server: {e}')