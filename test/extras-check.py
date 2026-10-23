"""The extra pages reach the console through their ConfigMap, and only so.

    python test/extras-check.py

`extras` names the extra pages the console turns on. The chart renders it into
a ConfigMap, and the Deployment reads KT_EXTRAS from that ConfigMap by
reference (`valueFrom.configMapKeyRef`), so the console needs no reader of its
own. Checked on the real render, each case against the property it rests on:

1. **Nothing named, nothing rendered.** No ConfigMap, no KT_EXTRAS, no
   checksum: an install that names no extra renders what it did before.
2. **Named, the three agree.** The ConfigMap holds the keys in the console's
   grammar - joined by commas, each once - and the Deployment's KT_EXTRAS names
   that ConfigMap and that key, and the pod template carries its checksum. A
   reference to a name the chart did not render is a pod that never starts
   (CreateContainerConfigError), so the name is compared, not assumed.
3. **extraEnv setting KT_EXTRAS wins.** One KT_EXTRAS entry, the extraEnv one,
   and no ConfigMap or checksum for a list nothing reads.
4. **What is not a list of keys is refused**, naming `extras`: a string, a
   comma or an `=` inside a key, an upper-case key.
5. **`extraSettings` takes the same road**: nothing named, no
   KT_EXTRA_SETTINGS; named, the ConfigMap holds `<page>.<setting>=on|off` in
   key order and the Deployment references it, alone or beside KT_EXTRAS;
   extraEnv setting KT_EXTRA_SETTINGS wins; what is not page -> setting ->
   boolean is refused, naming `extraSettings`.

That the checksum hashes the ConfigMap rendered beside it, and that changing
the list changes the Deployment, is test/checksum-check.py's.
"""

import subprocess
import sys

import yaml

CHART = "./charts/kubetower"


def render(*args):
    cmd = ["helm", "template", "kubetower", CHART, "--set", "image.tag=0.0.0-check", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def shape(*args, variable="KT_EXTRAS"):
    out = render(*args)
    if out.returncode != 0:
        raise SystemExit(f"FAIL  render with {args} failed: {out.stderr.strip()}")
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    maps = {d["metadata"]["name"]: d for d in docs
            if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-extras")}
    dep = [d for d in docs if d["kind"] == "Deployment"][0]
    env = [e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]
           if e["name"] == variable]
    annotations = dep["spec"]["template"]["metadata"].get("annotations") or {}
    return maps, env, annotations.get("checksum/extras")


def settings_failures():
    failures = []
    var = "KT_EXTRA_SETTINGS"

    maps, env, checksum = shape(variable=var)
    if maps or env or checksum:
        failures.append(f"no extraSettings: rendered {sorted(maps)}, {env}, checksum {checksum}")

    for args, want, extras in [
        (["--set", "extraSettings.argocd.list-column=true"], "argocd.list-column=on", None),
        (["--set", "extraSettings.bravo.zed=false", "--set", "extraSettings.alfa.one=true"],
         "alfa.one=on,bravo.zed=off", None),
        (["--set", "extras={alfa}", "--set", "extraSettings.alfa.one=false"], "alfa.one=off", "alfa"),
    ]:
        maps, env, checksum = shape(*args, variable=var)
        if len(maps) != 1:
            failures.append(f"{args}: {len(maps)} extras ConfigMaps, want 1")
            continue
        (name, cm), = maps.items()
        data = {var: want} if extras is None else {"KT_EXTRAS": extras, var: want}
        if cm["data"] != data:
            failures.append(f"{args}: the ConfigMap holds {cm['data']}, want {data}")
        ref = {"configMapKeyRef": {"name": name, "key": var}}
        if env != [{"name": var, "valueFrom": ref}]:
            failures.append(f"{args}: {var} is {env}, want one reference to {name}/{var}")
        if not checksum:
            failures.append(f"{args}: no checksum/extras on the pod template, so a change restarts nothing")
        _, extras_env, _ = shape(*args)
        if (extras is None) != (extras_env == []):
            failures.append(f"{args}: KT_EXTRAS is {extras_env}, which extras did not ask for")

    both = ["--set", "extraSettings.alfa.one=true",
            "--set", f"extraEnv[0].name={var}", "--set", "extraEnv[0].value=alfa.one=off"]
    maps, env, checksum = shape(*both, variable=var)
    if env != [{"name": var, "value": "alfa.one=off"}] or maps or checksum:
        failures.append(f"extraEnv as well: {env}, ConfigMaps {sorted(maps)}, checksum {checksum}; "
                        "want the extraEnv entry alone")

    # What the refusal must name: the value, or - for a key the schema's
    # propertyNames refuses, which helm reports by the key alone - that key.
    for value, named in [("extraSettings=alfa", "extraSettings"),
                         ("extraSettings.alfa=on", "extraSettings"),
                         ("extraSettings.alfa.one=yes", "extraSettings"),
                         ("extraSettings.Alfa.one=true", "Alfa"),
                         ("extraSettings.alfa.One=true", "One")]:
        out = render("--set", value)
        if out.returncode == 0:
            failures.append(f"NOT REFUSED: --set {value}")
        elif named not in out.stderr:
            failures.append(f"--set {value} refused without naming {named}: {out.stderr.strip()}")
    return failures


def check():
    failures = []

    maps, env, checksum = shape()
    if maps or env or checksum:
        failures.append(f"nothing named: rendered {sorted(maps)}, {env}, checksum {checksum}")

    for args, want in [
        (["--set", "extras={alfa}"], "alfa"),
        (["--set", "extras={alfa,bravo}"], "alfa,bravo"),
        (["--set", "extras={bravo,alfa,bravo}"], "bravo,alfa"),
    ]:
        maps, env, checksum = shape(*args)
        if len(maps) != 1:
            failures.append(f"{args}: {len(maps)} extras ConfigMaps, want 1")
            continue
        (name, cm), = maps.items()
        if cm["data"] != {"KT_EXTRAS": want}:
            failures.append(f"{args}: the ConfigMap holds {cm['data']}, want KT_EXTRAS={want}")
        ref = {"configMapKeyRef": {"name": name, "key": "KT_EXTRAS"}}
        if env != [{"name": "KT_EXTRAS", "valueFrom": ref}]:
            failures.append(f"{args}: KT_EXTRAS is {env}, want one reference to {name}/KT_EXTRAS")
        if not checksum:
            failures.append(f"{args}: no checksum/extras on the pod template, so a change restarts nothing")

    both = ["--set", "extras={alfa}",
            "--set", "extraEnv[0].name=KT_EXTRAS", "--set", "extraEnv[0].value=bravo"]
    maps, env, checksum = shape(*both)
    if env != [{"name": "KT_EXTRAS", "value": "bravo"}] or maps or checksum:
        failures.append(f"extraEnv as well: {env}, ConfigMaps {sorted(maps)}, checksum {checksum}; "
                        "want the extraEnv entry alone")

    for value in ["extras=alfa", "extras={alfa=off}", "extras={Alfa}", "extras={-x}"]:
        out = render("--set", value)
        if out.returncode == 0:
            failures.append(f"NOT REFUSED: --set {value}")
        elif "/extras" not in out.stderr:
            failures.append(f"--set {value} refused without naming extras: {out.stderr.strip()}")

    failures += settings_failures()
    return failures


if __name__ == "__main__":
    failures = check()
    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("ok    extras and extraSettings render one ConfigMap, KT_EXTRAS and KT_EXTRA_SETTINGS "
              "reference it and its checksum is on the pod; extraEnv wins; what is not a list of keys, "
              "or page -> setting -> boolean, is refused")
    sys.exit(1 if failures else 0)
