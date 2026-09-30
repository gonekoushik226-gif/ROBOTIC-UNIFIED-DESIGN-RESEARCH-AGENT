"""A loopback web server for Phase 18's tests (ADR 0050 P18-13).

It listens on 127.0.0.1 at an ephemeral port, inside the test process, and serves only the
routes a test gives it. Nothing here, or in any test that uses it, connects beyond this
machine.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: A route: (status, content type, body bytes, extra headers).
Route = tuple[int, str, bytes, dict[str, str]]


class Loopback:
    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the http.server interface
                outer.requests.append((self.path, dict(self.headers)))
                status, content_type, body, headers = outer.routes.get(
                    self.path, (404, "text/plain", b"not found", {}))
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep the test output clean
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> "Loopback":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    """A loopback port nothing listens on: a connection to it is refused."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


MEMRISTOR_PAGE = (
    "<html><head><title>Memristor notes</title><style>p { color: red; }</style>"
    "<script>var memristor = 'not text';</script></head><body>"
    "<h1>Memristor</h1>"
    "<p>A memristor is a passive element with 2 terminals.</p>"
    "<p>Chua proposed the memristor in 1971 as the fourth basic circuit element.</p>"
    "<p>Resistors, capacitors and inductors are the other three.</p>"
    "</body></html>"
).encode("utf-8")


def site_routes(port_placeholder: str = "") -> dict[str, Route]:
    """The routes of the test site: the page, a redirect inside, one outside, and failures."""
    return {
        "/docs/memristor.html": (200, "text/html; charset=utf-8", MEMRISTOR_PAGE, {}),
        "/docs/moved.html": (302, "text/plain", b"", {"Location": "/docs/memristor.html"}),
        "/docs/away.html": (302, "text/plain", b"", {"Location": "/private/secret.html"}),
        "/docs/huge.html": (200, "text/html", b"<p>memristor</p>" + b"x" * (2 * 1024 * 1024), {}),
        "/docs/binary.bin": (200, "application/octet-stream", b"\x00\x01memristor", {}),
        "/docs/unrelated.html": (200, "text/html", b"<p>Capacitors store charge.</p>", {}),
        "/private/secret.html": (200, "text/html", b"<p>A memristor secret.</p>", {}),
    }
