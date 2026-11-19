# What the chart deploys, and running it

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

## When the audit log stops writing

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
