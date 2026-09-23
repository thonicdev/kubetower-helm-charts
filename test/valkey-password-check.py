"""Valkey accepts exactly the password it was given, whatever it contains.

    python test/valkey-password-check.py      # needs docker; pulls the pinned image

The init container writes `requirepass` into Valkey's configuration from the
Secret. A password supplied through valkey.existingSecret is the operator's
and may contain anything; written naively inside double quotes, a quote ends
the string, a newline starts a directive of its own, and Valkey either refuses
to start or runs with a configuration nobody wrote.

This renders the chart, takes the ConfigMap and the init container's script
exactly as rendered, and runs both in the pinned Valkey image with passwords
chosen to break it. For each it asserts that Valkey started, that the password
authenticates, and that no password does not. It also asserts that an empty
password is refused, because an empty requirepass turns authentication off.

Nothing here reaches a cluster: it is `helm template` and a local container.
"""

import os
import subprocess
import sys

import yaml

CHART = "./charts/kubetower"
SEVERAL = [
    "replicaCount=2", "persistence.enabled=false",
    "auth.oidc.issuer=https://idp.example/dex", "auth.oidc.clientID=kubetower",
    "auth.oidc.redirectURL=https://kubetower.example/api/auth/oidc/callback",
]

PASSWORDS = {
    "letters and digits": "plainAlnum0123456789",
    "a quote, a backslash, a newline, a space, an apostrophe": 'a"b\\c\nd e\'f',
    "a line that tries to add a directive": 'x"\nprotected-mode no\n#',
    "something that looks like an escape": '\\x41\\n"',
}

PROBE = r'''
valkey-server /config/valkey.conf > /tmp/valkey.log 2>&1 &
i=0
until valkey-cli ping 2>&1 | grep -qv 'Could not connect'; do
  i=$((i + 1))
  if [ $i -ge 50 ]; then echo "STARTED:no"; tail -5 /tmp/valkey.log; exit 0; fi
  sleep 0.1
done
echo "STARTED:yes"
echo "AUTHED:$(VALKEYCLI_AUTH="$VALKEY_PASSWORD" valkey-cli ping 2>&1 | tr -d '\n')"
echo "ANON:$(valkey-cli ping 2>&1 | tr -d '\n')"
'''


def rendered():
    out = subprocess.run(
        ["helm", "template", "kubetower", CHART] + [a for s in SEVERAL for a in ("--set", s)],
        capture_output=True, check=True).stdout.decode()
    docs = [d for d in yaml.safe_load_all(out) if d]
    conf = next(d for d in docs if d["kind"] == "ConfigMap"
                and "valkey.conf" in d.get("data", {}))["data"]["valkey.conf"]
    sts = next(d for d in docs if d["kind"] == "StatefulSet")
    init = sts["spec"]["template"]["spec"]["initContainers"][0]
    if init["command"][:2] != ["sh", "-c"]:
        raise SystemExit(f"the init container is no longer `sh -c`: {init['command'][:2]}")
    return conf, init["command"][2], init["image"]


def run(image, script, password):
    env = dict(os.environ, VALKEY_PASSWORD=password)
    # -e NAME with no value passes the variable through from this process's
    # environment, so the password is never on docker's own command line.
    return subprocess.run(
        ["docker", "run", "-i", "--rm", "-e", "VALKEY_PASSWORD", image, "sh", "-s"],
        input=script.encode(), capture_output=True, env=env, timeout=120)


if __name__ == "__main__":
    conf, init, image = rendered()
    setup = ("mkdir -p /defaults /config /data\n"
             "cat > /defaults/valkey.conf <<'__KT_CONF__'\n" + conf + "__KT_CONF__\n")
    failures = []
    for name, password in PASSWORDS.items():
        done = run(image, setup + init + "\n" + PROBE, password)
        out = done.stdout.decode(errors="replace")
        lines = dict(l.split(":", 1) for l in out.splitlines() if ":" in l)
        ok = (done.returncode == 0 and lines.get("STARTED") == "yes"
              and lines.get("AUTHED") == "PONG" and "NOAUTH" in lines.get("ANON", ""))
        print(f"{'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)
            print("        " + (out + done.stderr.decode(errors="replace")).replace("\n", "\n        "))

    empty = run(image, setup + init + "\necho WROTE\n", "")
    refused = empty.returncode != 0 and b"WROTE" not in empty.stdout \
        and b"password is empty" in empty.stderr
    print(f"{'ok  ' if refused else 'FAIL'}  an empty password is refused")
    if not refused:
        failures.append("empty")
    sys.exit(1 if failures else 0)
