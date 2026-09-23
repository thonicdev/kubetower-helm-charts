"""A changed credential restarts the pods that read it.

    python test/checksum-check.py

The console reads its password hash and its session key once, at start. A
Secret that changes under a Deployment that does not leaves the old values
running, so the pod template carries a checksum of the Secret, and a changed
Secret is a changed pod template, which Kubernetes rolls.

Two things are checked, because each can be wrong while the other holds:

1. **The checksum describes the Secret that was rendered beside it.** The
   session key is drawn at random when nothing supplies one; a checksum over
   a second, independent draw would change on every render and describe
   nothing. Rendered once, the annotation must equal the hash of the Secret in
   the same output.
2. **Changing a credential changes the Deployment.** Two renders that differ
   only in the session key, or only in the password hash, must not produce
   byte-identical Deployments.
"""

import hashlib
import re
import subprocess
import sys

CHART = "./charts/kubetower"

# (the workload's template, the annotation on its pod template) -> the
# template that annotation hashes. Only the ones a render produces are checked,
# so each configuration below must render every one it is meant to exercise.
HASHED = {
    ("templates/deployment.yaml", "checksum/secret"): "templates/secret.yaml",
    ("templates/deployment.yaml", "checksum/valkey-secret"): "templates/valkey-secret.yaml",
    ("templates/valkey.yaml", "checksum/secret"): "templates/valkey-secret.yaml",
    ("templates/valkey.yaml", "checksum/config"): "templates/valkey-config.yaml",
}

# Several replicas, coherent enough to render: no volume, single sign-on.
SEVERAL = [
    "replicaCount=2", "persistence.enabled=false",
    "auth.oidc.issuer=https://idp.example/dex", "auth.oidc.clientID=kubetower",
    "auth.oidc.redirectURL=https://kubetower.example/api/auth/oidc/callback",
]


def render(sets, only=None):
    cmd = ["helm", "template", "kubetower", CHART]
    cmd += [arg for s in sets for arg in ("--set", s)]
    if only:
        cmd += ["--show-only", only]
    # Bytes, decoded without newline translation: a checkout with CRLF line
    # endings renders CRLF templates, and the hash is over those bytes.
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode()


def documents(out):
    parts = re.split(r"^---\n# Source: [^/\n]+/([^\n]+)\n", out, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def checksums_describe_what_was_rendered(sets, expected):
    """`expected` is how many of HASHED this render must carry."""
    docs = documents(render(sets))
    failures = []
    seen = 0
    for (workload, annotation), template in HASHED.items():
        if workload not in docs:
            continue
        found = re.search(re.escape(annotation) + r": (\w+)", docs[workload])
        if not found:
            continue
        seen += 1
        # `include` returns the template's text; `helm template` prints the
        # same text after its own `---` separator, without the line break the
        # template opens with and closes on.
        # That line break is the template file's own, which on a CRLF checkout
        # differs from the ones an included helper contributes - so it is read
        # from the file, not guessed from the output.
        doc = docs.get(template)
        if doc is None:
            failures.append(f"{workload} {annotation} names {template}, which rendered nothing")
            continue
        with open(f"{CHART}/{template}", "rb") as source:
            nl = "\r\n" if b"\r\n" in source.read() else "\n"
        body = nl + doc.rstrip("\r\n") + nl
        if hashlib.sha256(body.encode()).hexdigest() != found.group(1):
            failures.append(
                f"{workload} {annotation} does not hash the {template} rendered beside it")
    if seen != expected:
        failures.append(f"{seen} checksums rendered with {sets}, expected {expected}")
    return failures


def a_change_rolls_the_pods(name, first, second, workload="templates/deployment.yaml"):
    a = render(first, workload)
    b = render(second, workload)
    return [] if a != b else [f"{name}: {workload} is byte-identical, so nothing restarts"]


if __name__ == "__main__":
    fixed = ["auth.sessionSecret=fixed-for-the-check-and-32-characters-long"]
    failures = []
    failures += checksums_describe_what_was_rendered([], expected=1)
    failures += checksums_describe_what_was_rendered(fixed, expected=1)
    failures += checksums_describe_what_was_rendered(SEVERAL, expected=4)
    failures += a_change_rolls_the_pods(
        "a new session key",
        ["auth.sessionSecret=" + "1" * 32], ["auth.sessionSecret=" + "2" * 32])
    failures += a_change_rolls_the_pods(
        "a new password hash",
        fixed + ["auth.passwordHash=$2y$12$first"], fixed + ["auth.passwordHash=$2y$12$second"])
    # Valkey: any line of its configuration, and the Secret it reads its
    # password from, restart it - and the second restarts the consoles too.
    failures += a_change_rolls_the_pods(
        "a new Valkey memory ceiling",
        SEVERAL + ["valkey.maxmemory=48mb"], SEVERAL + ["valkey.maxmemory=40mb"],
        "templates/valkey.yaml")
    for workload in ("templates/valkey.yaml", "templates/deployment.yaml"):
        failures += a_change_rolls_the_pods(
            "a different Valkey Secret",
            SEVERAL + ["valkey.existingSecret=one"], SEVERAL + ["valkey.existingSecret=two"],
            workload)
    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("ok    every checksum hashes the object rendered beside it, "
              "and a changed credential changes the Deployment")
    sys.exit(1 if failures else 0)
