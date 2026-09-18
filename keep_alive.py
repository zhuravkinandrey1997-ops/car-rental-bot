"""Small HTTP health-check server for keeping the bot process reachable."""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TypeAlias


logger = logging.getLogger("car-rental-bot")

KEEP_ALIVE_HOST = "0.0.0.0"
KEEP_ALIVE_PORT = 8080
KEEP_ALIVE_TEXT = "Leon Rental Bot is running!"


class _KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404)
            return

        body = KEEP_ALIVE_TEXT.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


KeepAliveHandle: TypeAlias = tuple[ThreadingHTTPServer, Thread]


def keep_alive(
    host: str = KEEP_ALIVE_HOST,
    port: int = KEEP_ALIVE_PORT,
) -> KeepAliveHandle:
    """Start the health-check server in a daemon thread."""
    server = ThreadingHTTPServer((host, port), _KeepAliveHandler)
    thread = Thread(
        target=server.serve_forever,
        name="keep-alive",
        daemon=True,
    )
    thread.start()
    logger.info("Keep-alive server started on %s:%s", host, port)
    return server, thread


def stop_keep_alive(handle: KeepAliveHandle) -> None:
    """Stop and close a server returned by keep_alive."""
    server, thread = handle
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    logger.info("Keep-alive server stopped")