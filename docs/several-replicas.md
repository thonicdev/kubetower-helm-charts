# Several replicas

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
replica draws the defaults), the settings are read-only, the extra pages cannot
be turned on or off per cluster (see [Extra pages](extras.md)),
port-forwards are off, and there is no archive. The console does not register
those routes at all.

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

**Valkey's password is readable by everybody who signs in** once
`rbac.readSecrets` is on (it is off by default), and nothing restricts who can connect to Valkey. The
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
