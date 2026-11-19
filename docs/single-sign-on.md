# Single sign-on

## The name a RoleBinding names

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
