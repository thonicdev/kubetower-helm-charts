# KubeTower Helm chart

Installs the KubeTower web console in a pod, with read-only access to the
cluster it runs in. The console's source: [thonicdev/kubetower](https://github.com/thonicdev/kubetower).

## Prerequisites

- Kubernetes 1.25 or later.
- Helm 3.8 or later (it reads the chart from an OCI registry). Helm 3.14 or
  later for `--reset-then-reuse-values` on upgrade.
- A default StorageClass, or set `persistence.storageClass`. The console keeps
  its state on a 1 Gi volume.
- Optional: metrics-server, for the CPU and memory columns.
- Optional: an ingress controller and a TLS certificate, to reach the console
  without `kubectl port-forward`.

## Quick start

Install:

```bash
helm install kubetower oci://ghcr.io/thonicdev/charts/kubetower \
  --namespace kubetower --create-namespace \
  --set image.tag=0.1.0-rc.4
```

Add `--version <x.y.z>` to pin a chart version.

The OCI chart exists once a release is published; until then this answers
`not found`, and [docs/install.md](docs/install.md) installs from a checkout.

Open the console:

```bash
kubectl -n kubetower port-forward svc/kubetower 5823:5823
```

Then go to <http://127.0.0.1:5823>.

Sign in as `admin`. The chart drew a password for you:

```bash
kubectl -n kubetower get secret kubetower-initial-password \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Delete that Secret once you have the password. Upgrades keep the password.

```bash
kubectl -n kubetower delete secret kubetower-initial-password
```

`helm install` prints these steps too, with your values filled in.

## Know this first

The console makes every request to the cluster as its pod's ServiceAccount.
Anyone who signs in with the shared password gets everything that role allows.
For more than one person, use single sign-on: the console then checks each
person's own rights with the cluster before acting.
Details: [docs/security-model.md](docs/security-model.md).

## Common setups

Put your settings in a file, for example `values.yaml`, and pass it with
`-f values.yaml` to `helm install` or `helm upgrade`.

### Ingress and TLS

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt   # only if cert-manager issues the certificate
  hosts:
    - host: kubetower.example.com
      paths:
        - path: /          # the console works at the root path only
          pathType: Prefix
  tls:
    - secretName: kubetower-tls
      hosts: [kubetower.example.com]
```

- The chart raises the WebSocket timeouts for ingress-nginx. On another
  controller, raise them yourself, or shells and log streams drop after a
  minute.
- With password sign-in behind an ingress, set `auth.trustedProxies` to your
  ingress controller's addresses.
- Prefer single sign-on once the console is on the network.

Details: [docs/ingress.md](docs/ingress.md).

### Single sign-on (OIDC)

Store the client secret in a Secret (skip this for a public client):

```bash
kubectl -n kubetower create secret generic kubetower-oidc \
  --from-literal=client-secret=<secret>
```

```yaml
auth:
  oidc:
    issuer: https://idp.example.com/realms/main
    clientID: kubetower
    clientSecret:
      existingSecret: kubetower-oidc
      key: client-secret
    # Register this exact URL at the provider.
    # Through a port-forward: http://127.0.0.1:5823/api/auth/oidc/callback
    redirectURL: https://kubetower.example.com/api/auth/oidc/callback
    scopes: "openid,email,profile,groups"
```

- With an issuer set, password sign-in is off. Set `auth.localAccount: true`
  to keep it.
- Everyone the provider issues a token to can sign in. Restrict access at the
  provider.
- What each person may do comes from your RoleBindings. By default a
  RoleBinding names the person by email.

Details, including user-name prefixes: [docs/single-sign-on.md](docs/single-sign-on.md).

### Permissions (RBAC)

The default role is read-only, cluster-wide, and does not read Secrets. Turn
on more only if everyone who can sign in should have it.

```yaml
rbac:
  readSecrets: false       # Secrets page and Helm releases; exposes every Secret
  customResources: false   # browse custom resources (wildcard read)
  write: false             # edit, delete, scale, drain; treat it as cluster-admin
  exec: false              # shells and port-forwards in any pod
```

Other switches: `rbac.deleteAll`, `rbac.nodeProxy`, `rbac.prometheusProxy`.
What each one grants: [docs/rbac.md](docs/rbac.md).

### Your own password

Needed with Argo CD or `helm template`, which cannot read back a drawn password:

```bash
htpasswd -nbBC 12 "" "yourpassword" | cut -c2-
```

```yaml
auth:
  passwordHash: "$2y$12$..."                  # the output above
  sessionSecret: "<at least 32 characters>"   # e.g. openssl rand -hex 32
```

Or name a Secret you manage with `auth.existingSecret` (keys
`KT_PASSWORD_HASH` and `KT_SESSION_SECRET`).

### Resources and storage

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 1Gi

persistence:
  storageClass: ""   # empty: the cluster's default
  size: 1Gi
```

### Several replicas

```yaml
replicaCount: 2
persistence:
  enabled: false
networkPolicy:
  enabled: true
```

Needs single sign-on, `auth.passwordHash` or `auth.existingSecret`. The chart
adds a small Valkey to share sessions.
Details: [docs/several-replicas.md](docs/several-replicas.md).

## Upgrade

```bash
helm upgrade kubetower oci://ghcr.io/thonicdev/charts/kubetower \
  --namespace kubetower --reset-then-reuse-values
```

Or pass your values file again with `-f`. Read [docs/upgrading.md](docs/upgrading.md)
before you cross a minor version.

## Uninstall

```bash
helm uninstall kubetower --namespace kubetower
```

The state volume is kept on purpose. Delete it when you are sure:

```bash
kubectl -n kubetower delete pvc kubetower-state
```

## Values

The settings people change most. Every other one is described in
[values.yaml](charts/kubetower/values.yaml).

| Value | Default | What it does |
|---|---|---|
| `image.repository` | `ghcr.io/thonicdev/kubetower` | Console image |
| `image.tag` | chart `appVersion` | Console version |
| `image.pullSecrets` | `[]` | Pull secrets, as `[{name: ...}]` |
| `replicaCount` | `1` | Above 1 adds Valkey and needs `persistence.enabled: false` |
| `auth.user` | `admin` | User name for password sign-in |
| `auth.passwordHash` | `""` | bcrypt hash; empty draws a password |
| `auth.existingSecret` | `""` | Your Secret with `KT_PASSWORD_HASH` and `KT_SESSION_SECRET` |
| `auth.sessionSecret` | `""` | Session signing key, 32+ characters; empty draws one |
| `auth.sessionTTL` / `auth.sessionMax` | `8h` / `24h` | Idle timeout / maximum session |
| `auth.oidc.issuer` | `""` | OIDC issuer; set it to turn on single sign-on |
| `auth.oidc.clientID` | `""` | OIDC client |
| `auth.oidc.clientSecret.existingSecret` | `""` | Secret holding the client secret |
| `auth.oidc.redirectURL` | `""` | Callback URL, `.../api/auth/oidc/callback` |
| `auth.oidc.scopes` | `openid,email,profile` | Requested scopes |
| `auth.localAccount` | `false` | Keep password sign-in next to OIDC |
| `auth.trustedProxies` | `[]` | Ingress controller addresses, for password sign-in |
| `rbac.readSecrets` | `false` | Read every Secret |
| `rbac.customResources` | `false` | Read every custom resource |
| `rbac.write` | `false` | Edit and delete (cluster-admin in effect) |
| `rbac.exec` | `false` | Pod shells and port-forwards |
| `rbac.metrics` | `true` | CPU and memory columns |
| `extras` | `[]` | Extra pages that may be turned on, e.g. `[argocd]`: [docs/extras.md](docs/extras.md) |
| `ingress.enabled` | `false` | Create an Ingress |
| `ingress.className` | `""` | Ingress class |
| `ingress.hosts` / `ingress.tls` | `kubetower.local` / `[]` | Hosts and TLS |
| `persistence.enabled` | `true` | Keep state on a volume |
| `persistence.storageClass` | `""` | StorageClass; empty is the default |
| `persistence.size` | `1Gi` | Volume size |
| `resources` | 100m / 256Mi, limit 1Gi | Pod resources |
| `networkPolicy.enabled` | `false` | Ingress NetworkPolicies |
| `serverProfile` | `true` | No local shell, node shell or assistant |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ImagePullBackOff` | `image.tag` names a version ghcr.io does not hold, or the nodes cannot reach ghcr.io: [docs/install.md](docs/install.md#the-image) |
| Blank page behind an ingress | Serve the console at `/`, not under a path prefix |
| Shell or log stream drops after a minute | Raise the ingress controller's read timeout: [docs/ingress.md](docs/ingress.md) |
| A page says access is denied | Turn on the matching `rbac` switch: [docs/rbac.md](docs/rbac.md) |
| Pod not ready, audit log error | Fix the disk or log pipe, then restart: [docs/operations.md](docs/operations.md#when-the-audit-log-stops-writing) |

## More

[docs/](docs/): security model, RBAC, ingress, single sign-on, several
replicas, extra pages, operations, upgrading, publishing, development.

## Licence

AGPL-3.0-only, the same as the console: version 3 of the GNU Affero General
Public License, and no later version. See `LICENSE`.

Copyright (C) 2026 The KubeTower Authors

Security issues: [SECURITY.md](SECURITY.md).
