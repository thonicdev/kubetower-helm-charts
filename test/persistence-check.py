#!/usr/bin/env python3
"""The state directory, and the password that no longer depends on it.

`persistence.enabled` decides whether the state directory is a volume or an
emptyDir. The console cannot see which - both answer every write - so the chart
tells it through KT_STATE_PERSISTENT, and with it off the console offers no
archive and no setting kept on that disk.

The password does not live there any more. Whenever the password route is
offered, the release's Secret carries a bcrypt KT_PASSWORD_HASH - the one in
auth.passwordHash, or the one of the password the chart draws - so the console
never draws a password, never prints one in its log, and a restart on an
emptyDir keeps it. Persistence off with no password configured therefore
renders (it was refused until chart 0.4.0).

This renders the chart with `helm template` and asserts both, case by case. It
installs nothing and reaches no cluster.

    python test/persistence-check.py            # the cases
    python test/persistence-check.py --self-test

`--self-test` renders a copy of the chart whose Secret drops the drawn hash and
expects exactly the cases that rely on it to fail. A check nobody has seen fail
is a check nobody knows is connected.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

CHART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "charts", "kubetower")
HASH = "--set-string=auth.passwordHash=$2a$12$abcdefghijklmnopqrstuuPd4n0ZQ8ipT1ubx0rZBf0W4NGXnGFHK"
OIDC = [
    "--set=auth.oidc.issuer=https://idp.example/dex",
    "--set=auth.oidc.clientID=kubetower",
    "--set=auth.oidc.redirectURL=https://kubetower.example/api/auth/oidc/callback",
]
OFF = "--set=persistence.enabled=false"


def render(chart, args):
    """(returncode, rendered documents or None, stderr)."""
    proc = subprocess.run(
        # The image tag is irrelevant here; without one the chart refuses to
        # render while its appVersion is the placeholder.
        ["helm", "template", "kt", chart, "--set=image.tag=0.0.0-check", *args],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return proc.returncode, None, proc.stderr
    return 0, [d for d in yaml.safe_load_all(proc.stdout) if d], proc.stderr


def deployment(docs):
    found = [d for d in docs if d.get("kind") == "Deployment"]
    if len(found) != 1:
        raise AssertionError(f"expected one Deployment, rendered {len(found)}")
    return found[0]


def state_env(dep):
    env = dep["spec"]["template"]["spec"]["containers"][0].get("env", [])
    values = [e.get("value") for e in env if e.get("name") == "KT_STATE_PERSISTENT"]
    if len(values) != 1:
        raise AssertionError(f"KT_STATE_PERSISTENT appears {len(values)} times, want exactly once")
    return values[0]


def password_hash(docs):
    """KT_PASSWORD_HASH in the release's Secret (kt-kubetower), or None."""
    for d in docs:
        if d.get("kind") == "Secret" and d["metadata"]["name"] == "kt-kubetower":
            return (d.get("stringData") or {}).get("KT_PASSWORD_HASH")
    return None


def state_volume(dep):
    for vol in dep["spec"]["template"]["spec"]["volumes"]:
        if vol["name"] == "state":
            return "pvc" if "persistentVolumeClaim" in vol else ("emptyDir" if "emptyDir" in vol else "?")
    raise AssertionError("no volume named state")


# (name, args, (env, volume, hash)) - hash is "drawn" (a bcrypt hash the
# chart drew), "given" (auth.passwordHash), or None (no KT_PASSWORD_HASH in the
# chart's Secret: single sign-on only, or a Secret you made).
GIVEN = HASH.split("=", 2)[2]
CASES = [
    ("the defaults: a volume, the console told so, and a drawn hash",
     [], ("true", "pvc", "drawn")),
    ("persistence off with a hash: an emptyDir, and the console told so",
     [OFF, HASH], ("false", "emptyDir", "given")),
    ("persistence off with a Secret you made",
     [OFF, "--set=auth.existingSecret=mine"], ("false", "emptyDir", None)),
    ("persistence off with single sign-on and no shared password: nothing is drawn",
     [OFF, *OIDC], ("false", "emptyDir", None)),
    ("persistence off with no password configured: the chart draws it, the console does not",
     [OFF], ("false", "emptyDir", "drawn")),
    ("persistence off, single sign-on, and the shared password kept but not supplied: drawn by the chart",
     [OFF, *OIDC, "--set=auth.localAccount=true"], ("false", "emptyDir", "drawn")),
]


def run(chart):
    failures = []
    for name, args, expected in CASES:
        code, docs, err = render(chart, args)
        try:
            if code != 0:
                raise AssertionError(f"refused: {err.strip()}")
            dep = deployment(docs)
            env, vol = state_env(dep), state_volume(dep)
            if (env, vol) != expected[:2]:
                raise AssertionError(f"KT_STATE_PERSISTENT={env!r} on a {vol}, want "
                                     f"{expected[0]!r} on a {expected[1]}")
            got, want = password_hash(docs), expected[2]
            if want is None and got is not None:
                raise AssertionError(f"KT_PASSWORD_HASH is set, want none")
            if want == "given" and got != GIVEN:
                raise AssertionError(f"KT_PASSWORD_HASH is {got!r}, want auth.passwordHash")
            if want == "drawn" and not (got and re.match(r"^\$2[aby]\$\d\d\$.{53}$", got)):
                raise AssertionError(f"KT_PASSWORD_HASH is {got!r}, want a bcrypt hash the chart drew: "
                                     "without it the console draws the password and prints it in its log")
            print(f"ok    {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"FAIL  {name}: {exc}")
    return failures


def self_test():
    """The drawn hash dropped from the Secret must fail exactly the cases relying on it."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "kubetower")
        shutil.copytree(CHART, copy)
        secret = os.path.join(copy, "templates", "secret.yaml")
        text = open(secret).read()
        broken = re.sub(r"\{\{- else if include \"kubetower.drawsPassword\" \. \}\}\n.*\n", "", text)
        if broken == text:
            print("self-test FAILED: could not find the drawn hash in templates/secret.yaml to remove")
            return 1
        open(secret, "w").write(broken)
        print("self-test: the chart with the drawn hash removed from its Secret")
        failed = set(run(copy))
    want = {name for name, _, expected in CASES if expected[2] == "drawn"}
    if failed != want:
        print(f"self-test FAILED: with the drawn hash removed, {sorted(failed)} failed; "
              f"expected exactly {sorted(want)}")
        return 1
    print(f"self-test ok: removing the drawn hash fails the {len(want)} cases relying on it and nothing else\n")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:] and self_test() != 0:
        sys.exit(1)
    failed = run(CHART)
    if failed:
        print(f"\n{len(failed)} of {len(CASES)} cases failed")
        sys.exit(1)
    print(f"\nall {len(CASES)} cases hold")
