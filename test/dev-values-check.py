"""Refuse development values that disagree with the Dex fixture they are for.

    python test/dev-values-check.py              # check dev/values.yaml
    python test/dev-values-check.py --self-test  # prove the check can fail, then check

dev/values.yaml installs the console for development against
test/oidc/dex.yaml. Two files describe one environment, and the ways they
drift apart all look the same in a browser - a sign-in that fails at Dex or
at the callback. So the chart is rendered with the values, and what the pod
would be given is compared with what the fixture serves:

- the issuer is the fixture's, byte for byte, since a token's `iss` must equal it;
- the client is one the fixture registers, with its secret;
- the redirect is one that client lists;
- `groups` is asked for, without which Dex sends no groups claim;
- the shared password is kept beside single sign-on, with a hash, so there is
  a way in when Dex is not running;
- the server profile, and a local image that is never pulled.
"""

import base64
import subprocess
import sys

import yaml

CHART = "./charts/kubetower"
VALUES = "dev/values.yaml"
FIXTURE = "test/oidc/dex.yaml"


def rendered():
    out = subprocess.run(
        ["helm", "template", "kubetower", CHART, "--namespace", "kubetower", "-f", VALUES],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"helm template with {VALUES} failed:\n{out.stderr}")
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    pod = [d for d in docs if d["kind"] == "Deployment"][0]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    secret = {}
    for d in docs:
        if d["kind"] == "Secret":
            secret.update({k: base64.b64decode(v).decode() for k, v in (d.get("data") or {}).items()})
            secret.update(d.get("stringData") or {})
    return container, env, secret


def dex():
    with open(FIXTURE, encoding="utf-8") as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    config = [d for d in docs if d["kind"] == "ConfigMap"][0]["data"]["config.yaml"]
    return yaml.safe_load(config)


def complaints(container, env, secret, config):
    found = []
    if env.get("KT_OIDC_ISSUER") != config["issuer"]:
        found.append(f"the issuer is {env.get('KT_OIDC_ISSUER')!r}, and the fixture's is {config['issuer']!r}")
    clients = {c["id"]: c for c in config.get("staticClients") or []}
    client = clients.get(env.get("KT_OIDC_CLIENT_ID"))
    if client is None:
        found.append(f"the client {env.get('KT_OIDC_CLIENT_ID')!r} is not one the fixture registers ({sorted(clients)})")
    else:
        if env.get("KT_OIDC_CLIENT_SECRET") != client.get("secret"):
            found.append("the client secret is not the fixture's")
        if env.get("KT_OIDC_REDIRECT_URL") not in (client.get("redirectURIs") or []):
            found.append(f"the redirect {env.get('KT_OIDC_REDIRECT_URL')!r} is not one the fixture's client lists")
    if "groups" not in (env.get("KT_OIDC_SCOPES") or "").split(","):
        found.append("the scopes do not ask for groups, so Dex sends no groups claim")
    if env.get("KT_AUTH_LOCAL_ACCOUNT") != "true":
        found.append("the shared password is not kept beside single sign-on")
    if not secret.get("KT_PASSWORD_HASH", "").startswith("$2"):
        found.append("there is no bcrypt password hash, so the shared password would be drawn into a log")
    if "--incluster" not in (container.get("args") or []):
        found.append("the server profile is off")
    if container.get("imagePullPolicy") != "Never" or not container.get("image", "").endswith(":dev"):
        found.append(f"the image is {container.get('image')!r}, pulled {container.get('imagePullPolicy')}, "
                     "and the development image is a local :dev that is never pulled")
    return found


def self_test(container, env, secret, config):
    """Prove each comparison can say no, on the real render with one thing changed."""
    probes = [
        ("issuer", lambda e, s, c, k: c.update(issuer=c["issuer"] + "/")),
        ("client", lambda e, s, c, k: e.update(KT_OIDC_CLIENT_ID="somebody-else")),
        ("secret", lambda e, s, c, k: e.update(KT_OIDC_CLIENT_SECRET="wrong")),
        ("redirect", lambda e, s, c, k: e.update(KT_OIDC_REDIRECT_URL="http://127.0.0.1:9999/api/auth/oidc/callback")),
        ("groups", lambda e, s, c, k: e.update(KT_OIDC_SCOPES="openid,email,profile")),
        ("local account", lambda e, s, c, k: e.update(KT_AUTH_LOCAL_ACCOUNT="false")),
        ("password hash", lambda e, s, c, k: s.pop("KT_PASSWORD_HASH", None)),
        ("profile", lambda e, s, c, k: k.update(args=[a for a in k.get("args", []) if a != "--incluster"])),
        ("image", lambda e, s, c, k: k.update(imagePullPolicy="IfNotPresent")),
    ]
    for label, change in probes:
        e, s, c, k = dict(env), dict(secret), yaml.safe_load(yaml.safe_dump(config)), dict(container)
        change(e, s, c, k)
        if not complaints(k, e, s, c):
            raise SystemExit(f"the check accepted development values with the {label} changed")
    print(f"ok    self-test - {len(probes)} single changes refused")
    print()


if __name__ == "__main__":
    container, env, secret = rendered()
    config = dex()
    if "--self-test" in sys.argv:
        self_test(container, env, secret, config)
    found = complaints(container, env, secret, config)
    print(f"{'FAIL' if found else 'ok  '}  {VALUES} agrees with {FIXTURE}")
    for line in found:
        print(f"        {line}")
    sys.exit(1 if found else 0)
