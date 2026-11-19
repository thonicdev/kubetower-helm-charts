# Publishing the chart

A tag `kubetower-<version>` runs `.github/workflows/release.yml`, which runs
`.github/scripts/publish-chart.sh`. Before anything is pushed it refuses:

- a tag that is not the version in `Chart.yaml`;
- a tagged commit that is not on `main`;
- the placeholder `appVersion`;
- an `appVersion` that is not a semantic version (`MAJOR.MINOR.PATCH`,
  optionally `-prerelease`): it is the published chart's default image tag;
- a role read from another console than the one the chart installs: the
  console's tag `<appVersion>` (bare semver, no leading `v`) must be the
  commit `console-needs.yaml` was derived from, read from the console's
  repository, and a readable repository with no such tag is refused too;
- a console repository that cannot be read for any reason other than a
  missing credential - a name that does not resolve, a server error, a rate
  limit, a timeout. Each is a release that could have been checked, and a
  published version cannot be taken back;
- a version the registry already holds. A published version is never
  replaced; bump the chart's version instead.

**One answer is a warning, not a refusal: the missing-credential one.** While
`thonicdev/kubetower` is private, this public repository's release holds no
credential for it, deliberately, so GitHub answers "could not read Username"
(a 401 with nothing to offer), and the tie between the role and the console
cannot be checked. A 403, or a credential offered and rejected, is refused. The release then says so - a warning annotation, and a line in the
run's summary naming `derivedFrom.commit` as committed - and publishes. It
never reports that case as checked. Until the console is public, whoever pushes
the chart tag makes that check by hand: `derivedFrom.commit` must be the commit
the console's `<appVersion>` tag names. Once it is public, the tag is readable
and the equality is enforced again with no change here. One limit: without a
credential, GitHub gives the same answer for a repository that does not
exist, so a typo in the console's URL would also only warn. That URL is a
constant in `publish-chart.sh`, reviewed like any other line.

Then it packages the chart, pushes it to `oci://ghcr.io/thonicdev/charts`, pulls
it back, checks it is byte for byte what was pushed, and renders it. After the
registry login is gone, `.github/scripts/read-chart-anonymously.sh` reads it
once more with no login, so a package nobody else can read fails the release
instead of the first install. `chart.yml` runs both scripts on every pull
request against a registry that lives only for the job, and proves each
refusal, the missing-credential warning (from a local server answering 401)
and a matching tag's tie, so a release
takes no step CI has not taken. The job holding the
package token installs nothing and runs only actions pinned to a commit.
Nobody has pushed a tag yet.
