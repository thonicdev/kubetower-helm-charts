# Development

## What is here

| Path | What |
|---|---|
| `charts/kubetower/` | The console in a pod |
| `charts/kubetower/console-needs.yaml` | What the console asks the cluster for, request by request. The `ClusterRole` is rendered from it |
| `dev/values.yaml` | The values a development install in a local cluster uses, with the fixture below |
| `test/oidc/` | A Dex fixture for developing single sign-on against. Not part of the chart |
| `.github/workflows/release.yml` | Publishes the chart on a tag `kubetower-<version>` |

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
