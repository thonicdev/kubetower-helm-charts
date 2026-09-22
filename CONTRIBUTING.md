# Contributing

## Read this first: pull requests are not being merged

**Contributions require a Contributor Licence Agreement, that document does not
exist yet, and external contributions are not being accepted at this stage.**
The same statement governs [the console
itself](https://github.com/thonicdev/kubetower/blob/main/LICENSING.md); this
repository is under the same licence and the same position.

That is not a judgement about any contribution. It is that this project is
dual-licensed, which is only lawful while the copyright holder is the sole
author, and the instrument that lets a contribution join without breaking that
has not been written.

## What is useful right now

**Issues.** Especially these, because none of them can be answered from here:

- The chart has only ever been installed on **Docker Desktop**. If it fails on
  kind, minikube, EKS, GKE, AKS or OpenShift, that is worth an issue even
  without a fix.
- `rbac.write=true` has never been exercised against a real object.
- The Ingress path has never been tried with a real controller.
- `fsGroup` has never been load-bearing: the hostpath storage class gave the
  PVC mode 0777, so nothing has tested it on a class that respects ownership.

**Please do not report a weakness that the README already names.** The console
in a pod is single-user, shares one identity and one password, and with
`rbac.exec` on offers a shell holding the pod's ServiceAccount. All of that is
documented rather than fixed, and it is the reason the chart tells you not to
share the URL.

## If you do open a pull request

It will be read, and it will sit unmerged until the CLA exists. Knowing that in
advance is the only reason this file leads with it.

- **Conventional Commits**, on every commit and on the pull-request title. The
  allowed scopes are in `commitlint.config.js`.
- **Run the checks**: `helm lint ./charts/kubetower` and
  `python test/rbac-check.py --self-test`.
- **Say what you did not test.** The list above exists because nobody has;
  a change that leaves one of them untested is fine, and saying so is required.
