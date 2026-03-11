"""
Local HTTP server for the Plaid Link browser flow.

This module provides a minimal stdlib HTTP server that serves the Plaid Link
HTML page and receives the public token callback from the browser.  No
external dependencies beyond Python's stdlib are required.
"""

from __future__ import annotations

import http.server
import json
import threading

LINK_SERVER_HOST: str = "127.0.0.1"
LINK_SERVER_PORT: int = 18790

# Inline HTML template. __LINK_TOKEN__ is replaced at runtime with the
# JSON-encoded link token before the page is served.
_HTML_TEMPLATE: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Plaid Link</title>
  <style>
    body { font-family: sans-serif; max-width: 480px; margin: 4rem auto; }
    #status { padding: 1rem; border-radius: 4px; background: #f4f4f4; }
  </style>
</head>
<body>
  <h2>Connecting your institution&hellip;</h2>
  <p id="status">Initializing Plaid Link &mdash; please wait.</p>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
    var handler = Plaid.create({
      token: __LINK_TOKEN__,
      onSuccess: function(public_token, metadata) {
        document.getElementById('status').textContent =
          'Link complete \u2014 exchanging token, please wait\u2026';
        fetch('/callback', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({public_token: public_token})
        }).then(function() {
          document.getElementById('status').textContent =
            '\u2705 Done! You may close this tab and return to the terminal.';
        }).catch(function(err) {
          document.getElementById('status').textContent = 'Error: ' + err;
        });
      },
      onExit: function(err, metadata) {
        if (err) {
          document.getElementById('status').textContent =
            'Link exited with error: ' + JSON.stringify(err);
        } else {
          document.getElementById('status').textContent =
            'Link closed. Return to the terminal.';
        }
      }
    });
    handler.open();
  </script>
</body>
</html>
"""


def start_link_server(
    link_token: str,
    *,
    port: int = LINK_SERVER_PORT,
) -> tuple[http.server.HTTPServer, threading.Event, list[str]]:
    """
    Bind a local HTTP server for the Plaid Link flow and return controls.

    Returns a 3-tuple of:
    - ``server`` — the running :class:`http.server.HTTPServer` instance.
      Call ``server.shutdown()`` to stop it.
    - ``done_event`` — a :class:`threading.Event` that is set when the
      ``/callback`` endpoint receives a valid public token.
    - ``result`` — a list that will contain exactly one element (the
      ``public_token`` string) after ``done_event`` is set.

    The server is started in a daemon thread and is bound to ``host`` on
    ``port``.  Use ``port=0`` to let the OS pick a free port (useful in
    tests).
    """
    result: list[str] = []
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        """Request handler for the local Plaid Link server."""

        def do_GET(self) -> None:
            """Serve the Plaid Link HTML page on GET /."""
            if self.path != "/":
                self.send_response(http.HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            html = _HTML_TEMPLATE.replace(
                "__LINK_TOKEN__", json.dumps(link_token)
            )
            body = html.encode("utf-8")
            self.send_response(http.HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            """Receive the public_token from the browser on POST /callback."""
            if self.path != "/callback":
                self.send_response(http.HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            length_raw = self.headers.get("Content-Length") or "0"
            length = int(str(length_raw))
            raw = self.rfile.read(length)
            data: dict[str, str] = json.loads(raw)
            result.append(data["public_token"])
            response_body = b'{"status":"ok"}'
            self.send_response(http.HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            done.set()

        def log_request(
            self, code: int | str = "-", size: int | str = "-"
        ) -> None:
            """Suppress per-request access log lines."""

    server = http.server.HTTPServer((LINK_SERVER_HOST, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, done, result
