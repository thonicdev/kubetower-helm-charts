"""Refuse a ClusterRole that carries an escalation verb or a stray wildcard.

    python test/rbac-check.py             # check the chart
    python test/rbac-check.py --self-test # prove the check can fail, then check

The role is rendered from charts/kubetower/console-needs.yaml, and
test/console-needs-check.py holds it to exactly that list. This asks what that
cannot: whether a line in the list - added for a page, in good faith - carries
one of the few grants that make every other rule moot.

Three rules, and the third is the one with a real exception in it.

1. **Never `impersonate`, `escalate` or `bind`.** `impersonate` would let the
   console act as anybody and is the whole argument against the console doing
   its own authorisation. `escalate` and `bind` let a subject grant itself
   permissions it does not have, which makes every other line here decorative.

2. **Never `*` in `verbs`.** A wildcard verb on a resource the console only
   reads is a write grant nobody typed.

3. **`*` in `apiGroups` or `resources` only in two rules, each behind its own
   switch and each with its verbs checked.** `rbac.customResources` reads every
   kind, `get` and `list` and nothing else: the custom-resource browser
   genuinely cannot enumerate what it has not been told about, so that wildcard
   is the feature. `rbac.deleteAll` deletes every kind, verbs exactly
   `["delete"]`: the console's Delete takes its kind at run time. Both are off
   by default; any other wildcard, a second one of either shape, or either
   shape with one verb more is refused.

**What it does not check, stated because a green tick invites the opposite
reading.** It passes with every toggle on, and two toggles grant a great deal
without a wildcard or an escalation verb:

- `rbac.write` grants `create` on pods, Secrets and ServiceAccounts in every
  namespace. A pod runs as any ServiceAccount in its namespace, so that is the
  rights of every ServiceAccount in the cluster - cluster-admin on most.
- `rbac.nodeProxy` grants `get` on `nodes/proxy`, which reaches the kubelet's
  own API and allows running commands in the pods on each node.

- `rbac.deleteAll` passes because its wildcard carries `delete` alone, and
  that is still delete on everything in the cluster.

All three are off by default and described in `values.yaml`. This check
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
    "rbac.prometheusProxy=true", "rbac.deleteAll=true",
    # Every extra the console carries, so their own grants are checked too.
    "extras={argocd,kubecron,kubeadjust,redis}",
]


def rules(sets):
    """The ClusterRole's rules, as Helm renders them for these values."""
    out = subprocess.run(
        ["helm", "template", "kubetower", "./charts/kubetower",
         "--show-only", "templates/rbac.yaml",
         # Irrelevant to the role; without it the chart refuses to render
         # while its appVersion is the placeholder.
         "--set", "image.tag=0.0.0-check"]
        + [arg for s in sets for arg in ("--set", s)],
        capture_output=True, text=True, check=True).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc.get("kind") == "ClusterRole":
            return doc.get("rules") or []
    raise SystemExit("no ClusterRole was rendered - the check has nothing to look at")


# The wildcard rules a switch may add: the verbs each may carry, exactly.
READ_WILDCARD = "read"      # rbac.customResources: a subset of get, list, watch
DELETE_WILDCARD = "delete"  # rbac.deleteAll: delete, and nothing else


def complaints(rules, wildcards_allowed):
    """Every reason to refuse these rules. Empty means the role is acceptable.

    wildcards_allowed is the set of wildcard rules the toggles asked for:
    READ_WILDCARD, DELETE_WILDCARD, both or neither. True means both."""
    if wildcards_allowed is True:
        wildcards_allowed = {READ_WILDCARD, DELETE_WILDCARD}
    allowed = set(wildcards_allowed or ())
    found = []
    seen = {READ_WILDCARD: 0, DELETE_WILDCARD: 0}

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
            if verbs and verbs <= READ_ONLY:
                shape = READ_WILDCARD
            elif verbs == {"delete"}:
                shape = DELETE_WILDCARD
            else:
                found.append(
                    f"wildcard on {where} with verbs {sorted(verbs)} - a wildcard may only read "
                    "(rbac.customResources) or only delete (rbac.deleteAll)")
                continue
            seen[shape] += 1
            if shape not in allowed:
                found.append(f"wildcard on {where} with verbs {sorted(verbs)}, and no toggle asked for one")

    for shape, count in seen.items():
        if count > 1:
            found.append(f"{count} rules carry a {shape} wildcard; at most one may")
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
        {"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete"]},
    ]
    found = complaints(bad, wildcards_allowed=set())
    if len(found) != 4:
        raise SystemExit(f"the check did not catch a deliberately bad role: {found}")
    print("ok    self-test - a deliberate role is refused for 4 reasons:")
    for line in found:
        print(f"        {line}")
    # And the same role passes nothing merely by turning the toggles on.
    if not complaints(bad, wildcards_allowed=True):
        raise SystemExit("the toggles excused a role they must not excuse")
    # A wildcard with delete and one verb more is neither permitted shape,
    # whatever the toggles say; nor is a second wildcard delete.
    for label, role in [
        ("a wildcard with delete and patch", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete", "patch"]}]),
        ("a wildcard with delete and get", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete", "get"]}]),
        ("a wildcard with create", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["create"]}]),
        ("two wildcard deletes", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete"]}] * 2),
        ("a delete wildcard under the read toggle only", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete"]}]),
    ]:
        allowed = {READ_WILDCARD} if "read toggle" in label else True
        if not complaints(role, wildcards_allowed=allowed):
            raise SystemExit(f"the check accepted {label}")
        print(f"ok    self-test - refused {label}")
    # And the two permitted shapes, each under its own toggle, pass.
    good = [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["get", "list"]},
            {"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete"]}]
    if complaints(good, wildcards_allowed=True):
        raise SystemExit(f"the check refused the two permitted wildcards: {complaints(good, True)}")
    print()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    passed = [
        check("defaults - no wildcard is acceptable at all", [], wildcards_allowed=set()),
        check("every toggle on, including rbac.customResources and rbac.deleteAll", EVERYTHING_ON,
              wildcards_allowed={READ_WILDCARD, DELETE_WILDCARD}),
        check("every toggle on except rbac.customResources",
              [s for s in EVERYTHING_ON if not s.startswith("rbac.customResources")],
              wildcards_allowed={DELETE_WILDCARD}),
        check("every toggle on except rbac.deleteAll",
              [s for s in EVERYTHING_ON if not s.startswith("rbac.deleteAll")],
              wildcards_allowed={READ_WILDCARD}),
        check("every toggle on except both wildcard toggles",
              [s for s in EVERYTHING_ON if not s.startswith(("rbac.customResources", "rbac.deleteAll"))],
              wildcards_allowed=set()),
    ]
    sys.exit(0 if all(passed) else 1)
