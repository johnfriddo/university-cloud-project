"""Un server HTTP minimo per la salute, in un servizio che non riceve traffico HTTP.

Stessa ragione del worker: questo servizio consuma eventi da un exchange
fanout, quindi la porta HTTP esiste soltanto per le sonde del kubelet.
"""

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

ReadinessFn = Callable[[], dict]


def start_health_server(port: int, service: str, version: str, readiness: ReadinessFn):
    handler = _make_handler(service, version, readiness)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    log.info("health server in ascolto sulla porta %s", port)
    return server


def _make_handler(service: str, version: str, readiness: ReadinessFn):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path == "/health/live":
                self._respond(200, {"status": "ok", "service": service, "version": version})
            elif self.path == "/health/ready":
                checks = readiness()
                everything_ok = all(check["ok"] for check in checks.values())
                self._respond(
                    200 if everything_ok else 503,
                    {
                        "status": "ready" if everything_ok else "not-ready",
                        "service": service,
                        "checks": checks,
                    },
                )
            else:
                self._respond(404, {"error": "not found"})

        def _respond(self, code: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler
