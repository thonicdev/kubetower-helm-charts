"""Refuse a ClusterRole that does not grant what the console asks for.

    python test/console-needs-check.py             # check the chart
    python test/console-needs-check.py --self-test # prove the check can fail, then check
    python test/console-needs-check.py --chart DIR # check another copy of the chart

test/rbac-check.py asks whether the role grants something dangerous. This asks
the opposite question: whether it grants what the console needs. A control the
console offers and its ServiceAccount cannot perform answers 403 for every
person, whatever their own RoleBindings say - the ServiceAccount is the
ceiling.

test/console-needs.yaml lists each request, the switch meant to grant it, and
the source file in thonicdev/kubetower that makes it. Two assertions:

1. **With its switch on, every request is granted.** The role is rendered with
   that switch set to true - and rbac.read with it, since every switch here
   sits beside the read rules - and each of the request's verbs must be matched
   by a rule naming its API group and its resource, subresource included:
   `pods` does not grant `pods/eviction`.

2. **With the defaults, nothing behind an off-by-default switch is granted.**
   Otherwise a switch documented as off would be on in all but name.

Kept by hand, so it catches a grant that goes missing, not a request nobody
added to the list.
"""

import argparse
import subprocess
import sys

import yaml

NEEDS = "test/console-needs.yaml"

# The switches values.yaml leaves off. Their requests must not be granted by
# the defaults.
OFF_BY_DEFAULT = {
    "rbac.write", "rbac.exec", "rbac.nodeProxy", "rbac.prometheusProxy",
    "rbac.customResources",
}

# The chart refuses to render without an image it can name; the tag is
# irrelevant to the role.
BASE = ["image.tag=0.0.0-check"]


def rules(chart, sets):
    """The ClusterRole's rules, as Helm renders them for these values."""
    out = subprocess.run(
        ["helm", "template", "kubetower", chart, "--show-only", "templates/rbac.yaml"]
        + [arg for s in BASE + sets for arg in ("--set", s)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"helm template failed with {sets}:\n{out.stderr}")
    for doc in yaml.safe_load_all(out.stdout):
        if doc and doc.get("kind") == "ClusterRole":
            return doc.get("rules") or []
    raise SystemExit("no ClusterRole was rendered - the check has nothing to look at")


def grants(role, group, resource, verb):
    """Whether any rule grants verb on group/resource. resourceNames narrow a
    rule to named objects, which a list or a create cannot be, so a rule
    carrying them is not counted."""
    for rule in role:
        if rule.get("resourceNames"):
            continue
        groups = rule.get("apiGroups") or []
        resources = rule.get("resources") or []
        verbs = rule.get("verbs") or []
        if (group in groups or "*" in groups) \
                and (resource in resources or "*" in resources) \
                and (verb in verbs or "*" in verbs):
            return True
    return False


def missing(role, needs):
    """Every (need, verb) the role does not grant."""
    return [(n, v) for n in needs for v in n["verbs"]
            if not grants(role, n["group"], n["resource"], v)]


def present(role, needs):
    """Every (need, verb) the role grants."""
    return [(n, v) for n in needs for v in n["verbs"]
            if grants(role, n["group"], n["resource"], v)]


def name(need, verb):
    where = f"{need['group']}/{need['resource']}" if need["group"] else need["resource"]
    return f"{verb} {where}  ({need['switch']}; {need['source']})"


def check(chart, needs):
    failures = []
    switches = sorted({n["switch"] for n in needs})
    for switch in switches:
        mine = [n for n in needs if n["switch"] == switch]
        sets = [] if switch == "always" else ["rbac.read=true", f"{switch}=true"]
        gone = missing(rules(chart, sets), mine)
        total = sum(len(n["verbs"]) for n in mine)
        print(f"{'FAIL' if gone else 'ok  '}  {switch} on - {total - len(gone)} of {total} requests granted")
        for need, verb in gone:
            print(f"        not granted: {name(need, verb)}")
        failures += gone

    defaults = rules(chart, [])
    off = [n for n in needs if n["switch"] in OFF_BY_DEFAULT]
    leaked = present(defaults, off)
    print(f"{'FAIL' if leaked else 'ok  '}  defaults - none of {len(off)} off-by-default requests granted")
    for need, verb in leaked:
        print(f"        granted while its switch is off: {name(need, verb)}")
    return not failures and not leaked


def self_test():
    """Prove the matcher can say no, rather than assuming a green tick means anything."""
    need = {"switch": "rbac.write", "group": "", "resource": "pods/eviction",
            "verbs": ["create"], "source": "self-test"}
    near_misses = [
        [{"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]}],          # not the subresource
        [{"apiGroups": ["policy"], "resources": ["pods/eviction"], "verbs": ["create"]}],  # wrong group
        [{"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["get"]}],    # wrong verb
        [{"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["create"],
          "resourceNames": ["one"]}],                                                # narrowed
        [],                                                                          # nothing
    ]
    for role in near_misses:
        if not missing(role, [need]):
            raise SystemExit(f"the check accepted a role that does not grant create pods/eviction: {role}")
    if missing([{"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["create"]}], [need]):
        raise SystemExit("the check refused a role that grants exactly what is needed")
    print(f"ok    self-test - {len(near_misses)} near misses refused, the exact grant accepted")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--chart", default="./charts/kubetower")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    with open(NEEDS, encoding="utf-8") as f:
        needs = yaml.safe_load(f)["needs"]
    if not needs:
        raise SystemExit(f"{NEEDS} lists nothing - there is nothing to check")
    sys.exit(0 if check(args.chart, needs) else 1)
