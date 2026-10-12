"""A git HTTP server that answers only with failures, for publish-chart.sh's tests.

    python test/git-http-fixture.py <port>

Under /auth/ it answers 401 with a Basic challenge, which is what a private
repository answers a client holding no credential: git then says "could not
read Username ... terminal prompts disabled", the one failure
publish-chart.sh lets through with a warning. Under /forbidden/ it answers
403, which is GitHub refusing for another reason than a missing credential.
Anything else answers 500. Both stand for every failure that must be refused
- in the same class as a name that does not resolve, a rate limit or a
timeout.

It serves no repository at all, binds 127.0.0.1 only, and runs until killed.
"""
import http.server
import sys


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/auth/"):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
        elif self.path.startswith("/forbidden/"):
            self.send_response(403)
        else:
            self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
