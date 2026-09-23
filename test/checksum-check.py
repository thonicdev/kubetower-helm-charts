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

# annotation on the Deployment's pod template -> the template it hashes
HASHED = {
    "checksum/secret": "templates/secret.yaml",
}


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


def checksums_describe_what_was_rendered(sets):
    docs = documents(render(sets))
    deployment = docs["templates/deployment.yaml"]
    failures = []
    for annotation, template in HASHED.items():
        found = re.search(re.escape(annotation) + r": (\w+)", deployment)
        if not found:
            failures.append(f"{annotation} is missing from the Deployment")
            continue
        # `include` returns the template's text; `helm template` prints the
        # same text after its own `---` separator, without the line break the
        # template opens with and closes on.
        doc = docs[template]
        nl = "\r\n" if "\r\n" in doc else "\n"
        body = nl + doc.rstrip("\r\n") + nl
        if hashlib.sha256(body.encode()).hexdigest() != found.group(1):
            failures.append(f"{annotation} does not hash the {template} rendered beside it")
    return failures


def a_change_rolls_the_pods(name, first, second):
    a = render(first, "templates/deployment.yaml")
    b = render(second, "templates/deployment.yaml")
    return [] if a != b else [f"{name}: the Deployment is byte-identical, so nothing restarts"]


if __name__ == "__main__":
    fixed = ["auth.sessionSecret=fixed-for-the-check-and-32-characters-long"]
    failures = []
    failures += checksums_describe_what_was_rendered([])
    failures += checksums_describe_what_was_rendered(fixed)
    failures += a_change_rolls_the_pods(
        "a new session key",
        ["auth.sessionSecret=" + "1" * 32], ["auth.sessionSecret=" + "2" * 32])
    failures += a_change_rolls_the_pods(
        "a new password hash",
        fixed + ["auth.passwordHash=$2y$12$first"], fixed + ["auth.passwordHash=$2y$12$second"])
    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("ok    every checksum hashes the object rendered beside it, "
              "and a changed credential changes the Deployment")
    sys.exit(1 if failures else 0)
