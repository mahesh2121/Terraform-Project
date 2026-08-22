#!/usr/bin/env python3
"""
statelab.py - a tiny Terraform *state* simulator.

Why: every one of these challenges is really about 4 fields in the state JSON
(version, terraform_version, serial, lineage) plus the `resources` array.
You do NOT need AWS creds or a TFE licence to *understand* the concept -
this script lets you break and repair state files locally, exactly the way
Terraform / Terraform Enterprise would.

Usage:
  statelab.py new       <file> [--lineage L] [--serial N]
  statelab.py show      <file>
  statelab.py bump      <file> [--by 1]
  statelab.py set       <file> [--serial N] [--lineage L] [--tf-version V]
  statelab.py add-res   <file> --type aws_instance --name web --id i-123
  statelab.py rm-res    <file> --addr aws_instance.web
  statelab.py mv-res    <file> --from aws_instance.web --to aws_instance.app
  statelab.py corrupt   <file> [--mode truncate|badjson|nulls]
  statelab.py push      <file> --to <remote.tfstate>      # enforces TFE rules
  statelab.py versions  <remote.tfstate>                  # list snapshot history
  statelab.py rollback  <remote.tfstate> --to-serial N
"""
import argparse, json, os, shutil, sys, uuid, glob, time

HIST = ".history"


def load(p):
    with open(p) as f:
        return json.load(f)


def save(p, s):
    with open(p, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")


def new_state(lineage=None, serial=1):
    return {
        "version": 4,
        "terraform_version": "1.9.5",
        "serial": serial,
        "lineage": lineage or str(uuid.uuid4()),
        "outputs": {},
        "resources": [],
    }


def res_addr(r):
    return f"{r['type']}.{r['name']}"


def snapshot(remote):
    """Emulate a TFE 'state version' - every accepted push is archived."""
    d = os.path.join(os.path.dirname(os.path.abspath(remote)), HIST)
    os.makedirs(d, exist_ok=True)
    s = load(remote)
    shutil.copy(remote, os.path.join(d, f"{os.path.basename(remote)}.{s['serial']:06d}.json"))


def main():
    ap = argparse.ArgumentParser(add_help=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def f(name, *args):
        p = sub.add_parser(name)
        p.add_argument("file")
        for a, kw in args:
            p.add_argument(a, **kw)
        return p

    f("new", ("--lineage", {}), ("--serial", {"type": int, "default": 1}))
    f("show")
    f("bump", ("--by", {"type": int, "default": 1}))
    f("set", ("--serial", {"type": int}), ("--lineage", {}), ("--tf-version", {}))
    f("add-res", ("--type", {"required": True}), ("--name", {"required": True}),
      ("--id", {"required": True}), ("--attrs", {"default": "{}"}))
    f("rm-res", ("--addr", {"required": True}))
    f("mv-res", ("--from", {"dest": "src", "required": True}), ("--to", {"dest": "dst", "required": True}))
    f("corrupt", ("--mode", {"default": "truncate"}))
    f("push", ("--to", {"required": True}), ("--force", {"action": "store_true"}))
    f("versions")
    f("rollback", ("--to-serial", {"type": int, "required": True}))

    a = ap.parse_args()

    if a.cmd == "new":
        save(a.file, new_state(a.lineage, a.serial))
        print(f"created {a.file}")
        return

    if a.cmd == "corrupt":
        raw = open(a.file).read()
        if a.mode == "truncate":
            raw = raw[: int(len(raw) * 0.6)]
        elif a.mode == "badjson":
            raw = raw.replace('"resources"', '"resources",', 1)
        elif a.mode == "nulls":
            raw = raw.replace('"instances"', '"instances_BROKEN"')
        open(a.file, "w").write(raw)
        print(f"corrupted {a.file} (mode={a.mode})")
        return

    if a.cmd == "versions":
        d = os.path.join(os.path.dirname(os.path.abspath(a.file)), HIST)
        files = sorted(glob.glob(os.path.join(d, os.path.basename(a.file) + ".*.json")))
        cur = load(a.file)
        print(f"{'SERIAL':>8}  {'RESOURCES':>9}  SNAPSHOT")
        for p in files:
            s = load(p)
            print(f"{s['serial']:>8}  {len(s['resources']):>9}  {os.path.basename(p)}")
        print(f"{cur['serial']:>8}  {len(cur['resources']):>9}  <-- CURRENT")
        return

    if a.cmd == "rollback":
        d = os.path.join(os.path.dirname(os.path.abspath(a.file)), HIST)
        src = os.path.join(d, f"{os.path.basename(a.file)}.{a.to_serial:06d}.json")
        if not os.path.exists(src):
            sys.exit(f"no snapshot with serial {a.to_serial}")
        try:
            cur_serial = load(a.file)["serial"]
        except Exception as e:
            # Current state is corrupt/unreadable - exactly challenge 06.
            print(f"warning: current state unreadable ({e}); using history only")
            cur_serial = 0
        old = load(src)
        # THE key lesson: a rollback is a *forward* write of old content.
        highest = max([load(p)["serial"] for p in
                       glob.glob(os.path.join(d, os.path.basename(a.file) + ".*.json"))]
                      + [cur_serial])
        old["serial"] = highest + 1
        save(a.file, old)
        snapshot(a.file)
        print(f"rolled back content of serial {a.to_serial} -> written as NEW serial {old['serial']}")
        return

    if a.cmd == "push":
        src, dst = load(a.file), a.to
        if not os.path.exists(dst):
            save(dst, src); snapshot(dst); print("accepted (new state)"); return
        cur = load(dst)
        if src["lineage"] != cur["lineage"] and not a.force:
            sys.exit(f"Error: Invalid state lineage\n  remote: {cur['lineage']}\n  local : {src['lineage']}\n"
                     "  Hint: states come from different `terraform init`s. Do NOT force blindly.")
        if src["serial"] < cur["serial"] and not a.force:
            sys.exit(f"Error: cannot import state with serial {src['serial']} over state with serial {cur['serial']}\n"
                     "  Hint: pull remote, re-apply your change, bump serial above remote.")
        if src["serial"] == cur["serial"] and src != cur and not a.force:
            sys.exit(f"Error: serial {src['serial']} already exists remotely with DIFFERENT content (fork!).\n"
                     "  Hint: bump the serial - never reuse one.")
        save(dst, src); snapshot(dst); print(f"accepted -> remote serial {src['serial']}")
        return

    s = load(a.file)
    if a.cmd == "show":
        print(json.dumps({k: s[k] for k in ("version", "terraform_version", "serial", "lineage")}, indent=2))
        for r in s["resources"]:
            ids = [i.get("attributes", {}).get("id") for i in r.get("instances", [])]
            print(f"  {res_addr(r):<40} {ids}")
        return
    if a.cmd == "bump":
        s["serial"] += a.by
    elif a.cmd == "set":
        if a.serial is not None: s["serial"] = a.serial
        if a.lineage: s["lineage"] = a.lineage
        if a.tf_version: s["terraform_version"] = a.tf_version
    elif a.cmd == "add-res":
        attrs = json.loads(a.attrs); attrs["id"] = a.id
        s["resources"].append({"mode": "managed", "type": a.type, "name": a.name,
                               "provider": f'provider["registry.terraform.io/hashicorp/{a.type.split("_")[0]}"]',
                               "instances": [{"schema_version": 0, "attributes": attrs}]})
        s["serial"] += 1
    elif a.cmd == "rm-res":
        n = len(s["resources"])
        s["resources"] = [r for r in s["resources"] if res_addr(r) != a.addr]
        if len(s["resources"]) == n: sys.exit(f"no such resource {a.addr}")
        s["serial"] += 1
    elif a.cmd == "mv-res":
        for r in s["resources"]:
            if res_addr(r) == a.src:
                r["type"], r["name"] = a.dst.split(".")
                break
        else:
            sys.exit(f"no such resource {a.src}")
        s["serial"] += 1
    save(a.file, s)
    print(f"ok -> serial {s['serial']}")


if __name__ == "__main__":
    main()
