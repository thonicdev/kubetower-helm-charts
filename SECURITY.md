# Security policy

## Reporting a vulnerability

Report it privately through GitHub: **Security → Report a vulnerability** on
this repository
([private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).
Do not open a public issue for it.

Say which chart version and values you used, what an attacker gains, and how
to reproduce it.

## Supported versions

The latest published chart version only.

## Scope

This repository holds the Helm chart: what it renders, the role it grants, and
how it exposes the console. A vulnerability in the console itself is reported
the same way, here, until the console's own repository opens with its 0.1.0
release.

What the chart deliberately allows - every request made as the pod's
ServiceAccount, and the grants behind each `rbac` switch - is described in
[docs/security-model.md](docs/security-model.md) and is not a vulnerability by
itself.
