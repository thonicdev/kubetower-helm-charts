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


def shape(*args):
    out = render(*args)
    if out.returncode != 0:
        raise SystemExit(f"FAIL  render with {args} failed: {out.stderr.strip()}")
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    maps = {d["metadata"]["name"]: d for d in docs
            if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-extras")}
    dep = [d for d in docs if d["kind"] == "Deployment"][0]
    env = [e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]
           if e["name"] == "KT_EXTRAS"]
    annotations = dep["spec"]["template"]["metadata"].get("annotations") or {}
    return maps, env, annotations.get("checksum/extras")


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

    return failures


if __name__ == "__main__":
    failures = check()
    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("ok    extras renders one ConfigMap, KT_EXTRAS references it and its checksum is on the pod; "
              "extraEnv wins; what is not a list of keys is refused")
    sys.exit(1 if failures else 0)
