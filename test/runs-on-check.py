"""Every job runs where the RUNS_ON variable says, and a fork's pull request
on a GitHub-hosted runner.

    python3 test/runs-on-check.py               # every workflow here
    python3 test/runs-on-check.py --self-test   # the check refuses what it must

RUNS_ON is a repository variable holding a JSON list of runner labels. Unset,
a job runs on ubuntu-latest. Whatever it says, a pull request from a fork runs
on ubuntu-latest: a self-hosted runner is not discarded after a job, so code
nobody with write access has read must never reach one. The fork test comes
first in the expression, so no variable can undo it.

The rule is one exact expression rather than a set of acceptable labels: a
self-hosted runner does not have to be asked for as `self-hosted`, and
`[linux, other]` or `{ group: other }` both reach one.
"""

import glob
import os
import sys

import yaml

RUNS_ON = (
    "${{ github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name != github.repository && 'ubuntu-latest' || "
    "vars.RUNS_ON && fromJSON(vars.RUNS_ON) || 'ubuntu-latest' }}"
)


class UniqueKeys(yaml.SafeLoader):
    """A key written twice is refused: the last one would hide the first."""


def _mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(None, None, f"key {key!r} written twice", key_node.start_mark)
        keys.add(key)
    return loader.construct_mapping(node, deep)


UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def problems(name, text, local):
    try:
        doc = yaml.load(text, Loader=UniqueKeys) or {}
    except yaml.YAMLError as err:
        return [f"{name} cannot be read as a workflow ({err}), so where its jobs run is unchecked"]
    jobs = doc.get("jobs") or {}
    out = []
    for job_id, job in sorted(jobs.items(), key=lambda kv: str(kv[0])):
        where = f"{name} job {job_id!r}"
        if not isinstance(job, dict):
            out.append(f"{where} is not a mapping")
            continue
        if "uses" in job:
            path = str(job["uses"])
            prefix = "./.github/workflows/"
            if not path.startswith(prefix) or path[len(prefix):] not in local:
                out.append(f"{where} calls {path}: only a workflow in .github/workflows, checked here, may be called")
            if "runs-on" in job:
                out.append(f"{where} has both uses: and runs-on:")
            continue
        if "runs-on" not in job:
            out.append(f"{where} has no runs-on")
        elif job["runs-on"] != RUNS_ON:
            out.append(f"{where} runs on {job['runs-on']!r}: write `runs-on: {RUNS_ON}`")
    return out


def self_test():
    job = lambda runs: "jobs:\n  build:\n    runs-on: " + runs + "\n"
    cases = [
        ("a hosted label", job("ubuntu-latest"), True),
        ("a self-hosted label list", job("[self-hosted, linux]"), True),
        ("a runner group", job("{ group: other }"), True),
        ("another expression", job("${{ matrix.os }}"), True),
        ("the expression without its fork test",
         job("${{ vars.RUNS_ON && fromJSON(vars.RUNS_ON) || 'ubuntu-latest' }}"), True),
        ("no runs-on", "jobs:\n  build:\n    steps: []\n", True),
        ("runs-on written twice", job("[self-hosted, linux]") + "    runs-on: " + RUNS_ON + "\n", True),
        ("an external reusable workflow", "jobs:\n  build:\n    uses: someone/else/.github/workflows/x.yml@main\n", True),
        ("the expression", job(RUNS_ON), False),
        ("the expression with a quoted key", "jobs: { build: { \"runs-on\": \"" + RUNS_ON + "\" } }\n", False),
        ("a local reusable workflow", "jobs:\n  build:\n    uses: ./.github/workflows/chart.yml\n", False),
    ]
    failed = 0
    for name, text, refused in cases:
        got = bool(problems("test.yml", text, {"chart.yml"}))
        if got != refused:
            failed += 1
            print(f"self-test: {name}: refused={got}, want {refused}", file=sys.stderr)
    print(f"self-test: {len(cases) - failed} of {len(cases)} cases as expected")
    return 1 if failed else 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    files = sorted(glob.glob(".github/workflows/*.yml") + glob.glob(".github/workflows/*.yaml"))
    if not files:
        print("no workflow found, so nothing was checked", file=sys.stderr)
        return 1
    local = {os.path.basename(f) for f in files}
    found, jobs = [], 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        jobs += len((yaml.safe_load(text) or {}).get("jobs") or {})
        found += problems(os.path.basename(f), text, local)
    for p in found:
        print(p, file=sys.stderr)
    print(f"{jobs} jobs read from {len(files)} workflow files, {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
