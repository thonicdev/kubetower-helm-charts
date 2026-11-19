# Security model

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

## The server profile

`serverProfile` passes `--incluster` to the console, and it is **on by
default**. It **removes** routes; it adds nothing. `--set serverProfile=false`
deploys the desktop profile instead, with its local shell, and that shell runs
kubectl as the release's ServiceAccount with only [its role](rbac.md) - see
[Upgrading to 0.3.0](upgrading.md#to-030-the-role-grants-only-what-the-console-asks-for)
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
