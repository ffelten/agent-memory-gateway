"""Local HTTP transport for an explicitly supplied real gateway instance."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re

from gateway.service import MAX_BODY


def make_server(gateway, host="127.0.0.1", port=0):
    """Return a server without starting it or selecting a storage/provider backend."""

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # BaseHTTPRequestHandler otherwise logs paths and request metadata.
            pass

        def send_error(self, code, message=None, explain=None):
            self._write(code, {"error": "invalid_request"})

        def _write(self, status, body, headers=None):
            payload = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, allow_nan=False).encode("utf-8")
            self.close_connection = True
            self.send_response(status)
            headers = headers or {"Content-Type": "application/json"}
            for key, value in headers.items():
                if key.lower() not in {"content-length", "connection"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _dispatch(self):
            lengths = self.headers.get_all("Content-Length", [])
            if (
                self.headers.get("Transfer-Encoding") is not None
                or len(lengths) > 1
                or any(len(self.headers.get_all(name, [])) > 1 for name in ("Authorization", "Idempotency-Key"))
                or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0]))
            ):
                self._write(400, {"error": "invalid_request"})
                return
            length = int(lengths[0]) if lengths else 0
            if length > MAX_BODY:
                self._write(413, {"error": "body_too_large"})
                return
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    self._write(400, {"error": "invalid_request"})
                    return
                status, response, headers = gateway.handle(self.command, self.path, dict(self.headers), body)
                self._write(status, response, headers)
            except (BrokenPipeError, ConnectionError):
                return
            except Exception:
                self._write(503, {"error": "dependency_unavailable"})

        do_GET = _dispatch
        do_POST = _dispatch
        do_HEAD = _dispatch
        do_PUT = _dispatch
        do_DELETE = _dispatch
        do_PATCH = _dispatch
        do_OPTIONS = _dispatch

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # Socket failures and malformed requests must not print tracebacks.
            pass

    return Server((host, port), Handler)
