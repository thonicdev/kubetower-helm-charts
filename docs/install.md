# Install in detail

## Release status

**Not a release yet.** The console's release workflow publishes an image for
each version tag, at `ghcr.io/thonicdev/kubetower:<version>`, and this
repository's publishes the chart for each chart tag, at
`oci://ghcr.io/thonicdev/charts/kubetower`. The console has release
candidates and no release yet, and no chart has been published: the first
chart waits for the first console release, since a chart naming no released
image installs nothing. Until then you install from this checkout, naming the
image version; [Install](#install) has both ways, and the
registry one works unchanged once a chart is published.

## The image

The default is `ghcr.io/thonicdev/kubetower`, which is where the console's
release workflow pushes one public image per tag `<version>` (bare semver,
such as `0.1.0`), tagged the same. No pull secret is needed.
There is no `latest`. The tag defaults to the chart's `appVersion`.

**Building the image yourself**, from the console's source:

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

**1. A values file.** With single sign-on, which the chart is meant to run
with once more than one person reaches it:

```bash
kubectl create namespace kubetower
# The client secret, only for a confidential client. Never a value in the file.
kubectl -n kubetower create secret generic kubetower-oidc --from-literal=client-secret=<secret>
```

```yaml
# kubetower-values.yaml
image:
  # Empty is the chart's appVersion.
  tag: ""

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
to use is in [Single sign-on](single-sign-on.md).

**2. Install:**

```bash
helm install kubetower oci://ghcr.io/thonicdev/charts/kubetower --version <chart version> \
  --namespace kubetower -f kubetower-values.yaml
```

`NOTES.txt` then prints how to reach it and sign in, with your values filled
in. Without single sign-on and without `auth.passwordHash`, that means reading
the password the chart drew out of the Secret
`<release>-kubetower-initial-password`, port-forwarding and signing in. The pod
holds only its hash; the console draws no password and prints none in its log.

### From a checkout

Until a chart version is published, or to install a change of your own, the
same values file works from this repository:

```bash
helm install kubetower ./charts/kubetower \
  --namespace kubetower --create-namespace \
  -f kubetower-values.yaml --set image.tag=<console version>
```
