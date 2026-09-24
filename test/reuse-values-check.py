"""An upgrade with --reuse-values renders what a fresh install would.

    python test/reuse-values-check.py

`helm upgrade --reuse-values` does not merge the new chart's defaults into the
values it reuses: it renders the new templates with the old release's values,
the old chart's defaults included, and nothing else. A value this chart added
after a release was installed is therefore absent - not empty, absent - and a
template that reads `.Values.block.key` under an absent block stops with a nil
pointer, which is an upgrade that cannot be done.

This renders the chart twice for each case below:

- **as --reuse-values would**: a copy of the chart whose values.yaml is
  test/fixtures/first-release-values.yaml, the values of this chart's first
  version, so every value added since is missing;
- **as a fresh install would**: the chart as it is, with the same fixture
  given through -f, so every value added since takes its default - with the
  fill switched off, since nothing is missing there.

It asserts that both render, and that they render the same objects and the
same NOTES. Absent must mean the default, not a different behaviour. The
randomly drawn parts - a Secret's contents, the checksums over them - are
masked, since two renders never draw the same ones.

It also asserts that every template starts by filling the values added since
the first release (kubetower.laterDefaults): which template Helm renders first
is its business, so none may rely on another having filled them.

Nothing here reaches a cluster: it is `helm template`, with KUBECONFIG pointed
at nothing.
"""

import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

CHART = pathlib.Path("charts/kubetower")
FIXTURE = pathlib.Path("test/fixtures/first-release-values.yaml")
FILL = '{{- $_ := include "kubetower.fillLaterDefaults" . -}}'

# NOTES.txt is rendered with every template but `helm template` does not
# print it, and an upgrade fails on it like on any other. This wraps it in an
# object so that it is printed, and compared, too.
NOTES_AS_OBJECT = """apiVersion: v1
kind: ConfigMap
metadata:
  name: rendered-notes
data:
  notes: {{ include (print $.Template.BasePath "/NOTES.txt") . | toJson }}
"""

# And the list of later defaults itself, so that it can be held against
# values.yaml directly rather than only through what it renders.
LATER_AS_OBJECT = """apiVersion: v1
kind: ConfigMap
metadata:
  name: rendered-later-defaults
data:
  later: {{ include "kubetower.laterDefaults" . | fromYaml | toJson | toJson }}
"""

OIDC = ["auth.oidc.issuer=https://idp.example/dex", "auth.oidc.clientID=kubetower",
        "auth.oidc.redirectURL=https://kubetower.example/api/auth/oidc/callback"]
# Every case sets an image tag, which is what an upgrade usually changes, and
# a session key, so that the one value drawn at random that is not masked
# below is not drawn at all.
COMMON = ["image.tag=x", "auth.sessionSecret=" + "k" * 40]
CASES = {
    "the defaults": [],
    "several replicas": ["replicaCount=2", "persistence.enabled=false", "serverProfile=true",
                         "auth.passwordHash=$2a$12$abcdefghijklmnopqrstuv"],
    "single sign-on turned on at the upgrade": OIDC,
    "several replicas behind single sign-on": ["replicaCount=2", "persistence.enabled=false",
                                                "serverProfile=true"] + OIDC,
}


def copy_chart(dest, values):
    shutil.copytree(CHART, dest)
    if values is not None:
        shutil.copyfile(values, dest / "values.yaml")
    else:
        # The fresh render is the reference, so its fill is switched off:
        # nothing is missing there, and a fill that replaced a value present
        # would otherwise change both renders alike and pass unseen.
        later = dest / "templates" / "_later-defaults.tpl"
        text, n = re.subn(
            r'(\{\{- define "kubetower\.fillLaterDefaults" -\}\}).*?(\{\{- end \}\})',
            r"\1\2", later.read_text(encoding="utf-8"), flags=re.S)
        if n != 1:
            raise SystemExit("kubetower.fillLaterDefaults not found, so it cannot be switched off")
        later.write_text(text, encoding="utf-8")
    (dest / "templates" / "zz-rendered-notes.yaml").write_text(NOTES_AS_OBJECT)
    (dest / "templates" / "zz-rendered-later-defaults.yaml").write_text(LATER_AS_OBJECT)


def render(chart, sets, extra=()):
    env = dict(os.environ, KUBECONFIG=os.devnull)
    cmd = ["helm", "template", "kubetower", str(chart), *extra]
    cmd += [a for s in sets for a in ("--set", s)]
    return subprocess.run(cmd, capture_output=True, env=env)


def normalised(out):
    docs = [d for d in yaml.safe_load_all(out) if d]
    docs = [d for d in docs if d["metadata"]["name"] != "rendered-later-defaults"]
    for d in docs:
        if d.get("kind") == "Secret":
            for field in ("data", "stringData"):
                if field in d:
                    d[field] = {k: "<drawn>" for k in d[field]}
        for meta in (d.get("metadata", {}),
                     d.get("spec", {}).get("template", {}).get("metadata", {})):
            notes = meta.get("annotations") or {}
            for k in list(notes):
                if k.startswith("checksum/"):
                    notes[k] = "<checksum>"
    docs.sort(key=lambda d: (d["kind"], d["metadata"]["name"]))
    return yaml.safe_dump_all(docs, sort_keys=True).splitlines()


def leaves(d, path=()):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and v:
            out.update(leaves(v, path + (k,)))
        else:
            out[".".join(path + (k,))] = v
    return out


def later_defaults_match(rendered):
    """Every value values.yaml has and the first version did not is in the
    list, with the default values.yaml gives it - and nothing else is."""
    doc = next(d for d in yaml.safe_load_all(rendered) if d
               and d["metadata"]["name"] == "rendered-later-defaults")
    later = leaves(json.loads(doc["data"]["later"]))
    values = leaves(yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8")))
    first = leaves(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")))
    problems = [f"{k} is new since the first version and has no later default"
                for k in values if k not in first and k not in later]
    problems += [f"{k} is a later default values.yaml does not have" for k in later if k not in values]
    problems += [f"{k}: the later default is {later[k]!r}, values.yaml says {values[k]!r}"
                 for k in later if k in values and later[k] != values[k]]
    return problems


def every_template_fills():
    missing = []
    for f in sorted((CHART / "templates").iterdir()):
        if f.name.startswith("_") or not f.is_file():
            continue
        first = f.read_text(encoding="utf-8").lstrip().splitlines()[0]
        if first != FILL:
            missing.append(f.name)
    return missing


if __name__ == "__main__":
    failures = []
    missing = every_template_fills()
    print(f"{'ok  ' if not missing else 'FAIL'}  every template starts by filling the later defaults")
    if missing:
        failures.append("fill")
        print("        these do not: " + ", ".join(missing))

    with tempfile.TemporaryDirectory() as tmp:
        reused, fresh = pathlib.Path(tmp, "reused"), pathlib.Path(tmp, "fresh")
        copy_chart(reused, FIXTURE)
        copy_chart(fresh, None)
        for name, sets in CASES.items():
            a = render(reused, COMMON + sets)
            b = render(fresh, COMMON + sets, ["-f", str(FIXTURE)])
            if name == "the defaults" and b.returncode == 0:
                problems = later_defaults_match(b.stdout)
                print(f"{'ok  ' if not problems else 'FAIL'}  the later defaults are values.yaml's")
                if problems:
                    failures.append("later defaults")
                    print("        " + "\n        ".join(problems))
            if a.returncode != 0 or b.returncode != 0:
                failures.append(name)
                print(f"FAIL  {name}: does not render")
                for which, r in (("as --reuse-values", a), ("fresh", b)):
                    if r.returncode != 0:
                        print(f"        {which}: {r.stderr.decode(errors='replace').strip()[:400]}")
                continue
            diff = list(difflib.unified_diff(normalised(b.stdout), normalised(a.stdout),
                                             "fresh install", "--reuse-values", lineterm="", n=2))
            print(f"{'ok  ' if not diff else 'FAIL'}  {name}: --reuse-values renders what a fresh install does")
            if diff:
                failures.append(name)
                print("        " + "\n        ".join(diff[:40]))
    sys.exit(1 if failures else 0)
