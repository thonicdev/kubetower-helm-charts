# Extra pages

## Extra pages: `extras`

The console's extra pages - pages that are not a resource kind - can be
turned on only once the deployment names them, by key:

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

**`extras` names the extra pages that may be turned on; it turns none on.**
Each one named starts off on every cluster the console shows, until somebody
allowed to use it turns it on for that cluster in the console's Settings >
Extra - and whoever may turn it on may also change its own settings there. An
extra page the build carries and `extras` does not name is listed there too,
with its button disabled and the reason: it is not enabled in this
installation's chart values. A key the console's build does not carry is
logged at start and ignored. The value is a list of lower-case keys; a string,
or a key holding a comma or an `=`, is refused before anything renders.

Who may turn an extra page on, and change its settings, is the cluster's RBAC,
not the chart: the verb `use` on the resource named by the extra's key in the
API group `extras.kubetower.dev`. The console asks the cluster with a
SubjectAccessReview for the person signed in. An administrator grants it with
an ordinary ClusterRole bound to a group, for example for `argocd`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubetower-use-argocd
rules:
  - apiGroups: [extras.kubetower.dev]
    resources: [argocd]
    verbs: [use]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubetower-use-argocd
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubetower-use-argocd
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: Group
    name: platform-team
```

The chart does not render these: like every RoleBinding that says who may do
what, it is the cluster owner's to write. `extras.kubetower.dev` is not an API
the cluster serves - no CustomResourceDefinition is needed, a rule may name a
resource that does not exist.

**Turning an extra page on per cluster needs one replica and its state
volume.** The choice is kept in the console's state directory, which is the
chart's PersistentVolumeClaim (`persistence`). With `persistence.enabled:
false` the chart tells the console its state does not survive a restart
(`KT_STATE_PERSISTENT`), and with `replicaCount` above one each console's state
is its own: in both cases the console withholds the write, Settings > Extra is
a view with each switch disabled and the reason beside it, nobody can turn an
extra page on or off there, and only the chart's values decide. NOTES warns
when `extras` names a page and `replicaCount` is above one.

If `extraEnv` sets `KT_EXTRAS` as well, the `extraEnv` entry wins and the chart
leaves `extras` out, as it does for `GOMEMLIMIT` and `auth.trustedProxies`;
NOTES says so when both are set.

**`extras` also decides what the role grants the extra pages.** The console
carries eleven: `argocd`, `certmanager`, `cnpg`, `events`, `jvm`, `kubeadjust`,
`kubecron`, `netinspect`, `pending`, `redis` and `security`. What each reads
is in `charts/kubetower/console-needs.yaml`; the ClusterRole grants an extra's
own reads only while `extras` names it - the role follows `extras`, not a
`KT_EXTRAS` set through `extraEnv`, so a page turned on that way alone is
refused by the cluster. Reads of kinds the console shows anyway stay behind
`rbac.read`, and anything stronger stays behind its own switch: naming an
extra never grants a Secret, a port-forward or a proxy.

| Extra | Granted when `extras` names it | Also needs |
|---|---|---|
| `argocd` | `list` Argo CD Applications, ApplicationSets, AppProjects; Flux Kustomizations, HelmReleases | `rbac.customResources` for its tab on a custom resource's page; `rbac.readSecrets` and `rbac.exec` for Argo CD's own diff (a token Secret, and a tunnel to argocd-server) |
| `kubeadjust` | `list` VerticalPodAutoscalers, ControllerRevisions | `rbac.metrics` for live usage; `rbac.prometheusProxy` or `rbac.exec` for the six-hour window; `rbac.nodeProxy` for disk usage |
| `certmanager` | `list` cert-manager Certificates, CertificateRequests, Issuers, ClusterIssuers; ACME Orders and Challenges | `rbac.read` for Ingresses and the CRD catalogue |
| `cnpg` | `list` CloudNativePG Clusters, Backups, ScheduledBackups, Poolers, Databases, Publications, Subscriptions | `rbac.read` for Pods, Events and the CRD catalogue; `rbac.exec` for the SQL tunnel and replication-lag commands; `rbac.readSecrets` for the connection Secret; `rbac.nodeProxy` for disk figures |
| `events` | nothing of its own: Events, ReplicaSets, ConfigMaps, Pods and Nodes are `rbac.read`'s | - |
| `jvm` | nothing of its own: Pods and Services are `rbac.read`'s | `rbac.exec` for `jcmd` and the tunnel to a JVM; `rbac.metrics` for usage; `rbac.prometheusProxy` for history |
| `kubecron` | nothing of its own: CronJobs, Jobs, Pods and Services are `rbac.read`'s | `rbac.exec` for the tunnel to its collector; `rbac.readSecrets` when the collector asks for a token |
| `netinspect` | `list` Cilium network policies and CIDR groups; Calico, AdminNetworkPolicy and BaselineAdminNetworkPolicy kinds, counted only | `rbac.read` for NetworkPolicies, DaemonSets, Pods, Services, Namespaces and the CRD catalogue; `rbac.exec` for the tunnel to Hubble |
| `pending` | nothing of its own: Pods, Nodes, Events, PersistentVolumes(Claims), StorageClasses and ResourceQuotas are `rbac.read`'s | - |
| `redis` | nothing of its own: Services, EndpointSlices and Pods are `rbac.read`'s | `rbac.exec` for the tunnel to Redis; `rbac.readSecrets` for a password kept in a Secret |
| `security` | `list` trivy-operator reports (vulnerability, config audit, exposed secret, infra assessment, RBAC assessment, SBOM, compliance, and their cluster-scoped kinds); PolicyReports (`wgpolicyk8s.io`, `openreports.io`); Kyverno policies, including `ImageValidatingPolicy` and the two `Namespaced*` policy kinds; Gateway API HTTPRoutes and GRPCRoutes | `rbac.read` for Pods, Services, workloads and Ingresses; `rbac.customResources` for Gatekeeper constraints, whose kinds its templates create |

## Settings of the extra pages: `extraSettings`

An extra page may offer settings of its own, each on or off - the Argo CD page's
column in the console's resource lists, for one, which is off by default. The
deployment can decide one for every cluster, by page and setting key:

```yaml
extraSettings:
  argocd:
    list-column: true
```

or `--set extraSettings.argocd.list-column=true`. Which settings exist depends
on the console's build: its Settings page lists each page's settings, and its
start-up log names a setting it does not know and ignores it. A console built
before it read `KT_EXTRA_SETTINGS` ignores the whole variable without a word:
if the Settings page does not draw the setting as set by the deployment, the
image is older than the value. The chart writes the settings into the same
ConfigMap as `extras`, as `KT_EXTRA_SETTINGS` (`argocd.list-column=on`), and
the Deployment reads that key by reference; changing a setting restarts the
pod, as changing the list does. Empty, the default, the variable is not
rendered and every setting keeps its default.

A setting named here is **pinned**: it has that value on every cluster the
console shows, the Settings page draws it as set by the deployment, with no
control, and the console refuses a per-cluster change to it. A setting left out
keeps its default, and a person allowed to turn its page on in a cluster may
change it there, where the console offers that choice. A setting of a page
`extras` does not name has no effect, since that page cannot be turned on;
NOTES says so.

The value is a map of page to setting to `true` or `false`; a string, a number
or a key the console could not hold (upper case, a dot, longer than 40
characters) is refused before anything renders. If `extraEnv` sets
`KT_EXTRA_SETTINGS` as well, the `extraEnv` entry wins and NOTES says so.
