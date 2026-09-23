import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .service import ApiError

LOG = logging.getLogger("runner_controller.http")
RUNNER_ID = re.compile(r"^runner-[a-f0-9]{8}$")


class Handler(BaseHTTPRequestHandler):
    service = None

    def log_message(self, fmt, *args):
        LOG.info("http client=%s message=%s", self.client_address[0], fmt % args)

    def _send(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            raise ApiError(400, "INVALID_JSON", "Request body must be valid JSON") from None

    def _dispatch(self):
        path = self.path.split("?", 1)[0]
        if path == "/health" and self.command == "GET":
            return self._send(200, {"status": "ok"})
        if path == "/runners":
            if self.command == "GET":
                return self._send(200, self.service.list())
            if self.command == "POST":
                return self._send(201, self.service.create(self._body()))
        match = re.fullmatch(r"/runners/([^/]+)(?:/(shutdown))?", path)
        if match:
            runner, action = match.groups()
            if not RUNNER_ID.fullmatch(runner):
                raise ApiError(404, "RUNNER_NOT_FOUND", "Runner not found")
            if action == "shutdown" and self.command == "POST":
                return self._send(200, self.service.shutdown(runner))
            if not action and self.command == "GET":
                return self._send(200, self.service.get(runner))
            if not action and self.command == "DELETE":
                self.service.delete(runner)
                return self._send(204)
        raise ApiError(404, "NOT_FOUND", "Endpoint not found")

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def _handle(self):
        try:
            self._dispatch()
        except ApiError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:
            LOG.error("request failed error_type=%s", type(exc).__name__)
            self._send(500, {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}})


def serve(service, host, port):
    handler = type("RunnerHandler", (Handler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.serve_forever()
