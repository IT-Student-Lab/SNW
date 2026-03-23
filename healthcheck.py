# -*- coding: utf-8 -*-
"""Lightweight HTTP health-check server.

Runs alongside Streamlit on port 8082 and exposes a /health endpoint
that monitoring tools and nginx can hit.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8082
START_TIME = time.time()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            uptime = time.time() - START_TIME
            body = json.dumps(
                {"status": "ok", "uptime_seconds": round(uptime, 1)}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    # Suppress request logs to keep stdout clean
    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"Health check server running on port {PORT}", flush=True)
    server.serve_forever()
