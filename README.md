# kubetower-helm-charts

Helm charts for deploying [KubeTower](https://github.com/thonicdev/kubetower)
into a Kubernetes cluster.

**Not a release yet.** The console's release workflow publishes an image for
each version tag, at `ghcr.io/thonicdev/kubetower:<version>`, and this
repository's publishes the chart for each chart tag, at
`oci://ghcr.io/thonicdev/charts/kubetower`. The console has release
candidates and no release yet, and no chart has been published: the first
chart waits for the first console release, since a chart naming no released
image installs nothing. Until then you install from this checkout, naming the
image version (or your own build); [Install](#install) has both ways, and the
registry one works unchanged once a chart is published. Everything below says
what it does and what it does not, because the gap between those two is the
whole point of reading a chart.

## What is here

| Path | What |
|---|---|
| `charts/kubetower/` | The console in a pod |
| `charts/kubetower/console-needs.yaml` | What the console asks the cluster for, request by request. The `ClusterRole` is rendered from it |
| `dev/values.yaml` | The values a development install in a local cluster uses, with the fixture below |
| `test/oidc/` | A Dex fixture for developing single sign-on against. Not part of the chart |
| `.github/workflows/release.yml` | Publishes the chart on a tag `kubetower-<version>` |

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
inherits its repository's access. Pulling it then needs a pull secret holding
a token with `read:packages` from somebody who can read that repository;
[Install](#install) shows both commands.

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

### The chart's version is not the console's

`version` in `Chart.yaml` is the chart's own. It moves whenever the chart
changes, and it is what `--version` names. `appVersion` is the console release
that chart version was made for: it is the default `image.tag`, and the
console whose requests the `ClusterRole` was derived from
(`console-needs.yaml`). So a chart version installs its `appVersion` unless
you set `image.tag`. Read both before installing:

```bash
helm show chart oci://ghcr.io/thonicdev/charts/kubetower --version <chart version>
```

`image.tag` set to another console version runs that console under this
chart's role. A newer console may ask the cluster for something the role does
not grant, and the control that asks then answers 403. Prefer the chart version
whose `appVersion` is the console you want.

### From the registry

The chart is an OCI artefact: `helm install` reads it directly, with no
`helm repo add` and no clone.

**1. A pull secret, while the image is private.** A classic personal access
token with the `read:packages` scope, from an account that can read
`thonicdev/kubetower`, in a Secret of type `kubernetes.io/dockerconfigjson`
in the release's namespace:

```bash
kubectl create namespace kubetower
kubectl -n kubetower create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=<github user> --docker-password=<token>
```

The chart's value for the pod's `imagePullSecrets` is `image.pullSecrets`.

**2. A values file.** With single sign-on, which the chart is meant to run
with once more than one person reaches it:

```bash
# The client secret, only for a confidential client. Never a value in the file.
kubectl -n kubetower create secret generic kubetower-oidc --from-literal=client-secret=<secret>
```

```yaml
# kubetower-values.yaml
image:
  # Empty is the chart's appVersion. Required while appVersion is the
  # 0.0.0-dev placeholder, which names no image.
  tag: ""
  pullSecrets:
    - name: ghcr-pull

auth:
  oidc:
    issuer: https://idp.example.com/realms/main
    clientID: kubetower
    clientSecret:
      existingSecret: kubetower-oidc
      key: client-secret
    # The callback as the browser reaches it, registered at the provider byte
    # for byte. Through a port-forward: http://127.0.0.1:5823/api/auth/oidc/callback
    redirectURL: https://kubetower.example.com/api/auth/oidc/callback
    scopes: "openid,email,profile,groups"
    groupsClaim: groups
```

**With an issuer set, password sign-in is off** unless `auth.localAccount` is
true: the console registers no password route and draws no password, so the
only way in is the provider. The other single sign-on values -
`usernameClaim`, `usernamePrefix`, `groupsPrefix`, `discoveryURL`,
`sessionMax` - are described in `values.yaml`, and the name a RoleBinding has
to use is in [Single sign-on](#single-sign-on-the-name-a-rolebinding-names).

**3. Install:**

```bash
helm install kubetower oci://ghcr.io/thonicdev/charts/kubetower --version <chart version> \
  --namespace kubetower -f kubetower-values.yaml
```

`NOTES.txt` then prints how to reach it and sign in, with your values filled
in. Without single sign-on that means reading the drawn password out of the
log, port-forwarding and signing in.

### From a checkout

Until a chart version is published, or to install a change of your own, the
same values file works from this repository:

```bash
helm install kubetower ./charts/kubetower \
  --namespace kubetower --create-namespace \
  -f kubetower-values.yaml --set image.tag=<console version>
```

### Password sign-in behind an ingress: `auth.trustedProxies`

Only when password sign-in is on - no `auth.oidc.issuer`, or
`auth.localAccount: true` - **and** the console is reached through an ingress.
Every attempt then arrives from the ingress controller's address, so the
console cannot tell clients apart: it keeps no per-client attempt budget,
logs every failure against the controller, and a browser signing in for the
first time shares one process-wide queue with whoever floods it. The console
says so in its log at start.

`auth.trustedProxies` names the proxies whose `X-Forwarded-For` the console
believes, and is rendered as `KT_TRUSTED_PROXIES`. **Name the ingress
controller's own addresses, and nothing else.** The console believes the
header from any connecting address the list covers, so each of them can claim
to be any client: it picks its own attempt budget and chooses which address the
failure log names. Name the controller's pods as narrowly as your network
allows.

**A wide range, such as the cluster's whole pod CIDR, is safe only together
with a NetworkPolicy that lets nothing but the ingress controller reach the
console** - `networkPolicy.enabled` with `consoleFrom` narrowed to the
controller's namespace, on a network plugin that enforces it. Without that,
any pod that can reach the console's Service can write the header:

```yaml
auth:
  trustedProxies: ["10.244.0.0/16"]   # the pod CIDR, so only with the policy below
networkPolicy:
  enabled: true
  consoleFrom:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: ingress-nginx
```

A controller on `hostNetwork` connects from the nodes' addresses. Naming the
nodes' range trusts everything that runs with host networking on any node,
and no NetworkPolicy narrows that.

The value is a list, or one string separated by commas, spaces or newlines.
Empty, the variable is not set. With password sign-in on, an entry that is
neither an address nor a CIDR range stops the console at start, naming the
entry. With single sign-on and no `auth.localAccount` there is no password
sign-in: the console does not read the value at all, so it has no effect and a
malformed entry goes unnoticed.

If `extraEnv` sets `KT_TRUSTED_PROXIES` as well, the `extraEnv` entry wins and
the chart leaves `auth.trustedProxies` out, as it does for `GOMEMLIMIT`: two
entries of one name in a container's environment do not apply cleanly. NOTES
says so when both are set. Moving over from `extraEnv` is one upgrade: set
`auth.trustedProxies` and remove the `extraEnv` entry in the same values.

### Extra pages: `extras`

The console's extra pages - pages that are not a resource kind - are off
until the deployment names them, by key:

```yaml
extras: [first-page, second-page]
```

or `--set extras='{first-page}'`. Which keys exist depends on the console's
build; the console names the ones it carries, on its Settings page and in its
start-up log when it is given a key it does not know. The chart writes the list into a ConfigMap,
`<release>-extras`, and the Deployment reads it as `KT_EXTRAS`
(`valueFrom.configMapKeyRef`). The console reads that variable once, at
start, so the pod template carries a checksum of the ConfigMap and changing
the list restarts the pod. Empty, the default, neither the ConfigMap nor the
variable is rendered.

Every key named is on, for every cluster the console shows. Under the server
profile the list is the only switch: the console's Settings page shows each
extra page its build carries, on or off, as the deployment set it, with no
control. A key the console's build does not carry is logged at start and
ignored. The value is a list of lower-case keys; a string, or a key holding a
comma or an `=`, is refused before anything renders.

If `extraEnv` sets `KT_EXTRAS` as well, the `extraEnv` entry wins and the chart
leaves `extras` out, as it does for `GOMEMLIMIT` and `auth.trustedProxies`;
NOTES says so when both are set.

### Settings of the extra pages: `extraSettings`

An extra page may offer settings of its own, each on or off - the Argo CD page's
column in the console's resource lists, for one, which is off by default. Under
the server profile the console offers no control for them, so the deployment
sets them, by page and setting key:

```yaml
extraSettings:
  argocd:
    list-column: true
```

or `--set extraSettings.argocd.list-column=true`. Which settings exist depends
on the console's build: its Settings page lists each page's settings, and its
start-up log names a setting it does not know and ignores it. The chart writes
them into the same ConfigMap as `extras`, as `KT_EXTRA_SETTINGS`
(`argocd.list-column=on`), and the Deployment reads that key by reference;
changing a setting restarts the pod, as changing the list does. Empty, the
default, the variable is not rendered and every setting keeps its default.

Each setting named has that value for every cluster the console shows, and the
Settings page draws it as set by the deployment. The value is a map of page to
setting to `true` or `false`; a string, a number or an upper-case key is
refused before anything renders. If `extraEnv` sets `KT_EXTRA_SETTINGS` as
well, the `extraEnv` entry wins and NOTES says so.

### When the audit log stops writing

The console records every act it performs in its audit log, on its standard
output. If a record cannot be written - a write that fails, comes up short or
stalls - the console stops acting: that replica refuses every write, exec,
forward and reveal, and `/readyz` answers not ready with the reason, so the
readiness probe takes it out of the Service. **It does not recover inside the
process.** Fix the cause - a full disk, or a stdout or log pipe that nothing
drains - and restart. This restarts every replica of the Deployment:

```bash
kubectl -n <namespace> rollout restart deployment/<release>
```

and `kubectl -n <namespace> delete pod <pod>` restarts only the one affected.

`<release>` stands for the Deployment's name. It is `fullnameOverride` when
that is set. Otherwise it is built from the release name and the chart's name,
which is `nameOverride` when that is set and `kubetower` otherwise: the release
name alone when it already contains that name, as in the commands above, and
`<release>-<name>` when it does not. `NOTES.txt` prints the command with the
name filled in.

## Publishing

A tag `kubetower-<version>` runs `.github/workflows/release.yml`, which runs
`.github/scripts/publish-chart.sh`. Before anything is pushed it refuses:

- a tag that is not the version in `Chart.yaml`;
- a tagged commit that is not on `main`;
- the placeholder `appVersion`;
- an `appVersion` that is not a semantic version (`MAJOR.MINOR.PATCH`,
  optionally `-prerelease`): it is the published chart's default image tag;
- a role read from another console than the one the chart installs: the
  console's tag `v<appVersion>` must be the commit `console-needs.yaml` was
  derived from, read from the console's repository, and a readable repository
  with no such tag is refused too;
- a console repository that cannot be read for any reason other than a
  missing credential - a name that does not resolve, a server error, a rate
  limit, a timeout. Each is a release that could have been checked, and a
  published version cannot be taken back;
- a version the registry already holds. A published version is never
  replaced; bump the chart's version instead.

**One answer is a warning, not a refusal: the missing-credential one.** While
`thonicdev/kubetower` is private, this public repository's release holds no
credential for it, deliberately, so GitHub answers "could not read Username"
(a 401 with nothing to offer), and the tie between the role and the console
cannot be checked. A 403, or a credential offered and rejected, is refused. The release then says so - a warning annotation, and a line in the
run's summary naming `derivedFrom.commit` as committed - and publishes. It
never reports that case as checked. Until the console is public, whoever pushes
the chart tag makes that check by hand: `derivedFrom.commit` must be the commit
the console's `v<appVersion>` tag names. Once it is public, the tag is readable
and the equality is enforced again with no change here. One limit: without a
credential, GitHub gives the same answer for a repository that does not
exist, so a typo in the console's URL would also only warn. That URL is a
constant in `publish-chart.sh`, reviewed like any other line.

Then it packages the chart, pushes it to `oci://ghcr.io/thonicdev/charts`, pulls
it back, checks it is byte for byte what was pushed, and renders it. After the
registry login is gone, `.github/scripts/read-chart-anonymously.sh` reads it
once more with no login, so a package nobody else can read fails the release
instead of the first install. `chart.yml` runs both scripts on every pull
request against a registry that lives only for the job, and proves each
refusal, the missing-credential warning (from a local server answering 401)
and a matching tag's tie, so a release
takes no step CI has not taken. The job holding the
package token installs nothing and runs only actions pinned to a commit.
Nobody has pushed a tag yet.

## Upgrading

Upgrade with `helm upgrade --reset-then-reuse-values` (Helm 3.14 or later), or
pass your values file again with `-f`. Plain `--reuse-values` keeps only the
values the previous release stored, so any key a newer chart adds is missing,
and the render comes out wrong or fails.

### To 0.3.0: the role grants only what the console asks for

The `ClusterRole` is rendered from `charts/kubetower/console-needs.yaml` (see
[RBAC](#rbac)), and an upgrade removes every grant the console never uses:
`watch`; `update`; the `*/scale` and `*/status` subresources; `pods/attach`;
`nodes/stats` and `nodes/metrics`; `get` on CustomResourceDefinitions and on
`metrics.k8s.io`; `list` on `pods/log`; and every read of
`controllerrevisions`, `podtemplates`, `volumeattachments`, `csidrivers`,
`csinodes`, `csistoragecapacities` and `events.k8s.io` events.

The console itself loses nothing. **Under `serverProfile=false`, the shell in
the pod runs with this role too**, and these stop working there:

- `kubectl get -w`, `kubectl rollout status`, `kubectl wait`, and anything
  built on a watch or an informer;
- `kubectl replace`, `kubectl apply --force`, and anything else that sends an
  update;
- `kubectl scale` (the `scale` subresource);
- `kubectl attach`;
- `kubectl rollout history` and `kubectl rollout undo` on StatefulSets and
  DaemonSets (`controllerrevisions`);
- `kubectl top pod NAME` and `kubectl top node NAME` (`get` on
  `metrics.k8s.io`); the unnamed forms list, and still work;
- `kubectl get crd NAME`; `kubectl get crd` still lists.

If that shell needs them, bind a role of your own to the release's
ServiceAccount. The chart will not grant what the console does not ask for.

## What it deploys

ServiceAccount, ClusterRole + ClusterRoleBinding, Secret (session key, and the
password hash if you set one), PersistentVolumeClaim (the state directory),
Deployment, Service. A ConfigMap holding a kubeconfig only for the desktop
profile (`serverProfile: false`), and one holding `KT_EXTRAS` and
`KT_EXTRA_SETTINGS` only when `extras` names an extra page or `extraSettings` a
setting. Ingress only if you ask, and it is off
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

Read-only, cluster-wide, by default — rendered from what the console asks for,
not from `cluster-admin` and not from the built-in `view` role, which excludes
Secrets, which the Secrets page and the Helm release list both need.

`charts/kubetower/console-needs.yaml` lists every request the console makes:
its API group, resource and verbs, the switch below that grants it, and the
file in [the console's source](https://github.com/thonicdev/kubetower) that
makes it. `templates/rbac.yaml` writes that list out as rules and adds nothing,
so a grant that is not a line there does not exist. That is why the role has no
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

`test/console-needs-check.py` holds the role to the list: for each switch, and
for every switch on and every switch off, the rendered role must grant each
request whose switch is on and nothing else, and the defaults nothing behind an
off-by-default switch.

`test/derive-needs.py` holds the list to the console: it reads the console's Go
source at the commit the list names and fails when the source makes a request
the list lacks, or the list names one the source never makes. Writes are the
exception, by design: the editor reaches any kind the console lists, so the
kinds `rbac.write` grants are this chart's choice, and the check only asks that
each be a kind the console lists and a verb it writes with. CI runs it wherever
the console's repository is readable from the workflow, and says NOT RUN where
it is not. Locally, with the console checked out beside this repository:

```bash
python test/derive-needs.py --core ../kubetower
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

## The server profile

`serverProfile` passes `--incluster` to the console, and it is **on by
default**. It **removes** routes; it adds nothing. `--set serverProfile=false`
deploys the desktop profile instead, with its local shell, and that shell runs
kubectl as the release's ServiceAccount with only the role above - see
[Upgrading to 0.3.0](#to-030-the-role-grants-only-what-the-console-asks-for)
for what that role does not allow.

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

## A development install

`dev/values.yaml` is the environment a console under development runs in, on
Docker Desktop's Kubernetes: the server profile, the fixture below as identity
provider, the shared account beside it (`dev` / `dev`), every custom resource
readable (`rbac.customResources`, which the chart's default leaves off), and a
`kubetower:dev` image built from a checkout of the console and never pulled.
Every credential in it is public; it is for a laptop and nothing else.

The fixture's static users sign in with `password`, not `dev`.
`nobody@example.com` is the one no RoleBinding ever names: signed in as it, the
console shows what a person the cluster's owner has not bound may see, which the
shared account - acting with the console's own rights - cannot.

```bash
# in a checkout of the console
docker build --build-arg KT_VERSION=dev -t kubetower:dev .
# here
kubectl apply -f test/oidc/dex.yaml
kubectl -n dex port-forward svc/dex 5556:5556 --address 0.0.0.0
helm upgrade --install kubetower ./charts/kubetower \
  --namespace kubetower --create-namespace -f dev/values.yaml
kubectl -n kubetower port-forward svc/kubetower 8824:5823   # then http://127.0.0.1:8824
```

`chart.yml` renders the chart with this file and fails if it stops agreeing with
the fixture: the issuer, the client, its secret and the redirect it registers.
It also fails if the render stops granting the custom-resource read.

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
| **Email** (`nobody@example.com` / `password`) | the same, for a person with no RoleBinding anywhere |

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

- No published chart yet, and no `helm repo add`. The chart is published as
  an OCI artefact, which `helm install oci://...` reads directly; the first
  version waits for the first console release.
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
