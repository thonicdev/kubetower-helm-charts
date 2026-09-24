# kubetower-helm-charts

Helm charts for deploying [KubeTower](https://github.com/thonicdev/kubetower)
into a Kubernetes cluster.

**Not a release yet.** There is no published image and no chart repository
behind this. You build the image, you load it into your cluster, you install
from this checkout. Everything below says what it does and what it does not,
because the gap between those two is the whole point of reading a chart.

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

- **One identity.** The console acts as the pod's ServiceAccount. Two people
  signed in are one subject in the API server's audit log.
- **One password**, and a stateless session cookie. Signing one person out means
  rotating `KT_SESSION_SECRET`, which signs everybody out. **With `auth.oidc`
  set, this one changes**: people sign in through the identity provider as
  themselves, each with a session of their own. The shared password is then
  gone unless `auth.localAccount` keeps it. The line above still holds: they
  all act as the one ServiceAccount. **And everybody the provider will issue a
  token to for this client is admitted**, with the pod's full rights — the
  console keeps no list of who may sign in. With the default read of Secrets,
  a broad provider means every account holder reads every Secret. Restrict
  who may use the client at the provider.
- **The terminal is the pod's terminal.** With `rbac.exec` on, anybody who knows
  the password gets a shell in any pod, holding this ServiceAccount.
- **The port-forward cap is per process**, so its 16 forwards are shared by
  everyone signed in.

Install it for one operator, reached over `kubectl port-forward`. Reaching it
any other way is untested and the list above is why.

## There is no image to pull

Nothing in the console's repository publishes one yet: its release workflow
drafts binaries, its security workflow builds with `push: false`, and its CI
builds an image, probes it and discards it. So you build it and make it
reachable from your cluster:

```bash
git clone https://github.com/thonicdev/kubetower && cd kubetower
DOCKER_BUILDKIT=1 docker build --build-arg KT_VERSION=helm-test -t kubetower:helm-test .
# kind:           kind load docker-image kubetower:helm-test
# minikube:       minikube image load kubetower:helm-test
# Docker Desktop: nothing, the node already sees the daemon's images
```

## Install

```bash
helm install kubetower ./charts/kubetower \
  --namespace kubetower --create-namespace \
  --set image.tag=helm-test
```

Read the drawn password out of the log, port-forward, sign in — `NOTES.txt`
prints all three commands with your values filled in.

## Upgrading

Upgrade with `helm upgrade --reset-then-reuse-values` (Helm 3.14 or later), or
pass your values file again with `-f`. Plain `--reuse-values` keeps only the
values the previous release stored, so any key a newer chart adds is missing
and the render fails.

## What it deploys

ServiceAccount, ClusterRole + ClusterRoleBinding, Secret (session key, and the
password hash if you set one), ConfigMap (the kubeconfig), PersistentVolumeClaim
(the state directory), Deployment, Service. Ingress only if you ask, and it is
off because an Ingress in front of this publishes cluster credentials behind one
shared password.

### The kubeconfig is the trick

The binary reads `KUBECONFIG` and has no in-cluster path of its own. The chart
therefore writes a kubeconfig pointing at `kubernetes.default.svc`, with the API
server's CA and a **projected, expiring ServiceAccount token** referenced as
`tokenFile` so client-go re-reads it as it rotates. That is what
`rest.InClusterConfig()` would have built; here it is built in YAML instead.

When the console learns to read its own ServiceAccount, this ConfigMap is the
first thing to delete.

### RBAC

Read-only, cluster-wide, by default — derived from what the code reads, not from
`cluster-admin` and not from the built-in `view` role, which excludes Secrets,
which the Secrets page and the Helm release list both need.

Three switches are off and each one is a decision rather than a default:
`rbac.write`, `rbac.exec`, `rbac.nodeProxy`. `rbac.customResources` is off too —
the custom-resource browser needs a wildcard read, and a wildcard is worth typing
out deliberately.

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

**Turning it on does not make the console multi-user.** It makes it smaller. One
password and one session are still shared, so the warning above stands either
way. It is on by default because the alternative was the wrong way round: the
shell the desktop profile keeps is a shell holding this pod's ServiceAccount.

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

- No published image, no registry, no chart repository, no `helm package`.
- No per-person authorisation. With `auth.oidc` set, people sign in as
  themselves, but the console does not yet ask the cluster what a person may do,
  so everyone who can sign in acts as the pod's ServiceAccount — and everyone
  the provider issues a token to for the client can sign in. Restrict it at the
  provider; nothing here can. No
  multi-tenancy. And no group-to-permission mapping, ever: a group reaches a
  right through a RoleBinding the cluster's owner writes, not through a value
  here.
- No NetworkPolicy. The console legitimately talks to the API server, to
  Prometheus by port-forward, and to whatever a forward points at; a policy that
  is honest about that permits nearly everything, and one that is not breaks the
  console.
- No HA. `replicaCount` is 1 and the update strategy is `Recreate`, because the
  state directory is ReadWriteOnce and the forward table is in memory.
- No PodDisruptionBudget, no HorizontalPodAutoscaler. Both would be about a
  service; this is one operator's console.

## Licence

AGPL-3.0-or-later, the same as the console. See `LICENSE`.
