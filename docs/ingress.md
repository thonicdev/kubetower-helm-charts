# Behind an ingress

## The root path only

The console is served at the root path of its host only: keep `ingress.hosts[].paths[].path` at `/`, because a path prefix is not supported and gives a blank page.

## Long-lived connections

The terminal, the log stream and port-forwards are WebSocket connections that
can sit idle for minutes. An ingress controller that closes a connection idle
for its read timeout cuts them: a shell that dies every minute.

With `ingress.enabled`, the chart raises that timeout to an hour with two
annotations, `nginx.ingress.kubernetes.io/proxy-read-timeout` and
`proxy-send-timeout`. **They are ingress-nginx's alone.** Any other controller
ignores them without a word, so on another controller the timeout is yours to
raise, the way that controller sets it; `ingress.annotations` adds whatever
annotations it reads.

**On Traefik no Ingress annotation sets it.** What matters is the entry
point's `transport.respondingTimeouts.readTimeout`, 60 s by default in Traefik
v3, which is part of Traefik's own configuration, not of an Ingress, so this
chart cannot set it. Raise it on Traefik, for the entry point the console is
served on - for example
`--entryPoints.websecure.transport.respondingTimeouts.readTimeout=3600s`. It
applies to everything served on that entry point, not only the console.

## Password sign-in behind an ingress: `auth.trustedProxies`

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
