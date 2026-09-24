"""Refuse a ClusterRole that carries an escalation verb or a stray wildcard.

    python test/rbac-check.py             # check the chart
    python test/rbac-check.py --self-test # prove the check can fail, then check

A hand-written ClusterRole drifts the first time a page is added, and it drifts
towards a `*`. This is the part of that problem which can be asserted
mechanically: not *is every rule needed* - nothing here can know that - but
*does any rule carry one of the few grants that make every other rule moot*.

Three rules, and the third is the one with a real exception in it.

1. **Never `impersonate`, `escalate` or `bind`.** `impersonate` would let the
   console act as anybody and is the whole argument against the console doing
   its own authorisation. `escalate` and `bind` let a subject grant itself
   permissions it does not have, which makes every other line here decorative.

2. **Never `*` in `verbs`.** A wildcard verb on a resource the console only
   reads is a write grant nobody typed.

3. **`*` in `apiGroups` or `resources` only under `rbac.customResources`, and
   only for reading.** The custom-resource browser genuinely cannot enumerate
   what it has not been told about, so that one wildcard is the feature. It is
   off by default, it is the only wildcard permitted, and its verbs are checked.

**What it does not check, stated because a green tick invites the opposite
reading.** It passes with every toggle on, and two toggles grant a great deal
without a wildcard or an escalation verb:

- `rbac.write` grants `create` on pods, Secrets and ServiceAccounts in every
  namespace. A pod runs as any ServiceAccount in its namespace, so that is the
  rights of every ServiceAccount in the cluster - cluster-admin on most.
- `rbac.nodeProxy` grants `get` on `nodes/proxy`, which reaches the kubelet's
  own API and allows running commands in the pods on each node.

Both are off by default and both are described in `values.yaml`. This check
says neither was widened by a wildcard; it does not say they are safe.
"""

import subprocess
import sys

import yaml

READ_ONLY = {"get", "list", "watch"}
NEVER = {"impersonate", "escalate", "bind"}

# Every toggle on, because a check that only ever sees the defaults never sees
# the rules the toggles add - which are the dangerous ones.
EVERYTHING_ON = [
    "rbac.write=true", "rbac.exec=true", "rbac.nodeProxy=true",
    "rbac.customResources=true", "rbac.readSecrets=true", "rbac.metrics=true",
]


def rules(sets):
    """The ClusterRole's rules, as Helm renders them for these values."""
    out = subprocess.run(
        ["helm", "template", "kubetower", "./charts/kubetower",
         "--show-only", "templates/rbac.yaml"]
        + [arg for s in sets for arg in ("--set", s)],
        capture_output=True, text=True, check=True).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc.get("kind") == "ClusterRole":
            return doc.get("rules") or []
    raise SystemExit("no ClusterRole was rendered - the check has nothing to look at")


def complaints(rules, wildcards_allowed):
    """Every reason to refuse these rules. Empty means the role is acceptable."""
    found = []
    wildcarded = 0

    for rule in rules:
        verbs = set(rule.get("verbs") or [])
        resources = rule.get("resources") or []
        groups = rule.get("apiGroups") or []
        where = f"apiGroups={groups} resources={resources}"

        for verb in sorted(verbs & NEVER):
            found.append(f"verb {verb!r} on {where}")
        if "*" in verbs:
            found.append(f"wildcard verb on {where}")

        if "*" in resources or "*" in groups:
            wildcarded += 1
            if not wildcards_allowed:
                found.append(f"wildcard on {where}, and no toggle asked for one")
            elif not verbs <= READ_ONLY:
                found.append(
                    f"wildcard on {where} with verbs {sorted(verbs)} - "
                    "the custom-resource wildcard is for reading only")

    if wildcards_allowed and wildcarded > 1:
        found.append(f"{wildcarded} rules carry a wildcard; only the custom-resource one may")
    return found


def check(name, sets, wildcards_allowed):
    found = complaints(rules(sets), wildcards_allowed)
    print(f"{'FAIL' if found else 'ok  '}  {name}")
    for line in found:
        print(f"        {line}")
    return not found


def self_test():
    """Prove the check can fail, rather than assuming a green tick means anything."""
    bad = [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "impersonate"]},
        {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["*"]},
        {"apiGroups": ["*"], "resources": ["*"], "verbs": ["get"]},
    ]
    found = complaints(bad, wildcards_allowed=False)
    if len(found) != 3:
        raise SystemExit(f"the check did not catch a deliberately bad role: {found}")
    print("ok    self-test - a deliberate role is refused for 3 reasons:")
    for line in found:
        print(f"        {line}")
    # And the same role passes nothing merely by turning the toggle on.
    if not complaints(bad, wildcards_allowed=True):
        raise SystemExit("the toggle excused a role it must not excuse")
    print()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    passed = [
        check("defaults - no wildcard is acceptable at all", [], wildcards_allowed=False),
        check("every toggle on, including rbac.customResources", EVERYTHING_ON, wildcards_allowed=True),
        check("every toggle on except rbac.customResources", 
              [s for s in EVERYTHING_ON if not s.startswith("rbac.customResources")],
              wildcards_allowed=False),
    ]
    sys.exit(0 if all(passed) else 1)
