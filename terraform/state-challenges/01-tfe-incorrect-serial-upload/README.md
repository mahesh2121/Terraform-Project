# Challenge 01 — Incorrect serial number uploaded to Terraform Enterprise

**Difficulty:** ★★★★☆ · **Time:** 25 min · This is *the* interview question.

## Scenario

Friday 18:40. An engineer was debugging a workspace. They had an **old copy** of
`terraform.tfstate` on their laptop (serial **7**, 12 resources). The live TFE
workspace was at serial **11** (18 resources, including yesterday's new RDS
instance and 3 subnets).

They ran:

```bash
terraform state push -force old.tfstate     # <-- the incident
```

TFE accepted it because `-force` skips the serial check. The workspace now
reports serial 7 (TFE actually stores it as a *new* state version, but the
*content* is the old one).

Ten minutes later a CI pipeline runs `terraform apply`. Its plan says:

```
Plan: 6 to add, 0 to change, 0 to destroy.
```

Terraform wants to **re-create the RDS instance and subnets that already exist**,
because they are no longer in state. If that apply lands you get duplicate
infra, or a hard failure on a name/CIDR conflict.

**Your job: stop the bleeding and restore the workspace to the serial-11 content.**

---

## Key concept: why the serial matters

`serial` is Terraform's **optimistic concurrency token**. On every write the
backend checks `incoming.serial > current.serial`. Two consequences:

* A **lower** serial means "your view of the world is older than mine" → reject.
* Forcing a lower serial does **not** delete the newer state from TFE's history.
  TFE keeps **every** state version ever written. That is what saves you here.

So the restore is *not* "put the serial back to 11". It is:

> take the **content** of state version with serial 11, and write it forward
> as a **new** state version with a serial higher than whatever is current.

---

## Break it (simulate the incident)

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c01 && mkdir -p /tmp/c01 && cd /tmp/c01

# Build the workspace history up to serial 11-ish
$SL new remote.tfstate --lineage prod-lineage-0001 --serial 1
$SL add-res remote.tfstate --type aws_vpc      --name main --id vpc-01
$SL add-res remote.tfstate --type aws_subnet   --name a    --id subnet-a
$SL add-res remote.tfstate --type aws_subnet   --name b    --id subnet-b
cp remote.tfstate good.tfstate          # <- this is the healthy snapshot
mkdir -p .history && cp remote.tfstate .history/remote.tfstate.000004.json

# The engineer's stale laptop copy: same lineage, older serial, fewer resources
$SL new  stale.tfstate --lineage prod-lineage-0001 --serial 2
$SL add-res stale.tfstate --type aws_vpc --name main --id vpc-01

# The safe push is refused:
$SL push stale.tfstate --to remote.tfstate ; echo "exit=$?"

# ...so they forced it. THE INCIDENT:
$SL push stale.tfstate --to remote.tfstate --force
$SL show remote.tfstate        # subnets are GONE from state (but alive in AWS)
```

Real CLI equivalent of the failure message:

```
Error: cannot import state with serial 2 over state with serial 4; use the -force
flag to disable this check
```

## Fix it

```bash
# 0. FREEZE. Lock the workspace so no pipeline can apply.
#    TFE UI: Settings > Lock  |  API: POST /workspaces/:id/actions/lock
#    OSS:    terraform force-unlock is the opposite - do not touch it here.

# 1. Snapshot the current (bad) state before touching anything.
cp remote.tfstate incident-$(date +%s).bad.json

# 2. Find the last good state version.
$SL versions remote.tfstate

# 3. Restore its content forward as a NEW, higher serial.
$SL rollback remote.tfstate --to-serial 4

# 4. Confirm.
$SL show remote.tfstate        # subnets back; serial is now 5, not 4
$SL versions remote.tfstate
```

### The same thing against real Terraform Enterprise / HCP Terraform

```bash
WS=ws-XXXXXXXX
AUTH="Authorization: Bearer $TFE_TOKEN"
API=https://app.terraform.io/api/v2      # or https://tfe.mycorp.com/api/v2

# a) Lock the workspace
curl -s -XPOST -H "$AUTH" $API/workspaces/$WS/actions/lock \
     -H 'Content-Type: application/vnd.api+json' -d '{"reason":"state incident"}'

# b) List state versions (newest first) and find the good serial
curl -s -H "$AUTH" "$API/workspaces/$WS/state-versions?page[size]=20" \
  | jq -r '.data[] | [.attributes.serial, .attributes["created-at"], .id] | @tsv'

# c) Download the good snapshot
URL=$(curl -s -H "$AUTH" $API/state-versions/sv-GOODID \
      | jq -r '.data.attributes["hosted-state-download-url"]')
curl -s -L "$URL" -o good.tfstate

# d) Push it back through the CLI (simplest, keeps lineage/serial handling sane)
terraform state pull > current-bad.tfstate            # extra backup
jq --argjson s "$(( $(jq .serial current-bad.tfstate) + 1 ))" '.serial=$s' \
   good.tfstate > restore.tfstate
terraform state push restore.tfstate

# e) Unlock
curl -s -XPOST -H "$AUTH" $API/workspaces/$WS/actions/unlock
```

> Raw-API alternative to (d): `POST /workspaces/:id/state-versions` with
> `serial`, `md5` and base64 `state` — the workspace **must be locked by you**
> and the serial must exceed the current one.

## Verify

```
terraform plan   ->   No changes. Your infrastructure matches the configuration.
```

That, and nothing else, is the pass condition. If the plan still wants to
*create* resources you know exist, you restored the wrong version.

## Post-incident hardening

* Remove `-force` from every runbook and CI script; grep your repos for it.
* TFE: restrict who can lock/unlock and who holds `write` on the workspace.
* Never keep long-lived local copies of remote state; `state pull` on demand.
* Add a nightly job that exports every workspace's latest state version to
  object storage with versioning + object-lock (see challenge 15).
* Turn on run notifications so a surprise "6 to add" plan pings a channel.

## Discussion questions

1. Why doesn't setting `serial` back to 11 by hand fix it?
2. What would have happened if the stale file had a **different lineage**?
3. The push was forced *and* TFE history was pruned to 5 versions. Now what?
4. How would `terraform apply -refresh-only` have helped — and where would it fail?
