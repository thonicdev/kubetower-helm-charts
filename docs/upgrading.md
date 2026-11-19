# Upgrading

Upgrade with `helm upgrade --reset-then-reuse-values` (Helm 3.14 or later), or
pass your values file again with `-f`. Plain `--reuse-values` keeps only the
values the previous release stored, so any key a newer chart adds is missing,
and the render comes out wrong or fails.

## To 0.4.0: no Secret read by default, and no password in the log

**`rbac.readSecrets` is now `false` by default.** The role no longer grants
`get` and `list` on `secrets` cluster-wide unless you ask for it, so the
Config > Secrets page and the Helm release list report a denial, and so do the
extra pages that read a Secret (the table under [Extra pages](extras.md#extra-pages-extras)).
To keep the previous behaviour:

```bash
helm upgrade kubetower <chart> --reset-then-reuse-values --set rbac.readSecrets=true
```

It gives every Secret in the cluster, this release's session key included, to
whoever signs in.

**The chart draws the shared password itself** when the password route is
offered and neither `auth.passwordHash` nor `auth.existingSecret` is set. The
pod gets only its hash (`KT_PASSWORD_HASH`), the password is in the Secret
`<release>-kubetower-initial-password`, and the console no longer prints a
password in its log. Two consequences on upgrade:

- **A password the console drew under 0.3.0 stops working.** It lived in the
  state directory, and the hash in the environment now wins over it. Read the
  new one from that Secret, or keep the old one by setting `auth.passwordHash`
  to the `password_hash` in `credentials.json` in the state volume before
  upgrading.
- **`persistence.enabled=false` with no password configured is no longer
  refused**: the password no longer lives on that disk.

Under a tool that renders without a cluster (`helm template`, Argo CD), the
chart cannot read the drawn password back, so every render draws a new one:
set `auth.passwordHash` there, as for `auth.sessionSecret`.

## To 0.3.0: the role grants only what the console asks for

The `ClusterRole` is rendered from `charts/kubetower/console-needs.yaml` (see
[RBAC](rbac.md)), and an upgrade removes every grant the console never uses:
`watch`; `update`; the `*/scale` and `*/status` subresources; `pods/attach`;
`nodes/stats` and `nodes/metrics`; `get` on `metrics.k8s.io`; `list` on
`pods/log`; and every read of
`controllerrevisions`, `podtemplates`, `volumeattachments`, `csidrivers`,
`csinodes`, `csistoragecapacities` and `events.k8s.io` events.

The console itself loses nothing. **Under `serverProfile=false`, the shell in
the pod runs with this role too**, and these stop working there:

- `kubectl get -w`, `kubectl rollout status`, `kubectl wait`, and anything
  built on a watch or an informer;
- `kubectl replace`, `kubectl apply --force`, and anything else that sends an
  update;
- `kubectl scale` (the `scale` subresource);
- `kubectl attach`;
- `kubectl rollout history` and `kubectl rollout undo` on StatefulSets and
  DaemonSets (`controllerrevisions`);
- `kubectl top pod NAME` and `kubectl top node NAME` (`get` on
  `metrics.k8s.io`); the unnamed forms list, and still work.

If that shell needs them, bind a role of your own to the release's
ServiceAccount. The chart will not grant what the console does not ask for.
