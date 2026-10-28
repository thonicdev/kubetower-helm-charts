"""Derive what the console asks the cluster for from its source, and compare it
with charts/kubetower/console-needs.yaml.

    python test/derive-needs.py --core DIR             # compare, exit 1 on a difference
    python test/derive-needs.py --core DIR --print     # also print every request found
    python test/derive-needs.py --self-test --core DIR # prove the scan and the comparison can fail, then compare

DIR is a checkout of https://github.com/thonicdev/kubetower, at the commit the
list names under `derivedFrom` - CI fetches exactly that one. The role is
rendered from the list (templates/rbac.yaml), and test/console-needs-check.py
holds the role to the list; this holds the list to the console.

**A best-effort check, not a proof.** It reads Go as text, with patterns, and
no Go toolchain - so it runs wherever Python does, and it can be fooled by a
shape of code it has no pattern for. What it does to narrow that gap is refuse
what it cannot classify rather than skip it: every typed client chain, every
raw REST request, every dynamic client call, every `.Watch(` and watch option,
every metrics.k8s.io path and every access review must be one it can read or
one named in a table below with what it expands to. A shape it has never seen
fails the run. A request hidden behind indirection it cannot follow - a client
passed through three functions, a path assembled elsewhere - can still pass.
The rendered role is held to the list exactly; the list is held to the source
only as well as this scan reads it.

**What it reads.**

- a list page's question, `InNamespace("apps", "v1", "deployments", ns)` or
  `ClusterScoped(...)`;
- an access review's question, `Attributes{Verb: "get", Resource: "pods", ...}`,
  and the `.Attributes("get", ...)` and `edit.Attributes(..., "create", "eviction")`
  helpers;
- a typed client call, `CoreV1().Pods(ns).List`, also when the namespaced
  client is held in a variable first, `...GetLogs` (pods/log), and the
  SubjectAccessReview;
- a dynamic client call on a resource spelt out in the same file or inline,
  `Resource(crdGVR).List`;
- a raw REST request, `RESTClient().Post().Resource("pods")...SubResource("exec")`;
- metrics.k8s.io paths, and Prometheus through `services/proxy`;
- the WebSocket upgrade of an exec or a port-forward, which is a GET and is
  authorised as `get` on the subresource as well as `create`;
- the verbs every write is made with, from internal/edit and internal/actions.

**The extras**, each package under `extras/` that cmd/kubetower/main.go
compiles in, are read apart from the rest and attributed to their key - the
key `extras:` in values.yaml names. An extra reaches the cluster only through
`extension.Host`, so what is read there is:

- every `extension.ListQuery{GVR: ...}`, a list of the resource its GVR names:
  a variable spelt out in the extra's package, `crd.GVR()` for the custom
  resources whose `plural.group` constants the same file names, or an
  expression placed in EXTRA_GVRS below;
- every typed client call, as for the console's own code;
- every other Host method, as HOST_METHODS says it expands.

A Host method this scan does not know, a `ListQuery` GVR it cannot place and
`crd.GVR()` in a file naming no custom resource each fail the run.

**What it compares.** Every request the console's own code makes must be in
the list, under a switch other than an extra's. Every line in
the list must be a request found - except writes, which are the chart's choice
of kinds: the editor and Delete reach any kind the console lists, so a write in
the list must be a kind the console lists with a verb it writes with, and that
is all. The SelfSubjectAccessReview needs no grant (Kubernetes' own
system:basic-user role holds it) and is not compared.

Every request an extra makes must be in the list on a line whose switch is
that extra (`extras.<key>`), or on a line that names the extra under
`extras:` - a request the console makes as well, granted by the console's own
switch. Every `extras.<key>` line, and every extra named under `extras:`, must
be a request that extra makes, and every key must be an extra the binary
compiles in. An extra that reads "any kind" (Argo CD's tab on every page) is
read as the console's own list pages, already compared, and the
custom-resource wildcard, which must name it.

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
    "ApiextensionsV1": "apiextensions.k8s.io", "AppsV1beta1": "apps",
    "BatchV1beta1": "batch", "PolicyV1beta1": "policy", "StorageV1beta1": "storage.k8s.io",
}
METHODS = {"List": "list", "Get": "get", "Create": "create", "Patch": "patch",
           "Delete": "delete", "DeleteCollection": "deletecollection", "Update": "update",
           "UpdateStatus": "update", "Watch": "watch", "Apply": "patch"}
REST_VERBS = {"Get": "get", "Post": "create", "Put": "update", "Patch": "patch", "Delete": "delete"}
WRITE_VERBS = {"create", "patch", "delete", "update", "deletecollection"}

# An access review whose resource, or verb, is only known at run time: the
# file it is in, the verb as the source writes it (a literal, or the
# expression), and what it expands to.
#
#   listed-get       `get` on every kind with a list page: the drawer opens any
#                    of them by name
#   writes           a write on any kind the console lists: the chart chooses
#                    which kinds rbac.write grants
#   file-gvrs        the verb on every resource spelt out in the same file
#   custom-resources the verb on every custom resource: the wildcard
#   none             no request of the core's own:
#                    - the bodies of InNamespace and ClusterScoped, whose calls
#                      are read as list questions;
#                    - an audit record rebuilt from what was reviewed, which asks
#                      the cluster nothing;
#                    - the extension host, which reviews the requests the
#                      extras make; those are read from extras/ itself, and
#                      attributed to the extra that makes them.
DYNAMIC = {
    ("internal/resources/fetch.go", "list"): "none",
    ("internal/access/record.go", "r.Verb"): "none",
    ("internal/web/host.go", "list"): "none",
    ("internal/web/host.go", "get"): "none",
    ("internal/detail/detail.go", "verb"): "listed-get",
    ("internal/changes/changes.go", "get"): "listed-get",
    ("internal/changes/changes.go", "list"): "file-gvrs",
    ("internal/detail/usedby.go", "list"): "file-gvrs",
    # The review of each list the drawer's owned-object events make; the
    # lists themselves are typed calls in the same file, read on their own.
    ("internal/detail/owned.go", "list"): "none",
    ("internal/edit/door.go", "verb"): "writes",
    ("internal/customresources/customresources.go", "list"): "custom-resources",
    ("internal/customresources/customresources.go", "get"): "custom-resources",
}

# A dynamic client call whose resource is only known at run time: the file and
# the argument to Resource(), and what it requests. A string is the reason the
# request is already counted - the access review that guards it, read above. A
# list names the requests it makes.
DYNAMIC_CLIENT = {
    ("internal/actions/drain.go", "pods.Resource"): [("", "pods", "list")],
    ("internal/changes/changes.go", "mapping.Resource"): "the drawer's get, reviewed in the same file",
    ("internal/customresources/customresources.go", "gvr"): "custom resources, reviewed in the same file",
    ("internal/customresources/customresources.go", "definition.GVR()"): "custom resources, reviewed in the same file",
    ("internal/detail/detail.go", "l.Resource"): "the drawer's get, reviewed in the same file",
    ("internal/detail/usedby.go", "resource"): "the used-by lists, reviewed in the same file",
    ("internal/edit/create.go", "where.Resource"): "writes, reviewed in internal/edit/door.go",
    ("internal/edit/edit.go", "where.Resource"): "writes, reviewed in internal/edit/door.go",
    ("internal/web/host.go", "q.GVR"): "the extension host, reviewed in the same file",
}

# A helper call building an access review whose verb is not written at the
# call: the file and the call's arguments.
DYNAMIC_HELPER = {
    ("internal/actions/needs.go", "where, target, verb, subresource"): "the `on` helper, whose calls are read as writes",
}

# Files that issue a raw GET by path (AbsPath). Each path they build is read
# by the metrics.k8s.io rule; a raw path in any other file fails the run.
ABS_PATH_FILES = {"internal/metrics/metrics.go", "internal/overview/metrics.go"}

ARGS = r'\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)'   # a call's arguments, two levels of nesting
CLIENT_RE = "|".join(sorted(CLIENTS, key=len, reverse=True))
METHOD_RE = "|".join(sorted(METHODS, key=len, reverse=True))

LIST_QUESTION = re.compile(r'\b(?:InNamespace|ClusterScoped)\(\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*"([^"]+)"')
ATTRIBUTES = re.compile(r'\bAttributes\{([^{}]*)\}')
FIELD = re.compile(r'\b(Verb|Group|Resource|Subresource)\s*:\s*("([^"]*)"|[^,}\n]+)')
ATTR_CALL = re.compile(r'\.Attributes(' + ARGS + ')')
CLIENT_CALL = re.compile(r'\.(' + CLIENT_RE + r')\(\)')
TYPED = re.compile(r'\.(' + CLIENT_RE + r')\(\)\s*\.\s*(\w+)' + ARGS + r'\s*\.\s*(\w+)\b')
TYPED_HELD = re.compile(r'\b(\w+)\s*:?=\s*[\w.]*\.(' + CLIENT_RE + r')\(\)\s*\.\s*(\w+)' + ARGS + r'[ \t]*(?:;|\n|$)')
REST_CLIENT = re.compile(r'RESTClient\(\)')
REST_CHAIN = re.compile(r'RESTClient\(\)\s*\.\s*(Get|Post|Put|Patch|Delete)\(\)((?:\s*\.\s*\w+' + ARGS + r')*)')
REST_HELD = re.compile(r'\b(\w+)(?:\s*,\s*\w+)?\s*:?=\s*[\w.()]*RESTClient\(\)(?:\.\(\*rest\.RESTClient\))?[ \t]*(?:;|\n|$)')
ABS_PATH = re.compile(r'\bAbsPath\(')
GVR_VAR = re.compile(r'\b(\w+)\s*(?::=|=)\s*schema\.GroupVersionResource\{([^{}]*)\}')
GVR_ANY = re.compile(r'GroupVersionResource\{([^{}]*)\}')
GVR_CALL = re.compile(r'\bgvr\(\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*("([^"]*)"|\w+)\s*\)')
CONST = re.compile(r'\b(\w+)\s*=\s*"([a-z0-9.]+)"')
DYN_RESOURCE = re.compile(r'\.Resource\(((?:[^()]|\([^()]*\))+)\)')
SERVICE_PROXY = re.compile(r'"/api/v1/namespaces/%s/services/%s:%d/proxy')
METRICS_LINE = re.compile(r'metricsAPIPath|/apis/metrics\.k8s\.io')
WS_EXEC = re.compile(r'NewWebSocketExecutor\([^()]*"GET"')
WS_FORWARD = re.compile(r'NewSPDYOverWebsocketDialer\(')
WATCH_CALL = re.compile(r'\.\s*Watch\(')
WATCH_OPTION = re.compile(r'\bWatch\s*:\s*true\b|NewSharedInformer|NewInformer\(|NewListWatch|NewFilteredListWatch|watch=true')
WRITE_CALL = re.compile(r'\b(?:perform\(ctx,\s*where,\s*target,|on\(|edit\.Attributes\(\w+,\s*edit\.Target\{[^{}]*\},)\s*"(\w+)"\s*,\s*"(\w*)"')
KIND_ROW = re.compile(r'^\s*Kind:\s*"(\w+)"', re.M)


def go_files(core, under=None):
    """Every non-test Go file of the console's own module: not another module
    nested in it, not a test helper package, not the web front end, and not
    the extras, which are read apart (scan_extras). With under, only the files
    below that directory of the module, the extras included."""
    root = os.path.abspath(core)
    nested = {os.path.abspath(dp) for dp, _, fs in os.walk(core) if "go.mod" in fs} - {root}
    for dirpath, dirnames, filenames in os.walk(os.path.join(core, under) if under else core):
        here = os.path.abspath(dirpath)
        dirnames[:] = [d for d in dirnames
                       if d not in {"testdata", "vendor", "node_modules", ".git"}
                       and not (here == root and d in {"web", "extras"} and under is None)
                       and os.path.join(here, d) not in nested
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
        self.unplaced = []                 # (file:line, what)
        self.listed = {}                   # (group, resource) -> file, from the list pages
        self.kinds = set()                 # the kinds table's Kind names
        self.write_verbs = set()           # (verb, subresource)
        self.file_gvrs = defaultdict(set)  # file -> {(group, resource)}
        self.files = 0

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


def literals(args):
    return re.findall(r'"([^"]*)"', args)


def scan_file(scan, rel, text):
    # Whole-line comments are prose, not code: blanked, keeping line numbers.
    text = re.sub(r'(?m)^[ 	]*//.*$', '', text)
    consts = dict(CONST.findall(text))
    at = lambda m: f"{rel}:{line_of(text, m.start())}"
    unplaced = lambda m, what: scan.unplaced.append((at(m), " ".join(what.split())[:160]))

    # List pages.
    for m in LIST_QUESTION.finditer(text):
        scan.add(m.group(1), m.group(2), "list", at(m))
        if rel == "internal/resources/fetch.go":
            scan.listed[(m.group(1), m.group(2))] = at(m)
    if rel == "internal/resources/kinds.go":
        scan.kinds |= set(KIND_ROW.findall(text))

    # Access reviews, as literals.
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
            unplaced(m, "Attributes{" + m.group(1) + "}")

    # Access reviews, through a helper: where.Attributes("get", ...),
    # edit.Attributes(where, target, "create", "eviction").
    for m in ATTR_CALL.finditer(text):
        args = m.group(1)[1:-1]
        if re.match(r'\s*\w+\s+\w+', args) or "string" in args:
            continue  # the helper's own declaration
        lits = literals(args)
        verbs = [v for v in lits if v in WRITE_VERBS | {"get", "list"}]
        if not verbs:
            if (rel, " ".join(args.split())) in DYNAMIC_HELPER:
                continue
            unplaced(m, ".Attributes(" + args + ")")
        elif verbs[0] == "get":
            scan.dynamic["listed-get"].add((rel, "get", at(m)))
        elif verbs[0] in WRITE_VERBS:
            pass  # read by WRITE_CALL below, in internal/edit and internal/actions
            if not rel.startswith(("internal/edit/", "internal/actions/")):
                unplaced(m, ".Attributes(" + args + ")")
        else:
            unplaced(m, ".Attributes(" + args + ")")

    # Typed clients: chained, or held in a variable and called later.
    consumed = set()
    for m in TYPED.finditer(text):
        client, method, call = m.groups()
        consumed.add(m.start())
        if method == "RESTClient":
            continue  # read by the REST rules below
        resource = method.lower()
        if call == "GetLogs" and resource == "pods":
            scan.add("", "pods/log", "get", at(m))
        elif resource == "selfsubjectaccessreviews":
            continue  # granted to everybody by system:basic-user
        elif call in METHODS:
            scan.add(CLIENTS[client], resource, METHODS[call], at(m))
        else:
            unplaced(m, m.group(0))
    held = {}
    for m in TYPED_HELD.finditer(text):
        client, method = m.group(2), m.group(3)
        consumed.add(m.start(2) - 1)
        held[m.group(1)] = (CLIENTS[client], method.lower(), at(m))
    for name, (group, resource, where) in held.items():
        uses = list(re.finditer(r'\b' + name + r'\s*\.\s*(\w+)\(', text))
        for u in uses:
            call = u.group(1)
            if call == "GetLogs" and resource == "pods":
                scan.add("", "pods/log", "get", at(u))
            elif call in METHODS:
                scan.add(group, resource, METHODS[call], at(u))
            else:
                unplaced(u, u.group(0))
    for m in CLIENT_CALL.finditer(text):
        if m.start() not in consumed:
            unplaced(m, text[m.start():m.start() + 120])

    # Raw REST requests.
    chained = {m.start() for m in REST_CHAIN.finditer(text)}
    for m in REST_CHAIN.finditer(text):
        chain = m.group(2)
        res = re.search(r'\.\s*Resource\("(\w+)"\)', chain)
        sub = re.search(r'\.\s*SubResource\("(\w+)"', chain)
        if res:
            resource = res.group(1) + (f"/{sub.group(1)}" if sub else "")
            scan.add("", resource, REST_VERBS[m.group(1)], at(m))
        elif "AbsPath(" not in chain:
            unplaced(m, m.group(0))
    rest_held = {m.start() for m in REST_HELD.finditer(text)}
    for m in REST_CLIENT.finditer(text):
        if m.start() in chained:
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        if not any(line_start <= h <= m.start() for h in rest_held):
            unplaced(m, text[line_start:m.end() + 60])
    for m in ABS_PATH.finditer(text):
        if rel not in ABS_PATH_FILES:
            unplaced(m, text[m.start():m.start() + 120])

    # metrics.k8s.io, by path: every line that names it is read. A path ending
    # in /pods or /nodes is a list, one continuing past it names an object.
    for m in re.finditer(r'^.*$', text, re.M):
        line = m.group(0)
        if not METRICS_LINE.search(line):
            continue
        named = re.search(r'/(pods|nodes)(/?)"', line)
        if named:
            scan.add("metrics.k8s.io", named.group(1), "get" if named.group(2) else "list", at(m))
        elif re.search(r'const\s+metricsAPIPath\s*=|metricsAPIPath\s*[,)]|/v1beta1"', line):
            continue  # the API's own discovery document
        else:
            unplaced(m, line)

    # Dynamic clients.
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
    for m in DYN_RESOURCE.finditer(text):
        arg = " ".join(m.group(1).split())
        if arg.startswith('"'):
            continue  # a REST builder's Resource("pods"), read above
        after = re.match(r'(?:\s*\.\s*Namespace' + ARGS + r')?\s*\.\s*(' + METHOD_RE + r')\b', text[m.end():])
        fields = gvr_vars.get(arg)
        if fields is None and arg.startswith("schema.GroupVersionResource{"):
            fields = gvr_fields(arg, consts)
        if fields is not None:
            if after:
                scan.add(fields[0], fields[1], METHODS[after.group(1)], at(m))
            else:
                unplaced(m, m.group(0))
            continue
        reason = DYNAMIC_CLIENT.get((rel, arg))
        if reason is None:
            unplaced(m, m.group(0))
        elif isinstance(reason, list):
            for group, resource, verb in reason:
                scan.add(group, resource, verb, at(m))

    # Proxies, streams, watches.
    for m in SERVICE_PROXY.finditer(text):
        scan.add("", "services/proxy", "get", at(m))
    for m in WS_EXEC.finditer(text):
        scan.add("", "pods/exec", "get", at(m))
    for m in WS_FORWARD.finditer(text):
        scan.add("", "pods/portforward", "get", at(m))
    typed_watch = {m.start(3) for m in TYPED.finditer(text) if m.group(3) == "Watch"}
    for m in WATCH_CALL.finditer(text):
        if not any(abs(m.start() - t) <= 2 for t in typed_watch):
            unplaced(m, "a watch: " + text[max(0, m.start() - 60):m.end() + 20])
    for m in WATCH_OPTION.finditer(text):
        unplaced(m, "a watch: " + m.group(0))

    if rel.startswith(("internal/edit/", "internal/actions/")):
        for m in WRITE_CALL.finditer(text):
            scan.write_verbs.add((m.group(1), m.group(2)))


def scan_core(core):
    scan = Scan()
    for rel, path in go_files(core):
        with open(path, encoding="utf-8") as f:
            scan_file(scan, rel, f.read())
        scan.files += 1
    if scan.files == 0 or not scan.listed:
        print(f"read {scan.files} Go files under {core} and found no list page - "
              "this is not a checkout of the console", file=sys.stderr)
        sys.exit(2)
    return scan


# What each extension.Host method asks the cluster for, when an extra calls it.
# The Host is the console's own code (internal/web/host*.go), so every request
# here is also one the console's scan must find - checked, so that this table
# cannot drift from the source it summarises.
FORWARD = [("", "services", "get"), ("", "pods", "list"),
           ("", "pods/portforward", "create"), ("", "pods/portforward", "get")]
HOST_METHODS = {
    # Nothing by themselves: List is read from its ListQuery, Client from the
    # typed calls made on what it returns.
    "Client": [], "List": [], "ConfirmToken": [],
    # The custom-resource catalogue.
    "Definitions": [("apiextensions.k8s.io", "customresourcedefinitions", "list")],
    # One key of one named Secret, reviewed for the person first.
    "Reveal": [("", "secrets", "get")],
    # The Service, its pods, and the tunnel to one of them.
    "Forward": FORWARD,
    # metrics-server's figures.
    "ContainerUsage": [("metrics.k8s.io", "pods", "list")],
    # Prometheus: found among the Services, asked through the service proxy,
    # or through a port-forward when the proxy is refused.
    "UsageWindow": [("", "services", "list"), ("", "services/proxy", "get")] + FORWARD,
    # The kubelets' stats, through the node proxy.
    "Usage": [("", "nodes/proxy", "get")],
}

# A ListQuery whose GVR is an expression this scan cannot resolve from the
# extra's own variables: the file and the expression, and what it reads.
#
#   any-kind   every kind the console shows: its own list pages, already
#              compared, and every custom resource - the wildcard
#   a list     the extra's own GVR variables the expression takes its value from
EXTRA_GVRS = {
    ("extras/argocd/owner.go", "t.gvr"): "any-kind",
    ("extras/argocd/tracked.go", "target.gvr"): "any-kind",
    ("extras/kubeadjust/kubeadjust.go", "kind.gvr"): ["replicaSetsGVR", "jobsGVR"],
    ("extras/kubecron/owner.go", "gvr"): ["jobsGVR", "podsGVR"],
}

EXTRA_IMPORT = re.compile(r'"github\.com/thonicdev/kubetower/extras/([\w-]+)"')
EXTRA_KEY = re.compile(r'\bconst\s+Key\s*=\s*"([a-z0-9-]+)"')
LIST_QUERY = re.compile(r'\bListQuery\{')
QUERY_GVR = re.compile(r'\bGVR\s*:\s*([^,}\n]+)')
CRD_CONST = re.compile(r'\b(\w+Kind)\s*=\s*"([a-z0-9-]+)\.([a-z0-9-]+(?:\.[a-z0-9-]+)+)"')
HOST_PARAM = re.compile(r'\b(\w+)\s+extension\.Host\b')


def braced(text, start):
    """The text from the brace at start to its match."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


class Extra:
    def __init__(self, key, directory):
        self.key, self.directory = key, directory
        self.scan = Scan()                 # the typed calls, as for the console
        self.found = defaultdict(set)      # (group, resource, verb) -> {file:line}
        self.unplaced = []


def scan_extra_files(extra, files):
    """files: (rel, text). Reads every Host call and every ListQuery."""
    gvr_vars, crds = {}, {}
    for rel, text in files:
        consts = dict(CONST.findall(text))
        for m in GVR_VAR.finditer(text):
            fields = gvr_fields(m.group(2), consts)
            if fields:
                gvr_vars[m.group(1)] = fields
        for m in CRD_CONST.finditer(text):
            crds[m.group(1)] = (m.group(3), m.group(2))
    for rel, text in files:
        text = re.sub(r'(?m)^[ \t]*//.*$', '', text)
        scan_file(extra.scan, rel, text)
        at = lambda pos: f"{rel}:{line_of(text, pos)}"

        hosts = set(HOST_PARAM.findall(text)) - {"extension"}
        if hosts:
            call = re.compile(r'\b(?:' + "|".join(sorted(hosts)) + r')\.(\w+)\(')
            for m in call.finditer(text):
                if m.group(1) not in HOST_METHODS:
                    extra.unplaced.append((at(m.start()), "a Host method this scan does not know: " + m.group(0)))
                    continue
                for t in HOST_METHODS[m.group(1)]:
                    extra.found[t].add(at(m.start()))

        # The kinds crd.GVR() can be: those named in code - not in a string -
        # in the function the ListQuery is in. A file names kinds in messages
        # and in other handlers, and those are not this list.
        code = re.sub(r'"(?:[^"\\\n]|\\.)*"|`[^`]*`', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)
        funcs = [f.start() for f in re.finditer(r'(?m)^func\b', code)] + [len(code)]
        def named_in(pos):
            start = max([f for f in funcs if f <= pos], default=0)
            end = min(f for f in funcs if f > pos)
            return {n for n in re.findall(r'\b(\w+Kind)\b', code[start:end]) if n in crds}
        for m in LIST_QUERY.finditer(text):
            body = braced(text, m.end() - 1)
            g = QUERY_GVR.search(body)
            if not g:
                extra.unplaced.append((at(m.start()), "a ListQuery naming no GVR: " + " ".join(body.split())[:120]))
                continue
            expr = g.group(1).strip()
            if expr in gvr_vars:
                group, resource = gvr_vars[expr]
                extra.found[(group, resource, "list")].add(at(m.start()))
            elif expr == "crd.GVR()" and named_in(m.start()):
                for name in named_in(m.start()):
                    group, resource = crds[name]
                    extra.found[(group, resource, "list")].add(at(m.start()))
            elif (rel, expr) in EXTRA_GVRS:
                place = EXTRA_GVRS[(rel, expr)]
                if place == "any-kind":
                    extra.found[("*", "*", "list")].add(at(m.start()))
                    continue
                for name in place:
                    if name not in gvr_vars:
                        extra.unplaced.append((at(m.start()), f"EXTRA_GVRS names {name}, which the extra does not spell out"))
                        continue
                    group, resource = gvr_vars[name]
                    extra.found[(group, resource, "list")].add(at(m.start()))
            else:
                extra.unplaced.append((at(m.start()), "a ListQuery GVR this scan cannot place: " + expr))

    for t, where in extra.scan.found.items():
        extra.found[t] |= where
    extra.unplaced += extra.scan.unplaced


def scan_extras(core):
    """{key: Extra} for every extra cmd/kubetower/main.go compiles in, and the
    directories under extras/ it does not."""
    main = os.path.join(core, "cmd", "kubetower", "main.go")
    imported = []
    if os.path.exists(main):
        with open(main, encoding="utf-8") as f:
            imported = EXTRA_IMPORT.findall(f.read())
    extras, left = {}, []
    root = os.path.join(core, "extras")
    present = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []
    left = [d for d in present if d not in imported]
    for directory in imported:
        files = []
        for rel, path in go_files(core, under=f"extras/{directory}"):
            with open(path, encoding="utf-8") as f:
                files.append((rel, f.read()))
        keys = {k for rel, text in files if rel.count("/") == 2 for k in EXTRA_KEY.findall(text)}
        if len(keys) != 1:
            print(f"extras/{directory} is compiled in and declares {sorted(keys) or 'no'} `const Key` - "
                  "the scan cannot tell which key values.yaml names it by", file=sys.stderr)
            sys.exit(2)
        extra = Extra(keys.pop(), directory)
        scan_extra_files(extra, files)
        extras[extra.key] = extra
    return extras, left


def compare_extras(extras, needs):
    """(unlisted, unasked, unknown): an extra's requests the list does not grant
    it, lines and `extras:` names no request of that extra's accounts for, and
    keys the list names that the binary does not compile in."""
    own, uses = defaultdict(dict), defaultdict(list)
    unknown = set()
    for need in needs:
        sw = need["switch"]
        if sw.startswith("extras."):
            key = sw.removeprefix("extras.")
            for verb in need["verbs"]:
                own[key][(need["group"], need["resource"], verb)] = need
            if key not in extras:
                unknown.add(key)
        for key in need.get("extras") or []:
            uses[key].append(need)
            if key not in extras:
                unknown.add(key)
    used = lambda key: {(n["group"], n["resource"], v) for n in uses[key] for v in n["verbs"]}
    unlisted, unasked = [], []
    for key, extra in sorted(extras.items()):
        for t in sorted(extra.found):
            if t not in own[key] and t not in used(key):
                unlisted.append((key, t, extra.found[t]))
    for key in sorted(own):
        for t, need in sorted(own[key].items()):
            if key in extras and t not in extras[key].found:
                unasked.append((key, t, need["switch"]))
    for key in sorted(uses):
        for need in uses[key]:
            if key in extras and not any((need["group"], need["resource"], v) in extras[key].found for v in need["verbs"]):
                unasked.append((key, (need["group"], need["resource"], "/".join(need["verbs"])), f"{need['switch']}, extras: [{key}]"))
    return unlisted, unasked, sorted(unknown)


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
        if need["switch"].startswith("extras."):
            continue  # an extra's own grant: compare_extras
        for verb in need["verbs"]:
            listed[(need["group"], need["resource"], verb)] = need
    unlisted = sorted(k for k in requests if k not in listed)
    unasked = sorted((k, n) for k, n in listed.items() if k not in requests and k not in choices)
    return unlisted, unasked


def name(t):
    group, resource, verb = t
    return f"{verb} {group}/{resource}" if group else f"{verb} {resource}"


# Single lines a change to the console could add, each a request the list does
# not carry. The scan must refuse every one: by finding a request the list
# lacks, or by refusing a shape it cannot place.
PROBES = [
    ("a typed watch", 'x := c.CoreV1().Pods(ns).Watch(ctx, opts)\n'),
    ("a kind the console does not list", 'x := c.AppsV1().ControllerRevisions(ns).List(ctx, opts)\n'),
    ("nested parentheses", 'x := c.CoreV1().Pods(raw.GetNamespace()).Watch(ctx, opts)\n'),
    ("a namespaced client held in a variable", 'pods := c.CoreV1().Pods(ns)\nw, err := pods.Watch(ctx, opts)\n'),
    ("a dynamic call on an inline resource",
     'l, err := dyn.Resource(schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "controllerrevisions"}).Namespace(ns).List(ctx, opts)\n'),
    ("a raw path to one object's metrics",
     'raw, err := rest.Get().AbsPath("/apis/metrics.k8s.io/v1beta1/namespaces/"+ns+"/pods/"+name).DoRaw(ctx)\n'),
    ("a watch through list options", 'l, err := c.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{Watch: true})\n'),
    ("a raw REST request on a new subresource", 'r := c.CoreV1().RESTClient().Post().Resource("pods").Name(p).SubResource("attach")\n'),
    ("a dynamic call on a resource known only at run time", 'l, err := dyn.Resource(somethingElse).List(ctx, opts)\n'),
]


# Extra files a change to an extra could add, each a request the list does
# not grant it. The extras' scan must refuse every one.
EXTRA_PROBES = [
    ("a ListQuery on a resource nothing grants",
     'var revisionsGVR = schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "controllerrevisions"}\n'
     'func f(h extension.Host) { h.List(r, c, extension.ListQuery{GVR: revisionsGVR}) }\n'),
    ("a ListQuery on an expression the scan cannot place",
     'func f(h extension.Host) { h.List(r, c, extension.ListQuery{GVR: somewhere.gvr}) }\n'),
    ("crd.GVR() in a file naming no custom resource",
     'func f(h extension.Host) { h.List(r, c, extension.ListQuery{GVR: crd.GVR()}) }\n'),
    ("crd.GVR() in a function naming no kind, beside one that does",
     'const KustomizationKind = "kustomizations.kustomize.toolkit.fluxcd.io"\n'
     'func g() string { return "Flux, serving " + KustomizationKind }\n'
     'func f(h extension.Host) { h.List(r, c, extension.ListQuery{GVR: crd.GVR()}) }\n'),
    ("a Host method the scan does not know",
     'func f(h extension.Host) { h.Exec(r, c, "ns", "pod") }\n'),
    ("a typed write", 'func f(c kubernetes.Interface) { c.CoreV1().Secrets(ns).Delete(ctx, n, opts) }\n'),
]


def self_test_extras(extras, needs):
    """Prove the extras' comparison refuses a list that grants an extra too
    little, a line or an `extras:` name nobody asks for, and an unknown key;
    and that the extras' scan refuses each probe."""
    if not extras:
        raise SystemExit("the console compiles in no extra, so the extras' self-test has nothing to remove")
    key = sorted(extras)[0]
    probe = sorted(extras[key].found)[0]
    less = []
    for n in needs:
        if n["switch"] == f"extras.{key}" or key in (n.get("extras") or []):
            if any((n["group"], n["resource"], v) == probe for v in n["verbs"]):
                if n["switch"] == f"extras.{key}":
                    n = dict(n, verbs=[v for v in n["verbs"] if (n["group"], n["resource"], v) != probe])
                else:
                    n = dict(n, extras=[k for k in n["extras"] if k != key])
        less.append(n)
    unlisted, _, _ = compare_extras(extras, less)
    if not any(k == key and t == probe for k, t, _ in unlisted):
        raise SystemExit(f"the comparison accepted a list that does not grant {key} its {name(probe)}")
    more = needs + [
        {"switch": f"extras.{key}", "group": "apps", "resource": "controllerrevisions", "verbs": ["list"], "source": "self-test"},
        {"switch": "rbac.exec", "group": "", "resource": "pods/attach", "verbs": ["create"], "extras": [key], "source": "self-test"},
        {"switch": "extras.no-such-extra", "group": "", "resource": "pods", "verbs": ["list"], "source": "self-test"},
    ]
    _, unasked, unknown = compare_extras(extras, more)
    if len([u for u in unasked if u[1][1] in {"controllerrevisions", "pods/attach"}]) != 2 or "no-such-extra" not in unknown:
        raise SystemExit(f"the comparison accepted a line, an extras: name or a key nobody asks for: {unasked} {unknown}")

    for label, text in EXTRA_PROBES:
        extra = Extra("probe", "probe")
        scan_extra_files(extra, [("extras/probe/probe.go", "package probe\n\n" + text)])
        unlisted, _, _ = compare_extras({"probe": extra}, needs)
        if not extra.unplaced and not unlisted:
            raise SystemExit(f"the extras' scan accepted {label}")
    print(f"ok    self-test - a list not granting {key} its {name(probe)} refused, a line, an extras: name and a key "
          f"nobody asks for refused, and {len(EXTRA_PROBES)} probe files refused by the extras' scan")
    print()


def self_test(requests, choices, needs):
    """Prove the comparison refuses a list that names too little or too much,
    and that the scan refuses each probe."""
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

    for label, line in PROBES:
        scan = Scan()
        scan_file(scan, "internal/probe/probe.go", "package probe\n\n" + line)
        found, _ = derived(scan)
        unlisted, _ = compare(found, {}, needs)
        if not scan.unplaced and not unlisted:
            raise SystemExit(f"the scan accepted {label}: {line.strip()}")
    print(f"ok    self-test - a list missing a request refused, one naming watch, */scale and update refused, "
          f"and {len(PROBES)} probe lines refused by the scan")
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
    extras, left = scan_extras(args.core)
    commit = (doc.get("derivedFrom") or {}).get("commit", "?")
    print(f"read {scan.files} Go files of the console (the list says {commit}); "
          f"{len(scan.listed)} list pages, {len(requests)} requests, "
          f"{len(choices)} writes the chart may choose from")

    for key, extra in sorted(extras.items()):
        print(f"read extras/{extra.directory} as the extra {key!r}: {len(extra.found)} requests")
    for directory in left:
        print(f"note  extras/{directory} is not compiled into cmd/kubetower, and was not read")

    if args.self_test:
        self_test(requests, choices, needs)
        self_test_extras(extras, needs)

    if args.print:
        for t in sorted(requests):
            print(f"        {name(t):70} {', '.join(sorted(requests[t])[:3])}")
        for key, extra in sorted(extras.items()):
            print(f"      the extra {key!r}:")
            for t in sorted(extra.found):
                print(f"        {name(t):70} {', '.join(sorted(extra.found[t])[:3])}")

    ok = True
    missing_kinds = sorted(scan.kinds - {r for _, r in scan.listed})
    if missing_kinds:
        ok = False
        print(f"FAIL  the kinds table names {missing_kinds}, and no list page asks for them - the scan is behind the source")
    if scan.unplaced:
        ok = False
        print(f"FAIL  {len(scan.unplaced)} site(s) this scan cannot read, and which no table in it places:")
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

    stale = sorted({t for t, _ in HOST_METHODS.items() for t in _} - set(requests))
    if stale:
        ok = False
        print(f"FAIL  HOST_METHODS expands a Host method to {len(stale)} request(s) the console's own code no longer makes - "
              "the table is behind the source:")
        for t in stale:
            print(f"        {name(t)}")
    unplaced = [u for e in extras.values() for u in e.unplaced]
    if unplaced:
        ok = False
        print(f"FAIL  {len(unplaced)} site(s) in the extras this scan cannot read:")
        for where, what in unplaced:
            print(f"        {where}  {what}")
    e_unlisted, e_unasked, unknown = compare_extras(extras, needs)
    print(f"{'FAIL' if e_unlisted else 'ok  '}  every request an extra makes is granted to it in {args.needs}"
          + (f" - {len(e_unlisted)} missing" if e_unlisted else ""))
    for key, t, where in e_unlisted:
        print(f"        not granted to {key}: {name(t)}  ({', '.join(sorted(where)[:2])})")
    print(f"{'FAIL' if e_unasked else 'ok  '}  every line for an extra is a request that extra makes"
          + (f" - {len(e_unasked)} it never makes" if e_unasked else ""))
    for key, t, where in e_unasked:
        print(f"        {key} never asks for: {name(t)}  ({where})")
    if unknown:
        print(f"FAIL  the list names extras the console does not compile in: {unknown}")
    ok = ok and not e_unlisted and not e_unasked and not unknown
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
