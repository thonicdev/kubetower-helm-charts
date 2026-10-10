"""Refuse a ClusterRole that grants less, or more, than the console asks for.

    python test/console-needs-check.py              # check the chart
    python test/console-needs-check.py --self-test  # prove the check can fail, then check
    python test/console-needs-check.py --chart DIR  # check another copy of the chart
    python test/console-needs-check.py --chart DIR --needs FILE
                                                    # ...against a given list

test/rbac-check.py asks whether the role grants something dangerous. This asks
whether it grants exactly what the console needs.

charts/kubetower/console-needs.yaml lists each request the console makes, the
switch in values.yaml that grants it, and the source file in thonicdev/kubetower
that makes it. templates/rbac.yaml renders the role from that list. For every
combination of switches below, the role is rendered and compared with the list,
both ways:

1. **Nothing missing.** Every request whose switch is on is granted: each verb
   by a rule naming its API group and its resource, subresource included -
   `pods` does not grant `pods/eviction`. A control the console offers and its
   ServiceAccount cannot perform answers 403 for every person, whatever their
   own RoleBindings say - the ServiceAccount is the ceiling.

2. **Nothing extra.** Every grant the role makes is a request in the list whose
   switch is on. A grant nobody asks for is a permission everybody who signs in
   holds for no reason. A wildcard matches only a wildcard in the list, so the
   custom-resource rule cannot stand in for anything else.

3. **The off-by-default switches are off.** values.yaml leaves them false, and
   the defaults grant none of their requests - otherwise a switch documented as
   off would be on in all but name.

And every switch the list names must be a value under `rbac`, or `always`: the
template refuses an unknown one, and so does this.

Whether the list itself matches the console's source is test/derive-needs.py's
question, not this one's.
"""

import argparse
import os
import subprocess
import sys

import yaml

# The switches values.yaml leaves off. Their requests must not be granted by
# the defaults.
OFF_BY_DEFAULT = {
    "rbac.write", "rbac.exec", "rbac.nodeProxy", "rbac.prometheusProxy",
    "rbac.customResources",
}

# The chart refuses to render without an image it can name; the tag is
# irrelevant to the role.
BASE = ["image.tag=0.0.0-check"]


def render(chart, rbac):
    """The ClusterRole's rules, as Helm renders them with these rbac values."""
    values = yaml.safe_dump({"rbac": rbac})
    out = subprocess.run(
        ["helm", "template", "kubetower", chart, "--show-only", "templates/rbac.yaml",
         "--values", "-"] + [arg for s in BASE for arg in ("--set", s)],
        input=values, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"helm template failed with rbac {rbac}:\n{out.stderr}")
    for doc in yaml.safe_load_all(out.stdout):
        if doc and doc.get("kind") == "ClusterRole":
            return doc.get("rules") or []
    raise SystemExit("no ClusterRole was rendered - the check has nothing to look at")


def granted(role):
    """Every (group, resource, verb, names) the rules grant, as a set. A rule
    carrying resourceNames grants only those names, so the names are part of
    the grant: an unnarrowed request is not met by a narrowed rule."""
    out = set()
    for rule in role:
        names = tuple(rule.get("resourceNames") or ())
        for group in rule.get("apiGroups") or []:
            for resource in rule.get("resources") or []:
                for verb in rule.get("verbs") or []:
                    out.add((group, resource, verb, names))
    return out


def switch_on(switch, rbac):
    """The template's own reading of a switch (kubetower.rbacSwitch)."""
    if switch == "always":
        return True
    key = switch.removeprefix("rbac.")
    if key == switch or key not in rbac:
        raise SystemExit(f"the list names the switch {switch!r}, which is not a value under rbac")
    if key == "readSecrets":
        return bool(rbac["read"]) and bool(rbac["readSecrets"])
    return bool(rbac[key])


def expected(needs, rbac):
    """Every (group, resource, verb, names) the list asks for with these values,
    mapped to the line that asks for it."""
    out = {}
    for need in needs:
        if not switch_on(need["switch"], rbac):
            continue
        names = ()
        if need.get("resourceNamesFrom"):
            names = tuple(rbac.get(need["resourceNamesFrom"]) or ())
        for verb in need["verbs"]:
            out[(need["group"], need["resource"], verb, names)] = need
    return out


def compare(role, needs, rbac):
    """(missing, extra): what the list asks for and the role does not grant,
    and what the role grants that the list does not ask for."""
    want = expected(needs, rbac)
    have = granted(role)
    missing = sorted(((k, v) for k, v in want.items() if k not in have), key=lambda kv: kv[0])
    extra = sorted(have - set(want))
    return missing, extra


def name(grant):
    group, resource, verb, names = grant
    where = f"{group}/{resource}" if group else resource
    return f"{verb} {where}" + (f" named {list(names)}" if names else "")


def combinations(defaults, needs):
    """The switch settings the role is rendered with: the defaults, each switch
    alone beside rbac.read, everything on, everything off, the Secrets switch
    without rbac.read, and the service proxy narrowed."""
    switches = sorted({n["switch"].removeprefix("rbac.") for n in needs} - {"always"})
    out = [("defaults", {})]
    out += [(f"rbac.{s} on", {"read": True, s: True}) for s in switches]
    out += [
        ("every switch on", {s: True for s in switches}),
        ("every switch off", {s: False for s in switches}),
        ("rbac.readSecrets without rbac.read", {"read": False, "readSecrets": True}),
        ("rbac.prometheusProxy narrowed to one Service",
         {"prometheusProxy": True, "prometheusProxyServices": ["prometheus-server:80"]}),
    ]
    return [(label, {**defaults, **sets}) for label, sets in out]


def check(chart, needs):
    with open(os.path.join(chart, "values.yaml"), encoding="utf-8") as f:
        defaults = yaml.safe_load(f)["rbac"]
    ok = True

    for need in needs:
        switch_on(need["switch"], defaults)  # refuses a switch values.yaml does not have

    for label, rbac in combinations(defaults, needs):
        missing, extra = compare(render(chart, rbac), needs, rbac)
        total = len(expected(needs, rbac))
        bad = bool(missing or extra)
        ok = ok and not bad
        print(f"{'FAIL' if bad else 'ok  '}  {label} - {total - len(missing)} of {total} requests granted, "
              f"{len(extra)} grant(s) nobody asks for")
        for grant, need in missing:
            print(f"        not granted: {name(grant)}  ({need['switch']}; {need['source']})")
        for grant in extra:
            print(f"        granted, never asked for: {name(grant)}")

    off = [n for n in needs if n["switch"] in OFF_BY_DEFAULT]
    on_by_default = sorted(s for s in OFF_BY_DEFAULT if defaults.get(s.removeprefix("rbac.")))
    all_on = {**defaults, **{s.removeprefix("rbac."): True for s in OFF_BY_DEFAULT}}
    leaked = granted(render(chart, defaults)) & set(expected(off, all_on))
    bad = bool(on_by_default or leaked)
    ok = ok and not bad
    print(f"{'FAIL' if bad else 'ok  '}  defaults - {len(OFF_BY_DEFAULT)} switches off, "
          f"none of their {len(off)} requests granted")
    for switch in on_by_default:
        print(f"        values.yaml turns on {switch}, which is meant to be off")
    for grant in sorted(leaked):
        print(f"        granted while its switch is off: {name(grant)}")
    return ok


def self_test():
    """Prove the comparison can say no, both ways, rather than assuming a green
    tick means anything."""
    rbac = {"write": True}
    need = {"switch": "rbac.write", "group": "", "resource": "pods/eviction",
            "verbs": ["create"], "source": "self-test"}
    exact = {"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["create"]}

    short = [
        [{"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]}],                 # not the subresource
        [{"apiGroups": ["policy"], "resources": ["pods/eviction"], "verbs": ["create"]}],  # wrong group
        [{"apiGroups": [""], "resources": ["pods/eviction"], "verbs": ["get"]}],           # wrong verb
        [{**exact, "resourceNames": ["one"]}],                                             # narrowed
        [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["create"]}],                   # a wildcard is not the request
        [],                                                                                # nothing
    ]
    for role in short:
        missing, _ = compare(role, [need], rbac)
        if not missing:
            raise SystemExit(f"the check accepted a role that does not grant create pods/eviction: {role}")

    over = [
        [{**exact, "verbs": ["create", "watch"]}],                                         # a verb more
        [{**exact, "verbs": ["create", "update"]}],                                        # the verb nothing sends
        [{**exact, "resources": ["pods/eviction", "pods/attach"]}],                        # a resource more
        [exact, {"apiGroups": ["apps"], "resources": ["deployments/scale"], "verbs": ["patch"]}],  # a rule more
        [exact, {"apiGroups": ["*"], "resources": ["*"], "verbs": ["get"]}],               # a wildcard nobody asked for
    ]
    for role in over:
        _, extra = compare(role, [need], rbac)
        if not extra:
            raise SystemExit(f"the check accepted a role that grants more than create pods/eviction: {role}")

    missing, extra = compare([exact], [need], rbac)
    if missing or extra:
        raise SystemExit("the check refused a role that grants exactly what is needed")
    missing, extra = compare([], [need], {"write": False})
    if missing or extra:
        raise SystemExit("the check asked for a request whose switch is off")
    try:
        switch_on("rbac.wirte", {"write": True})
    except SystemExit:
        pass
    else:
        raise SystemExit("the check accepted a switch that is not a value")
    print(f"ok    self-test - {len(short)} roles granting too little and {len(over)} granting too much "
          "refused, a misspelt switch refused, the exact grant accepted")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--chart", default="./charts/kubetower")
    parser.add_argument("--needs", help="the list to compare with; default: the chart's console-needs.yaml")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    path = args.needs or os.path.join(args.chart, "console-needs.yaml")
    with open(path, encoding="utf-8") as f:
        needs = (yaml.safe_load(f) or {}).get("needs")
    if not needs:
        raise SystemExit(f"{path} lists nothing - there is nothing to check")
    sys.exit(0 if check(args.chart, needs) else 1)
