#!/usr/bin/env python3
"""The state directory's two halves: what the console is told, and what is refused.

`persistence.enabled` decides whether the state directory is a volume or an
emptyDir. The console cannot see which - both answer every write - so the chart
tells it through KT_STATE_PERSISTENT, and with it off the console offers no
archive and no setting kept on that disk. And one combination is refused at
install: persistence off with a password the console would draw for itself,
because every restart would draw a new one.

This renders the chart with `helm template` and asserts both, case by case. It
installs nothing and reaches no cluster.

    python test/persistence-check.py            # the cases
    python test/persistence-check.py --self-test

`--self-test` renders a copy of the chart with the refusal removed and expects
the refusal cases to fail. A check nobody has seen fail is a check nobody knows
is connected.
"""

import os
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
        ["helm", "template", "kt", chart, *args],
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


def state_volume(dep):
    for vol in dep["spec"]["template"]["spec"]["volumes"]:
        if vol["name"] == "state":
            return "pvc" if "persistentVolumeClaim" in vol else ("emptyDir" if "emptyDir" in vol else "?")
    raise AssertionError("no volume named state")


# (name, args, expected) - expected is ("renders", env, volume) or ("refused",)
CASES = [
    ("the defaults: a volume, and the console told so",
     [], ("renders", "true", "pvc")),
    ("persistence off with a hash: an emptyDir, and the console told so",
     [OFF, HASH], ("renders", "false", "emptyDir")),
    ("persistence off with a Secret you made",
     [OFF, "--set=auth.existingSecret=mine"], ("renders", "false", "emptyDir")),
    ("persistence off with single sign-on and no shared password: nothing is drawn",
     [OFF, *OIDC], ("renders", "false", "emptyDir")),
    ("persistence off with a drawn password: refused",
     [OFF], ("refused",)),
    ("persistence off, single sign-on, and the shared password kept but not supplied: refused",
     [OFF, *OIDC, "--set=auth.localAccount=true"], ("refused",)),
]


def run(chart):
    failures = []
    for name, args, expected in CASES:
        code, docs, err = render(chart, args)
        try:
            if expected[0] == "refused":
                if code == 0:
                    raise AssertionError("rendered, and it must be refused at install")
                if "persistence.enabled=false" not in err:
                    raise AssertionError(f"refused, but not by the persistence check: {err.strip()}")
            else:
                if code != 0:
                    raise AssertionError(f"refused: {err.strip()}")
                dep = deployment(docs)
                env, vol = state_env(dep), state_volume(dep)
                if (env, vol) != expected[1:]:
                    raise AssertionError(f"KT_STATE_PERSISTENT={env!r} on a {vol}, want "
                                         f"{expected[1]!r} on a {expected[2]}")
            print(f"ok    {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"FAIL  {name}: {exc}")
    return failures


def self_test():
    """The refusal removed must make exactly the refusal cases fail."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "kubetower")
        shutil.copytree(CHART, copy)
        os.remove(os.path.join(copy, "templates", "check-persistence.yaml"))
        print("self-test: the chart with check-persistence.yaml removed")
        failed = set(run(copy))
    want = {name for name, _, expected in CASES if expected[0] == "refused"}
    if failed != want:
        print(f"self-test FAILED: with the check removed, {sorted(failed)} failed; "
              f"expected exactly {sorted(want)}")
        return 1
    print(f"self-test ok: removing the check fails the {len(want)} refusal cases and nothing else\n")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:] and self_test() != 0:
        sys.exit(1)
    failed = run(CHART)
    if failed:
        print(f"\n{len(failed)} of {len(CASES)} cases failed")
        sys.exit(1)
    print(f"\nall {len(CASES)} cases hold")
