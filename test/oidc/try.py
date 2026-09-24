"""A throwaway OIDC client, so the Dex fixture can be tried in a browser.

It is not a KubeTower component and never will be. The console has no OIDC code
yet; this exists so the question that work has to answer - *what does a real
id_token from a real issuer actually carry* - has a measured answer instead of an
assumed one.

    kubectl --context docker-desktop -n dex port-forward svc/dex 5556:5556
    python test/oidc/try.py          # then open http://localhost:5555

Pick **Mock** on Dex's login screen to see a `groups` claim, or **Email**
(alice@example.com / password) to see that the static password DB carries none.
That difference is the finding.
"""

import base64
import http.server
import json
import secrets
import urllib.parse
import urllib.request
import webbrowser

ISSUER = "http://localhost:5556/dex"
CLIENT_ID = "kubetower"
CLIENT_SECRET = "kubetower-dev-secret"
REDIRECT = "http://localhost:5555/callback"
# `groups` is a Dex scope rather than a standard one: without it the claim is
# absent even from a connector that has the groups to emit.
SCOPE = "openid profile email groups"

state = secrets.token_urlsafe(16)


def claims(jwt):
    """The payload, unverified. Verifying it is the console's job, not this script's."""
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            return self.redirect(ISSUER + "/auth?" + urllib.parse.urlencode({
                "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                "response_type": "code", "scope": SCOPE, "state": state,
            }))
        if url.path != "/callback":
            return self.send_error(404)

        query = urllib.parse.parse_qs(url.query)
        if query.get("state", [""])[0] != state:
            return self.page("state mismatch - the response is not from the request this process made")
        if "code" not in query:
            return self.page("no code: " + url.query)

        token = self.exchange(query["code"][0])
        body = ["access_token and id_token received.", ""]
        body.append("=== id_token claims ===")
        body.append(json.dumps(claims(token["id_token"]), indent=2, sort_keys=True))
        body.append("")
        got = claims(token["id_token"]).get("groups")
        body.append("groups: " + (json.dumps(got) if got else
                                  "ABSENT - this connector carries none. Try the other one."))
        self.page("\n".join(body))

    def exchange(self, code):
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        }).encode()
        with urllib.request.urlopen(ISSUER + "/token", data) as response:
            return json.load(response)

    def redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.end_headers()

    def page(self, text):
        out = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("http://localhost:5555  ->  " + ISSUER)
    webbrowser.open("http://localhost:5555")
    # Threading, and it is not a refinement. A browser holds its connection open
    # between requests, so the single-threaded HTTPServer stops answering
    # everything else the moment a tab is pointed at it - which looked exactly
    # like the fixture being down, measured 2026-09-22.
    http.server.ThreadingHTTPServer(("127.0.0.1", 5555), Handler).serve_forever()
