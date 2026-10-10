"""Derive what the console asks the cluster for from its source, and compare it
with charts/kubetower/console-needs.yaml.

    python test/derive-needs.py --core DIR             # compare, exit 1 on a difference
    python test/derive-needs.py --core DIR --print     # also print every request found
    python test/derive-needs.py --self-test --core DIR # prove the comparison can fail, then compare

DIR is a checkout of https://github.com/thonicdev/kubetower, at the commit the
list names under `derivedFrom` - CI fetches exactly that one. The role is
rendered from the list (templates/rbac.yaml), and test/console-needs-check.py
holds the role to the list; this holds the list to the console.

**How it reads the source.** Every non-test Go file of the console's module is
read as text - no Go toolchain, so it runs wherever Python does. It finds the
requests whose resource and verb are written out:

- a list page's question, `InNamespace("apps", "v1", "deployments", ns)` or
  `ClusterScoped(...)`;
- an access review's question, `Attributes{Verb: "get", Resource: "pods", ...}`;
- a typed client call, `CoreV1().Pods(ns).List`, `...GetLogs` (pods/log), and
  the SubjectAccessReview it creates;
- a dynamic client call on a resource spelt out in the same file,
  `Resource(crdGVR).List`;
- the three requests made through a raw path: the kubelet's stats through
  `nodes/proxy`, Prometheus through `services/proxy`, and metrics-server's
  `metrics.k8s.io` lists;
- the WebSocket upgrade of an exec or a port-forward, which is a GET and is
  authorised as `get` on the subresource as well as `create`;
- the verbs every write is made with, from internal/edit and internal/actions.

**And the requests whose resource is only known at run time.** Each such site
must be named in DYNAMIC below, with what it expands to. A site this script
cannot place fails the run: a new kind of request is a decision about the role,
and it is made here, not discovered in a cluster.

**What it compares.** Every request found must be in the list. Every line in
the list must be a request found - except writes, which are the chart's choice
of kinds: the editor and Delete reach any kind the console lists, so a write in
the list must be a kind the console lists with a verb it writes with, and that
is all. The SelfSubjectAccessReview needs no grant (Kubernetes' own
system:basic-user role holds it) and is not compared.

Exit status: 0 the list and the source agree, 1 they differ, 2 the source could
not be read. The last is not a pass.
"""

import argparse
import os
import re
import sys
from collections import defaultdict

import yaml

NEEDS = "charts/kubetower/console-needs.yaml"

# The typed clientset's group accessors, and the API group each one reaches.
CLIENTS = {
    "CoreV1": "", "AppsV1": "apps", "BatchV1": "batch",
    "NetworkingV1": "networking.k8s.io", "DiscoveryV1": "discovery.k8s.io",
    "AutoscalingV1": "autoscaling", "AutoscalingV2": "autoscaling", "PolicyV1": "policy",
    "SchedulingV1": "scheduling.k8s.io", "NodeV1": "node.k8s.io",
    "CoordinationV1": "coordination.k8s.io",
    "AdmissionregistrationV1": "admissionregistration.k8s.io",
    "RbacV1": "rbac.authorization.k8s.io", "StorageV1": "storage.k8s.io",
    "AuthorizationV1": "authorization.k8s.io", "EventsV1": "events.k8s.io",
    "ApiextensionsV1": "apiextensions.k8s.io",
}
METHODS = {"List": "list", "Get": "get", "Create": "create", "Patch": "patch",
           "Delete": "delete", "Update": "update", "Watch": "watch", "Apply": "patch"}

# A request whose resource, or verb, is only known at run time: the file it is
# in, the verb as the source writes it (a literal, or the expression), and what
# it expands to.
#
#   listed-get       `get` on every kind with a list page: the drawer opens any
#                    of them by name
#   writes           a write on any kind the console lists: the chart chooses
#                    which kinds rbac.write grants
#   file-gvrs        the verb on every resource spelt out in the same file
#   custom-resources the verb on every custom resource: the wildcard
#   list-question    nothing: the bodies of InNamespace and ClusterScoped,
#                    whose calls are read as list questions above
DYNAMIC = {
    ("internal/resources/fetch.go", "list"): "list-question",
    ("internal/detail/detail.go", "verb"): "listed-get",
    ("internal/changes/changes.go", "get"): "listed-get",
    ("internal/changes/changes.go", "list"): "file-gvrs",
    ("internal/detail/usedby.go", "list"): "file-gvrs",
    ("internal/edit/door.go", "verb"): "writes",
    ("internal/customresources/customresources.go", "list"): "custom-resources",
    ("internal/customresources/customresources.go", "get"): "custom-resources",
}

LIST_QUESTION = re.compile(r'\b(?:InNamespace|ClusterScoped)\(\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*"([^"]+)"')
ATTRIBUTES = re.compile(r'\bAttributes\{([^{}]*)\}')
FIELD = re.compile(r'\b(Verb|Group|Resource|Subresource)\s*:\s*("([^"]*)"|[^,}\n]+)')
TYPED = re.compile(r'\.(' + "|".join(CLIENTS) + r')\(\)\s*\.\s*(\w+)\([^()]*\)\s*\.\s*(\w+)\b')
GVR_VAR = re.compile(r'\b(\w+)\s*(?::=|=)\s*schema\.GroupVersionResource\{([^{}]*)\}')
GVR_ANY = re.compile(r'GroupVersionResource\{([^{}]*)\}')
GVR_CALL = re.compile(r'\bgvr\(\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*("([^"]*)"|\w+)\s*\)')
CONST = re.compile(r'\b(\w+)\s*=\s*"([a-z0-9.]+)"')
DYN_CALL = re.compile(r'\.Resource\((\w+)\)(?:\s*\.\s*Namespace\([^()]*\))?\s*\.\s*(' + "|".join(METHODS) + r')\b')
REST_SUB = re.compile(r'\.Get\(\)\s*\.\s*Resource\("(\w+)"\)\s*\.\s*Name\([^()]*\)\s*\.\s*SubResource\("(\w+)"\)')
SERVICE_PROXY = re.compile(r'"/api/v1/namespaces/%s/services/%s:%d/proxy')
METRICS = re.compile(r'(?:metricsAPIPath|"/apis/metrics\.k8s\.io/v1beta1[^"]*")\s*\+[^\n]*?"/(pods|nodes)"')
WS_EXEC = re.compile(r'NewWebSocketExecutor\([^()]*"GET"')
WS_FORWARD = re.compile(r'NewSPDYOverWebsocketDialer\(')
WRITE_CALL = re.compile(r'\b(?:perform\(ctx,\s*where,\s*target,|on\(|edit\.Attributes\(\w+,\s*edit\.Target\{[^{}]*\},)\s*"(\w+)"\s*,\s*"(\w*)"')
KIND_ROW = re.compile(r'^\s*Kind:\s*"(\w+)"', re.M)


def go_files(core):
    """Every non-test Go file of the console's own module."""
    nested = {os.path.dirname(os.path.join(dp, "go.mod"))
              for dp, _, fs in os.walk(core) if "go.mod" in fs} - {os.path.abspath(core), core}
    for dirpath, dirnames, filenames in os.walk(core):
        dirnames[:] = [d for d in dirnames
                       if d not in {"testdata", "vendor", "node_modules", ".git", "web"}
                       and os.path.join(dirpath, d) not in nested
                       and not d.endswith("test")]
        for f in filenames:
            if f.endswith(".go") and not f.endswith("_test.go"):
                path = os.path.join(dirpath, f)
                yield os.path.relpath(path, core).replace(os.sep, "/"), path


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


class Scan:
    def __init__(self):
        self.found = defaultdict(set)      # (group, resource, verb) -> {file:line}
        self.dynamic = defaultdict(set)    # expansion -> {(file, verb, file:line)}
        self.unplaced = []                 # file:line, what
        self.listed = {}                   # (group, resource) -> file, from the list pages
        self.kinds = set()                 # the kinds table's Kind names
        self.write_verbs = set()           # (verb, subresource)
        self.file_gvrs = defaultdict(set)  # file -> {(group, resource)}

    def add(self, group, resource, verb, where):
        self.found[(group, resource, verb)].add(where)


def gvr_fields(body, consts):
    fields = dict(re.findall(r'\b(Group|Resource)\s*:\s*("[^"]*"|\w+)', body))
    def value(v):
        if v is None:
            return ""
        if v.startswith('"'):
            return v.strip('"')
        return consts.get(v)
    group, resource = value(fields.get("Group")), value(fields.get("Resource"))
    if resource is None or group is None or not resource:
        return None
    return group, resource


def scan_file(scan, rel, text):
    consts = dict(CONST.findall(text))
    at = lambda m: f"{rel}:{line_of(text, m.start())}"

    for m in LIST_QUESTION.finditer(text):
        scan.add(m.group(1), m.group(2), "list", at(m))
        if rel == "internal/resources/fetch.go":
            scan.listed[(m.group(1), m.group(2))] = at(m)
    if rel == "internal/resources/kinds.go":
        scan.kinds |= set(KIND_ROW.findall(text))

    for m in ATTRIBUTES.finditer(text):
        fields = {k: (lit if raw.startswith('"') else None, raw.strip())
                  for k, raw, lit in FIELD.findall(m.group(1))}
        if "Verb" not in fields:
            continue  # a zero value, compared against; not a question
        verb, verb_raw = fields["Verb"]
        resource = fields.get("Resource", (None, ""))[0]
        group = fields.get("Group", ("", ""))[0]
        sub = fields.get("Subresource", ("", ""))[0]
        if verb is not None and resource is not None and group is not None and sub is not None:
            scan.add(group, resource + (f"/{sub}" if sub else ""), verb, at(m))
            continue
        key = (rel, verb if verb is not None else verb_raw)
        if key in DYNAMIC:
            scan.dynamic[DYNAMIC[key]].add((rel, key[1], at(m)))
        else:
            scan.unplaced.append((at(m), "Attributes{" + " ".join(m.group(1).split()) + "}"))

    for m in TYPED.finditer(text):
        client, method, call = m.groups()
        if method == "RESTClient":
            continue
        resource = method.lower()
        if call == "GetLogs" and resource == "pods":
            scan.add("", "pods/log", "get", at(m))
        elif resource == "selfsubjectaccessreviews":
            continue  # granted to everybody by system:basic-user
        elif call in METHODS:
            scan.add(CLIENTS[client], resource, METHODS[call], at(m))

    gvr_vars = {}
    for m in GVR_VAR.finditer(text):
        fields = gvr_fields(m.group(2), consts)
        if fields:
            gvr_vars[m.group(1)] = fields
    for m in GVR_ANY.finditer(text):
        fields = gvr_fields(m.group(1), consts)
        if fields:
            scan.file_gvrs[rel].add(fields)
    for m in GVR_CALL.finditer(text):
        resource = m.group(3) if m.group(3) is not None else consts.get(m.group(2))
        if resource:
            scan.file_gvrs[rel].add((m.group(1), resource))
    for m in DYN_CALL.finditer(text):
        if m.group(1) in gvr_vars:
            group, resource = gvr_vars[m.group(1)]
            scan.add(group, resource, METHODS[m.group(2)], at(m))

    for m in REST_SUB.finditer(text):
        scan.add("", f"{m.group(1)}/{m.group(2)}", "get", at(m))
    for m in SERVICE_PROXY.finditer(text):
        scan.add("", "services/proxy", "get", at(m))
    for m in METRICS.finditer(text):
        scan.add("metrics.k8s.io", m.group(1), "list", at(m))
    for m in WS_EXEC.finditer(text):
        scan.add("", "pods/exec", "get", at(m))
    for m in WS_FORWARD.finditer(text):
        scan.add("", "pods/portforward", "get", at(m))

    if rel.startswith(("internal/edit/", "internal/actions/")):
        for m in WRITE_CALL.finditer(text):
            scan.write_verbs.add((m.group(1), m.group(2)))


def scan_core(core):
    scan = Scan()
    files = 0
    for rel, path in go_files(core):
        with open(path, encoding="utf-8") as f:
            scan_file(scan, rel, f.read())
        files += 1
    if files == 0 or not scan.listed:
        print(f"read {files} Go files under {core} and found no list page - "
              "this is not a checkout of the console", file=sys.stderr)
        sys.exit(2)
    scan.files = files
    return scan


def derived(scan):
    """(requests, choices): every request the source makes, mapped to where;
    and the writes the source can make on any listed kind, of which the list
    may grant a subset."""
    requests = defaultdict(set, {k: set(v) for k, v in scan.found.items()})
    choices = defaultdict(set)

    for _, _, where in scan.dynamic.get("listed-get", ()):
        for group, resource in scan.listed:
            requests[(group, resource, "get")].add(where)
    for rel, verb, where in scan.dynamic.get("file-gvrs", ()):
        for group, resource in scan.file_gvrs[rel]:
            requests[(group, resource, verb)].add(where)
    for _, verb, where in scan.dynamic.get("custom-resources", ()):
        requests[("*", "*", verb)].add(where)
    if scan.dynamic.get("writes"):
        for verb, sub in scan.write_verbs:
            if sub == "eviction":
                requests[("", "pods/eviction", verb)].add("internal/edit, internal/actions")
            elif not sub:
                for group, resource in scan.listed:
                    choices[(group, resource, verb)].add("internal/edit, internal/actions")
    return requests, choices


def compare(requests, choices, needs):
    """(unlisted, unasked): requests the source makes that the list lacks, and
    lines in the list the source never asks for."""
    listed = {}
    for need in needs:
        for verb in need["verbs"]:
            listed[(need["group"], need["resource"], verb)] = need
    unlisted = sorted(k for k in requests if k not in listed)
    unasked = sorted((k, n) for k, n in listed.items() if k not in requests and k not in choices)
    return unlisted, unasked


def name(t):
    group, resource, verb = t
    return f"{verb} {group}/{resource}" if group else f"{verb} {resource}"


def self_test(requests, choices, needs):
    """Prove the comparison refuses a list that names too little or too much."""
    probe = ("", "pods", "list")
    assert probe in requests, "the source makes no `list pods`, so the self-test has nothing to remove"
    less = [dict(n, verbs=[v for v in n["verbs"] if (n["group"], n["resource"], v) != probe]) for n in needs]
    unlisted, _ = compare(requests, choices, less)
    if probe not in unlisted:
        raise SystemExit("the comparison accepted a list without `list pods`, which the console makes")
    more = needs + [{"switch": "rbac.read", "group": "", "resource": "pods", "verbs": ["watch"], "source": "self-test"},
                    {"switch": "rbac.write", "group": "apps", "resource": "deployments/scale", "verbs": ["patch"], "source": "self-test"},
                    {"switch": "rbac.write", "group": "", "resource": "pods", "verbs": ["update"], "source": "self-test"}]
    _, unasked = compare(requests, choices, more)
    if len([k for k, n in unasked if n["source"] == "self-test"]) != 3:
        raise SystemExit(f"the comparison accepted a list asking for watch, a scale subresource or update: {unasked}")
    print("ok    self-test - a list missing a request refused, and one naming watch, */scale and update refused")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", required=True, help="a checkout of thonicdev/kubetower")
    parser.add_argument("--needs", default=NEEDS)
    parser.add_argument("--print", action="store_true", help="print every request found, with where")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    with open(args.needs, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    needs = doc.get("needs") or []
    if not needs:
        print(f"{args.needs} lists nothing - there is nothing to compare", file=sys.stderr)
        sys.exit(2)

    scan = scan_core(args.core)
    requests, choices = derived(scan)
    commit = (doc.get("derivedFrom") or {}).get("commit", "?")
    print(f"read {scan.files} Go files of the console (the list says {commit}); "
          f"{len(scan.listed)} list pages, {len(requests)} requests, "
          f"{len(choices)} writes the chart may choose from")

    if args.self_test:
        self_test(requests, choices, needs)

    if args.print:
        for t in sorted(requests):
            print(f"        {name(t):70} {', '.join(sorted(requests[t])[:3])}")

    ok = True
    missing_kinds = sorted(scan.kinds - {r for _, r in scan.listed})
    if missing_kinds:
        ok = False
        print(f"FAIL  the kinds table names {missing_kinds}, and no list page asks for them - the scan is behind the source")
    if scan.unplaced:
        ok = False
        print(f"FAIL  {len(scan.unplaced)} request(s) whose resource or verb is only known at run time, "
              "and which DYNAMIC in this script does not place:")
        for where, what in scan.unplaced:
            print(f"        {where}  {what}")

    unlisted, unasked = compare(requests, choices, needs)
    print(f"{'FAIL' if unlisted else 'ok  '}  every request the console makes is in {args.needs}"
          + (f" - {len(unlisted)} missing" if unlisted else ""))
    for t in unlisted:
        print(f"        not in the list: {name(t)}  ({', '.join(sorted(requests[t])[:2])})")
    print(f"{'FAIL' if unasked else 'ok  '}  every line in the list is a request the console makes"
          + (f" - {len(unasked)} it never makes" if unasked else ""))
    for t, need in unasked:
        print(f"        never asked for: {name(t)}  ({need['switch']})")
    ok = ok and not unlisted and not unasked
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
