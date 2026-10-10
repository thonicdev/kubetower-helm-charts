# kubetower-helm-charts

Helm charts for deploying [KubeTower](https://github.com/thonicdev/kubetower)
into a Kubernetes cluster.

**Not a release yet.** The console's release workflow publishes an image for
each version tag, at `ghcr.io/thonicdev/kubetower:<version>`, and no version
has been tagged yet; there is no chart repository either. You install from this
checkout, naming the image version (or your own build). Everything below says
what it does and what it does not, because the gap between those two is the
whole point of reading a chart.

## What is here

| Path | What |
|---|---|
| `charts/kubetower/` | The console in a pod |
| `test/oidc/` | A Dex fixture for developing single sign-on against. Not part of the chart |

## The one sentence that matters

**This chart deploys the single-operator console remotely. It does not deploy a
shared multi-user console, because there is not one yet.**

The console was written to run on one person's machine, against their
kubeconfig. Putting it in a pod moves where it runs; it does not change what it
is. Every consequence follows from that:

- **One identity towards the cluster.** The console makes every request as the
  pod's ServiceAccount, so two people signed in are one subject in the API
  server's audit log. With `auth.oidc` set, it first asks the cluster - a
  SubjectAccessReview naming the person and their groups - whether *they* may
  read or change the thing, and refuses when they may not. The shared password
  names nobody, so its holder gets the ServiceAccount's own rights.
- **One password**, and a stateless session cookie. Signing one person out means
  rotating `KT_SESSION_SECRET`, which signs everybody out. **With `auth.oidc`
  set, this one changes**: people sign in through the identity provider as
  themselves, each with a session of their own. The shared password is then
  gone unless `auth.localAccount` keeps it. **And everybody the provider will
  issue a token to for this client is admitted** — the console keeps no list
  of who may sign in, so each of them gets whatever the cluster's RBAC grants
  them, including what it grants `system:authenticated`, up to the pod's own
  role. Restrict who may use the client at the provider.
- **The terminal is the pod's terminal.** With `rbac.exec` on, anybody who knows
  the password gets a shell in any pod, holding this ServiceAccount.
- **The port-forward cap is per process**, so its 16 forwards are shared by
  everyone signed in.

Install it for one operator, reached over `kubectl port-forward`. Reaching it
any other way is untested and the list above is why.

## Several replicas

`replicaCount` above one also deploys a small Valkey - a StatefulSet of one,
from the official `valkey/valkey` image, pinned by digest - through which the
consoles share their sessions, their confirmation plans and a short-lived
marker per completed sign-in, so that a sign-in code cannot be used twice. A
sign-in in progress is not stored there: it travels in a sealed cookie in the
browser, so a sign-in started through one pod finishes through another. A
session opened on one pod is valid on the next, and a sign-out through either
ends it on both. Sessions and plans are sealed by the console with its session
key before they are written, and a marker is only a hash of the sign-in it
closes, so the Valkey password alone reads no session and forges none.
Nothing is written to disk: a restart of the Valkey pod signs everybody out,
which is what a restart of a single console costs too.

**What several replicas buy is a console that survives losing one of them.**
The chart spreads them across nodes (`kubernetes.io/hostname`,
`ScheduleAnyway`, replaced by `topologySpreadConstraints` if you set it) and
gives them a PodDisruptionBudget of `maxUnavailable: 1`, so a node drain or an
upgrade takes them one at a time. **What it does not buy is a store that
survives**: Valkey is one pod, and its eviction - a drain of its node, an
upgrade of its image, a node lost - signs everybody out. It has no
PodDisruptionBudget, deliberately: a budget on a single pod blocks the drain of
its node outright, which would trade one sign-in for a stuck node.

**What several replicas do not offer**, because each console's state directory
is its own: the rail, column and density preferences cannot be changed (every
replica draws the defaults), the settings are read-only, port-forwards are off,
and there is no archive. The console does not register those routes at all.

**The chart refuses, at install**, the combinations that would half-work:

| With `replicaCount > 1` | Why it is refused |
|---|---|
| `persistence.enabled: true` | One ReadWriteOnce volume is mounted by one node at a time; a ReadWriteMany one would be several processes rewriting each other's files from memory |
| `serverProfile: false` | The desktop profile is one operator's console; several of it are several consoles |
| The shared password offered with no `auth.passwordHash` | Each pod would draw its own password |
| `valkey.existingSecret` with an empty `valkey.existingSecretKey` | The key holding the password would be unknown |
| `valkey.existingSecret` with one replica | A setting read by nothing |

**The Valkey password is drawn once and kept across upgrades**, by reading back
the Secret the first install wrote (`lookup`). So is the console's session key.
That works only when Helm talks to a cluster: `helm template`, `--dry-run` and
GitOps tools that render without one (Argo CD among them) cannot see either
Secret and draw both anew on every render. Valkey and every console carry a
checksum of the Secrets they read, so a changed one restarts all of them
together — the alternative, pods started after the change holding the new
password and key while the running ones hold the old, fails requests on
whichever pod is out of step. Restarting together still signs everybody out on
every sync. **With those tools, create the Secrets yourself**: set
`valkey.existingSecret`, and `auth.sessionSecret` or `auth.existingSecret`.

The password may contain any bytes: the init container writes it escaped into
Valkey's configuration, and `test/valkey-password-check.py` runs the rendered
script in the pinned image with a quote, a backslash, a newline and an attempt
to add a directive. An empty password is refused, because an empty
`requirepass` turns authentication off, and so is one above 16384 bytes, which
Valkey would start with and then never authenticate.

**Valkey's password is readable by everybody who signs in**, with the default
`rbac.readSecrets`, and nothing restricts who can connect to Valkey. The
sessions and plans there are sealed with the session key — which that same read
reaches in this release's own Secret. So the Valkey password adds the power to
delete every record, which signs everybody out and forgets which sign-in codes
were used, and nothing the Secrets read had not already given.

```bash
helm install kubetower ./charts/kubetower -n kubetower --create-namespace \
  --set image.tag=<version> \
  --set replicaCount=2 --set persistence.enabled=false \
  --set auth.oidc.issuer=... --set auth.oidc.clientID=... --set auth.oidc.redirectURL=... \
  --set networkPolicy.enabled=true
```

`networkPolicy.enabled` admits Valkey's port from this release's consoles and
from nothing else, so its password alone is no longer enough to connect - on a
network plugin that enforces NetworkPolicy. Not done yet: TLS to Valkey, and a
Valkey that survives its own restart.

## The image

The default is `ghcr.io/thonicdev/kubetower`, which is where the console's
release workflow pushes one image per tag `v<version>`, tagged `<version>`.
There is no `latest`. The tag defaults to the chart's `appVersion`, and while
that is the `0.0.0-dev` placeholder **the chart refuses to render without
`image.tag`**, rather than install an image no registry holds and leave the pod
in `ImagePullBackOff`.

**The image is private while `thonicdev/kubetower` is**: a package on ghcr.io
inherits its repository's access. Pulling it then needs a token with
`read:packages` from somebody who can read that repository:

```bash
kubectl -n kubetower create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>
# then: --set image.pullSecrets[0].name=ghcr-pull
```

Or build it yourself and make it reachable from your cluster:

```bash
git clone https://github.com/thonicdev/kubetower && cd kubetower
DOCKER_BUILDKIT=1 docker build --build-arg KT_VERSION=helm-test -t kubetower:helm-test .
# kind:           kind load docker-image kubetower:helm-test
# minikube:       minikube image load kubetower:helm-test
# Docker Desktop: nothing, the node already sees the daemon's images
# and install with --set image.repository=kubetower --set image.tag=helm-test
```

## Install

```bash
helm install kubetower ./charts/kubetower \
  --namespace kubetower --create-namespace \
  --set image.tag=<version>
```

Read the drawn password out of the log, port-forward, sign in — `NOTES.txt`
prints all three commands with your values filled in.

## Upgrading

Upgrade with `helm upgrade --reset-then-reuse-values` (Helm 3.14 or later), or
pass your values file again with `-f`. Plain `--reuse-values` keeps only the
values the previous release stored, so any key a newer chart adds is missing,
and the render comes out wrong or fails.

## What it deploys

ServiceAccount, ClusterRole + ClusterRoleBinding, Secret (session key, and the
password hash if you set one), PersistentVolumeClaim (the state directory),
Deployment, Service. A ConfigMap holding a kubeconfig only for the desktop
profile (`serverProfile: false`). Ingress only if you ask, and it is off
because an Ingress in front of this publishes cluster credentials behind one
shared password. NetworkPolicies only if you ask (`networkPolicy.enabled`).

With `replicaCount` above one, no PersistentVolumeClaim, a PodDisruptionBudget
for the consoles, and four more objects for the store they share: a Valkey
StatefulSet of one, its Service, its ConfigMap (the configuration without the
password) and its Secret (the password, unless `valkey.existingSecret` names
yours).

### The pod

- **A read-only root filesystem.** The console writes to its state directory,
  to `$HOME` (the caches its tools keep, and the assistant's configuration in
  `~/.claude`, which it writes at every start) and to `/tmp` (the terminal's
  work root and its per-context homes); each is a volume, the emptyDirs bounded
  by `scratchSizeLimits`, and nothing else is writable. `/tmp` and
  `/home/kubetower` are the chart's own mounts, so an `extraVolumeMounts` entry
  at either is a duplicate the API server refuses. If something you add writes
  elsewhere, mount a volume there; `securityContext.readOnlyRootFilesystem=false`
  is the way back. It runs as uid 100, drops every capability, cannot escalate, uses
  the runtime's default seccomp profile, and gets a projected ServiceAccount
  token that expires after an hour instead of the legacy one.
- **Shutdown.** On deletion, `preStop` pauses five seconds so the endpoint's
  removal reaches the Service and the ingress before the listener closes; then
  SIGTERM, and the console gives in-flight requests five seconds.
  `terminationGracePeriodSeconds` (15) covers both, and the chart refuses one
  that does not. A log stream or a pod shell open at that moment is cut, and
  the browser has to open it again.
- **Probes.** A startup probe, liveness on `/healthz` and readiness on
  `/readyz`, each with a timeout. Liveness asks only whether the console is
  serving, so a console whose clusters are unreachable is not restarted.
  Readiness follows the console's own judgement: whether its clusters answer -
  under the server profile, the API server the pod runs in - and, in console
  versions that check it, whether its audit log can still be written. A replica
  that cannot record what people do leaves the Service. Whether a brief
  API-server error should count is the console's decision, made in `/readyz`;
  the chart does not work around it.
- **Memory.** `GOMEMLIMIT` is set to `goMemLimitPercent` (90) of the memory
  limit, so the Go runtime collects harder near the limit instead of being
  killed at it.

### Without persistence

`persistence.enabled: false` puts the state directory on an emptyDir, and the
chart tells the console so (`KT_STATE_PERSISTENT`) - it cannot see a volume
for itself. The console then offers nothing that would not survive a restart:
no event archive, no archive settings and no node-shell image setting, absent
from the Settings page and from its API rather than offered and forgotten. The
rail and the column layouts still work, for as long as the pod runs.

**It is refused with a password the console would draw for itself**, because
every restart would draw a new one. Turning persistence off needs
`auth.passwordHash`, `auth.existingSecret`, or single sign-on with
`auth.localAccount: false`. `test/persistence-check.py` holds both halves.

### How the pod reaches the API server

Under the server profile, the default, the console reads the pod's own
ServiceAccount - `rest.InClusterConfig()`, over the **projected, expiring
token** the Deployment mounts - and does not read `KUBECONFIG` at all. So the
chart renders no kubeconfig for it.

The desktop profile (`serverProfile: false`) reads only `KUBECONFIG`. For it,
the chart writes a kubeconfig pointing at `kubernetes.default.svc`, with the
API server's CA and the same token referenced as `tokenFile`, so client-go
re-reads it as it rotates; or mounts your own with `kubeconfig.generate:
false`.

### RBAC

Read-only, cluster-wide, by default — derived from what the code reads, not from
`cluster-admin` and not from the built-in `view` role, which excludes Secrets,
which the Secrets page and the Helm release list both need.

One rule is not read-only and is always there: `create` on
`subjectaccessreviews`, which lets the console check each person's rights
before acting, rather than acting with its own. It asks the cluster a question
and grants nothing.

Four switches are off and each one is a decision rather than a default:
`rbac.write`, `rbac.exec`, `rbac.nodeProxy`, `rbac.prometheusProxy`.
`rbac.customResources` is off too — the custom-resource browser needs a
wildcard read, and a wildcard is worth typing out deliberately.

| Switch | What the console does with it |
|---|---|
| `rbac.write` | Edit, create, delete; restart, scale, suspend, resume, trigger; cordon, uncordon and drain a node (`patch` on `nodes`); evict a pod (`create` on `pods/eviction`) |
| `rbac.exec` | A shell in a pod, a port-forward, and the fallback route to Prometheus |
| `rbac.prometheusProxy` | Prometheus through the API server's service proxy (`get` on `services/proxy`), which is a proxy to every Service in the cluster unless `rbac.prometheusProxyServices` names yours as `<name>:<port>` |
| `rbac.nodeProxy` | Per-node usage from the kubelet where there is no metrics-server |

What two of them amount to, in plain terms: **`rbac.write` is cluster-admin on
most clusters**, because creating a pod in a namespace means running as any
ServiceAccount there; **`rbac.nodeProxy` is a shell on every node**, because
the kubelet API it reaches runs commands in pods. And the default read of
Secrets includes this release's own, so anybody who signs in can read the
session key and mint a session of their own.

`test/rbac-check.py` refuses an escalation verb (`impersonate`, `escalate`,
`bind`), a wildcard verb, and any wildcard but the custom-resource one. That is
all it checks: it passes with every switch on, so a green run says nothing about
whether the switches you chose are safe.

`test/console-needs-check.py` checks the other direction: every request in
`test/console-needs.yaml` - what the console asks the cluster for, each with
the source file in the console's repository it comes from - is granted when
its switch is on, and the off-by-default switches grant nothing when off. The
list is kept by hand, so it catches a grant that goes missing, not a request
the console starts making that nobody added to it.

`charts/kubetower/templates/rbac.yaml` names the source file each rule exists
for.

## The server profile

`serverProfile` passes `--incluster` to the console, and it is **on by
default**. It **removes** routes; it adds nothing. `--set serverProfile=false`
deploys the desktop profile instead, shell included.

| | off | on (default) |
|---|---|---|
| Local shell, node shell, assistant | served | **not registered** — the address answers what a path that never existed answers, not a refusal |
| Pod exec, port-forward, everything else | served | served |
| `/api/capabilities` `mode` | `container` | `shared` |

**"What a path that never existed answers" is not one status**, and the
difference is worth knowing before you go looking. Measured on Docker Desktop,
2026-09-22, against a pod running the profile, with controls:

| | `/api/…` | `/kt-api/…` |
|---|---|---|
| `GET` | 404, `application/problem+json` | **200 `text/html`, the single-page shell** |
| `POST` | 404 | 405 |

Byte-identical to `/definitely-not-a-route` in every cell. The `/kt-api/` paths
are not under the API mount, so they fall through to the console's own
single-page handler — **so a `200` there is the removal working, not failing.**

And the control that makes the rest mean anything: `/api/c/<cluster>/ws/exec`
answers `400 namespace and pod must be Kubernetes names`, from its own handler.
The 404s are the router holding nothing, not the surface being down. `kubectl exec` is then the only shell into the pod, bounded by
your RBAC rather than by the console's own switches.

**Turning it on does not change who the cluster sees.** It makes the console
smaller, and makes it read the pod's own ServiceAccount; every request is still
that ServiceAccount's, so the warning above stands either way. It is on by
default because the alternative was the wrong way round: the shell the desktop
profile keeps is a shell holding this pod's ServiceAccount.

## Single sign-on: the name a RoleBinding names

With `auth.oidc` set, the console checks a person's rights with the cluster
under a name, and **a RoleBinding has to name that string, prefix included**.
The prefix is `auth.oidc.usernamePrefix`, which is kube-apiserver's
`--oidc-username-prefix` with the same default and the same spelling of "none":

| `auth.oidc.usernamePrefix` | The name a RoleBinding names |
|---|---|
| unset (the default) | the issuer and `#` before the claim: `https://idp.example/dex#CiQw...`. With the `email` claim, which is the default claim, the bare address: `alice@example.com` |
| `-` | the bare claim, whatever it is. It has to be typed out: an empty value is what you get by not setting anything, so it cannot also mean a decision |
| anything else, `oidc:` say | that string before the claim: `oidc:alice@example.com` |

In a values file, quote it: `usernamePrefix: "-"`, `usernamePrefix: "oidc:"`.
Unquoted, `-` and `oidc:` are YAML errors, and anything after a ` #` is read
as a comment and silently dropped.

So with `usernameClaim: sub` and nothing else, a RoleBinding written for
`CiQw...` matches nobody; it has to name `https://idp.example/dex#CiQw...`.
NOTES prints which form your values produce, and the console logs it at
start-up.

```yaml
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata: { name: alice-edit, namespace: team-a }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: edit }
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: User
    name: "https://idp.example/dex#CiQw..."   # the prefixed name, not the bare claim
```

Why a prefix at all: without one, `alice` from the provider is the `alice` of a
client certificate or of another authenticator, and a RoleBinding written for
one grants the other. Why kube-apiserver's default rather than a stricter one:
if your API server trusts the same issuer, the console asserting the same
string is what lets one RoleBinding mean the same person to `kubectl` and to
the console. **That default is the `--oidc-*` flags'.** An API server configured
through structured authentication (`AuthenticationConfiguration`) has no
default, so set this value to its `claimMappings.username.prefix`. **The claim
has to match as well**: kube-apiserver's `--oidc-username-claim` defaults to
`sub` and the console's `usernameClaim` to `email`, so set
`auth.oidc.usernameClaim` to the API server's claim, or to its
`claimMappings.username.claim` under structured authentication.

A prefix that would start with `system:` - Kubernetes' own users - is refused
by the console at start-up, and by the chart at render, with the same rule:
compared trimmed and case-insensitively, the user-name prefix as it will be
applied (`-` is never refused; unset is the issuer and `#`), and only when an
issuer is set. The groups prefix is refused the same way. Changing the prefix or the claim changes everyone's
name: people signed in under the old one sign in again, and RoleBindings
written for it match nobody. The value needs a console image that knows
`KT_OIDC_USERNAME_PREFIX`; an older one ignores it.

## The OIDC fixture

`test/oidc/` is a [Dex](https://dexidp.io), for developing single sign-on
against a real issuer. **It is not part of the chart.** A console image built
with single sign-on signs in through it with the `auth.oidc` values below.

```bash
kubectl apply -f test/oidc/dex.yaml
kubectl -n dex port-forward svc/dex 5556:5556 --address 0.0.0.0
python test/oidc/try.py            # opens http://localhost:5555, a raw token
```

And the console itself, with its client secret in a Secret rather than in the
values:

```bash
kubectl -n kubetower create secret generic kubetower-oidc --from-literal=client-secret=kubetower-dev-secret
helm upgrade --install kubetower ./charts/kubetower -n kubetower \
  --set auth.oidc.issuer=http://host.docker.internal:5556/dex \
  --set auth.oidc.clientID=kubetower \
  --set auth.oidc.clientSecret.existingSecret=kubetower-oidc \
  --set auth.oidc.redirectURL=http://127.0.0.1:8824/api/auth/oidc/callback \
  --set auth.oidc.scopes=openid\,email\,profile\,groups \
  --set auth.oidc.groupsPrefix=oidc:
kubectl -n kubetower port-forward svc/kubetower 8824:5823   # then http://127.0.0.1:8824
```

Two connectors, and the difference between them is the finding:

| Log in with | Claims |
|---|---|
| **Mock** | `email`, and `groups: ["authors"]` |
| **Email** (`alice@example.com` / `password`) | `email`, **no `groups`** |

Dex's static password database carries no groups. So a group-to-RBAC mapping
cannot be developed against static users — it needs the mock connector, or a
real identity provider.

**The issuer is the trap.** A token's `iss` must be the exact string the console
expects, and the browser and the pod reach the provider at different addresses.
The fixture's issuer is `http://host.docker.internal:5556/dex`, which Docker
Desktop resolves both on the host and inside the cluster. So one string works
for both sides, and `--address 0.0.0.0` is there because the browser dials the
host's own address rather than loopback. That publishes Dex on the host's
network while the port-forward runs, so stop it afterwards. Where the two
addresses really differ, `auth.oidc.discoveryURL` is where the pod fetches
discovery from, and the issuer stays what the browser sees.

## What this chart does not do

- No chart repository and no `helm package`. The image is published per
  console release; the chart is not.
- No per-person identity towards the cluster. With `auth.oidc` set, the
  console asks the cluster what each person may do before acting, but the
  request itself is the pod's ServiceAccount's, so the API server's audit log
  names the ServiceAccount. Everyone the provider issues a token to for the
  client can sign in. Restrict it at the provider; nothing here can. No
  multi-tenancy. And no group-to-permission mapping, ever: a group reaches a
  right through a RoleBinding the cluster's owner writes, not through a value
  here.
- No egress NetworkPolicy, and no NetworkPolicy at all by default. The
  optional one is ingress only. The console legitimately talks to the API
  server, the identity provider, Prometheus and whatever a forward points at; an
  egress policy honest about that permits nearly everything, and one that is not
  breaks the console.
- No high availability of the store. `replicaCount` defaults to 1, with
  `Recreate`, because the state directory is ReadWriteOnce and the forward table
  is in memory. Above one, the consoles roll with `RollingUpdate` and share a
  single Valkey pod that keeps nothing on disk: losing it signs everybody out.
- No HorizontalPodAutoscaler: the consoles hold no load worth scaling on, and
  each replica drops what only one pod can hold. No PodDisruptionBudget with one
  replica, where it could only block a drain or do nothing.

## Licence

AGPL-3.0-or-later, the same as the console. See `LICENSE`.
