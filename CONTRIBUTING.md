# Contributing

**Contributions are welcome**: issues and pull requests, from anyone, with no
agreement to sign.

This chart is GNU AGPL-3.0-only, the same as the
[console](https://github.com/thonicdev/kubetower). A contribution is offered
under that licence, and its author keeps the copyright in it. The notice reads
*Copyright (C) 2026 The KubeTower Authors*: everyone whose work is in the tree,
as the git history records them.

## What is useful right now

**Issues.** Especially these, because none of them can be answered from here:

- The chart has only ever been installed on **Docker Desktop**. If it fails on
  kind, minikube, EKS, GKE, AKS or OpenShift, that is worth an issue even
  without a fix.
- `rbac.write=true` has never been exercised against a real object.
- The Ingress path has never been tried with a real controller.
- `fsGroup` has never been load-bearing: the hostpath storage class gave the
  PVC mode 0777, so nothing has tested it on a class that respects ownership.

**Please do not report a weakness that
[docs/security-model.md](docs/security-model.md) already names.** The console
in a pod is single-user, shares one identity and one password, and with
`rbac.exec` on offers a shell holding the pod's ServiceAccount. All of that is
documented rather than fixed, and it is the reason the chart tells you not to
share the URL.

## If you do open a pull request

- **Conventional Commits**, on every commit and on the pull-request title. The
  allowed scopes are in `commitlint.config.js`.
- **Run the checks**: `helm lint ./charts/kubetower --set image.tag=0.0.0-ci`
  (the chart refuses to render without a tag while its appVersion is a
  placeholder), `python test/rbac-check.py --self-test`,
  `python test/console-needs-check.py --self-test`, `python test/checksum-check.py`,
  `python test/dev-values-check.py --self-test`,
  `python test/derive-needs.py --self-test --core <a checkout of the console>`
  when you change `charts/kubetower/console-needs.yaml`,
  `bash test/public-check.sh --self-test && bash test/public-check.sh tree`
  and `bash test/attribution-check.sh --self-test`. The last two also read the
  pull request's title and description, because the squash merge makes the
  description the commit message on `main`.
- **Say what you did not test.** The list above exists because nobody has;
  a change that leaves one of them untested is fine, and saying so is required.
