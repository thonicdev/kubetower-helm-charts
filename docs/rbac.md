# RBAC

Read-only, cluster-wide, by default — rendered from what the console asks for,
not from `cluster-admin` and not from the built-in `view` role, which excludes
Secrets, which the Secrets page and the Helm release list both need.

`charts/kubetower/console-needs.yaml` lists every request the console makes:
its API group, resource and verbs, the switch below that grants it, and the
file in the console's source (open from its 0.1.0 release) that makes it.
`templates/rbac.yaml` writes that list out as rules and adds nothing, so a grant that is not a line there does not exist. That is why the role has no
`watch` (the console polls), no `update` (every write is a merge patch, a
create, a delete or an eviction), no `*/scale` or `*/status`, no `pods/attach`,
and no `nodes/stats` or `nodes/metrics` (the kubelet's figures come through
`nodes/proxy`). The one verb the console's own review does not name is `get`
on `pods/exec` and `pods/portforward`: both open as a WebSocket, which is a GET
the API server authorises as `get` - and, from Kubernetes 1.30, as `create`
too.

One rule is not read-only and is always there: `create` on
`subjectaccessreviews`, which lets the console check each person's rights
before acting, rather than acting with its own. It asks the cluster a question
and grants nothing.

Four switches are off and each one is a decision rather than a default:
`rbac.write`, `rbac.exec`, `rbac.nodeProxy`, `rbac.prometheusProxy`.
`rbac.customResources` is off too — the custom-resource browser needs a
wildcard read, and a wildcard is worth typing out deliberately. So is
`rbac.deleteAll`, the other wildcard: `delete` on every kind, and no other verb.

| Switch | What the console does with it |
|---|---|
| `rbac.write` | Edit, create, delete; restart, scale, suspend, resume, trigger; cordon, uncordon and drain a node (`patch` on `nodes`); evict a pod (`create` on `pods/eviction`) |
| `rbac.exec` | A shell in a pod, a port-forward, and the fallback route to Prometheus |
| `rbac.prometheusProxy` | Prometheus through the API server's service proxy (`get` on `services/proxy`), which is a proxy to every Service in the cluster unless `rbac.prometheusProxyServices` names yours as `<name>:<port>` |
| `rbac.nodeProxy` | Per-node usage from the kubelet where there is no metrics-server |
| `rbac.deleteAll` | Delete on every kind the console shows - Roles and RoleBindings, quotas, custom resources and their definitions, Namespaces, PersistentVolumes, Nodes - where `rbac.write` deletes only the kinds it lists. One rule: `delete` on `*` in `*`. It needs neither `rbac.write` nor `rbac.customResources` |

What three of them amount to, in plain terms: **`rbac.write` is cluster-admin on
most clusters**, because creating a pod in a namespace means running as any
ServiceAccount there; **`rbac.nodeProxy` is a shell on every node**, because
the kubelet API it reaches runs commands in pods. **`rbac.deleteAll` is
cluster-admin for deletion**: everybody who can sign in may delete anything in
the cluster, up to their own RoleBindings with single sign-on, and all of it
with the shared password. And the default read of
Secrets includes this release's own, so anybody who signs in can read the
session key and mint a session of their own.

`test/rbac-check.py` refuses an escalation verb (`impersonate`, `escalate`,
`bind`), a wildcard verb, and any wildcard but two: the custom-resource read
(`get`, `list`) and `rbac.deleteAll`'s, whose verbs must be exactly `delete`. That is
all it checks: it passes with every switch on, so a green run says nothing about
whether the switches you chose are safe.

`test/console-needs-check.py` holds the role to the list: for each switch, and
for every switch on and every switch off, the rendered role must grant each
request whose switch is on and nothing else, and the defaults nothing behind an
off-by-default switch.

`test/derive-needs.py` holds the list to the console: it reads the console's Go
source at the commit the list names and fails when the source makes a request
the list lacks, or the list names one the source never makes. Writes are the
exception, by design: the editor reaches any kind the console lists, so the
kinds `rbac.write` grants are this chart's choice, and the check only asks that
each be a kind the console lists and a verb it writes with - or, for
`rbac.deleteAll`, `delete` on every kind, which the console's Delete reaches
since it takes its kind at run time. The extras are
read apart, each under its key: every request an extra makes must be granted to
it, by a line of its own (`switch: extras.<key>`) or by a line of the console's
that names it under `extras:`. While the console is private, CI runs it on a
push to `main` only, reading the console with the repository
secret `KUBETOWER_READ_TOKEN`, and **fails** where it cannot read it - a check
that did not compare anything is not a pass. On a pull request it is skipped
with a notice. Locally, with the console checked out beside this repository at
the commit the list names:

```bash
python test/derive-needs.py --self-test --core ../kubetower
```

**It is a best-effort check, not a proof.** It reads Go as text, with
patterns. It refuses every client call, raw path, dynamic call and watch it
cannot classify, rather than skipping it, and its self-test feeds it lines a
change could add and requires each to fail. But a request hidden behind
indirection it cannot follow can still pass. The role is held to the list
exactly; the list is held to the console only as well as that scan reads it.

**A known limit: owner links to kinds the console does not list.** The drawer
follows an object's owner reference to any kind the cluster serves, and reads
it with `get`. The role grants `get` only on the kinds the console lists, so a
link to any other kind answers 403. The kinds 0.2.0 granted and 0.3.0 does not
(`controllerrevisions`, `podtemplates`, `volumeattachments`, the CSI kinds) own
nothing in practice, so this is a difference on paper rather than one you
should meet.
