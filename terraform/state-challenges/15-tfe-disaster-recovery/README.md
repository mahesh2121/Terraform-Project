# Challenge 15 — TFE workspace deleted: full state DR runbook

**Difficulty:** ★★★★★ · **Time:** 45 min · **Capstone**

## Scenario

A cleanup script iterated the TFE API and deleted 6 workspaces it believed were
unused. One of them was `prod-payments`: 180 resources, all still running and
taking traffic. Deleting a workspace in TFE **does not touch infrastructure**,
but it **does discard the state and its entire version history**.

You have: the Git repo with the HCL, cloud access, and whatever backups you set
up *before* today.

## Key concept

Everything in challenges 01–14 assumed the backend still had your history. This
one asks the only question that matters: **what is your RPO for state?**

```
state loss  ──► backup exists?  ──yes──► restore (minutes)   [ch. 01/02/06]
                     │
                     └──no──► rebuild by import (hours-days) [ch. 07]
```

TFE's history is excellent but it is **not a backup** — it lives inside the thing
that can be deleted, and it is subject to retention policy. A backup must be in a
*different failure domain* with a *different deletion path*.

## Part A — Build the backup (do this before the incident)

```bash
#!/usr/bin/env bash
# tfe-state-backup.sh - export the latest state version of every workspace
set -euo pipefail
: "${TFE_TOKEN:?}" ; ORG="${ORG:-acme}" ; API="${API:-https://app.terraform.io/api/v2}"
DEST="${DEST:-s3://acme-tfstate-backups}" ; DAY=$(date -u +%F)
AUTH="Authorization: Bearer $TFE_TOKEN"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

page=1
while :; do
  resp=$(curl -sf -H "$AUTH" "$API/organizations/$ORG/workspaces?page[number]=$page&page[size]=100")
  names=$(jq -r '.data[].attributes.name' <<<"$resp"); [ -z "$names" ] && break
  for ws in $names; do
    sv=$(curl -sf -H "$AUTH" "$API/state-versions?filter[workspace][name]=$ws&filter[organization][name]=$ORG&page[size]=1")
    url=$(jq -r '.data[0].attributes["hosted-state-download-url"] // empty' <<<"$sv")
    ser=$(jq -r '.data[0].attributes.serial // "na"' <<<"$sv")
    [ -z "$url" ] && { echo "SKIP $ws (no state)"; continue; }
    f="$tmp/${ws}.serial-${ser}.tfstate"
    curl -sfL "$url" -o "$f"
    jq -e '.version and .lineage and (.resources|type=="array")' "$f" >/dev/null \
      || { echo "INVALID $ws"; exit 1; }
    sha256sum "$f" > "$f.sha256"
    echo "OK $ws serial=$ser $(jq '.resources|length' "$f") resources"
  done
  page=$((page+1))
done

aws s3 cp --recursive "$tmp" "$DEST/$DAY/" --sse aws:kms
```

Run it daily. The bucket must have **versioning + Object Lock (compliance mode)
+ a deny-delete policy for everyone except a break-glass role**, in a different
account from the one being managed. That is what makes it a backup rather than a
second copy.

Practise the restore quarterly. An untested backup is a hypothesis.

## Part B — The restore

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c15 && mkdir -p /tmp/c15/backups && cd /tmp/c15

# yesterday's backup
$SL new backups/prod-payments.serial-88.tfstate --lineage payments-lineage --serial 88
for r in "aws_vpc main vpc-p" "aws_ecs_cluster main ecs-p" "aws_db_instance pg db-p"; do
  set -- $r; $SL add-res backups/prod-payments.serial-88.tfstate --type $1 --name $2 --id $3
done

# the workspace is gone; a fresh one is empty
$SL new fresh-workspace.tfstate --lineage NEW-EMPTY --serial 1
$SL show fresh-workspace.tfstate
```

Runbook:

```bash
# 1. FREEZE  - disable VCS-driven runs / auto-apply across the org.
#              An apply against an empty state is the actual outage.

# 2. Recreate the workspace, matching the ORIGINAL settings exactly:
#    name, Terraform version, execution mode, working directory, variables,
#    VCS connection. A version mismatch here re-runs challenge 12.
curl -s -XPOST -H "$AUTH" -H 'Content-Type: application/vnd.api+json' \
  "$API/organizations/$ORG/workspaces" -d '{"data":{"type":"workspaces",
   "attributes":{"name":"prod-payments","terraform-version":"1.13.0",
   "auto-apply":false,"execution-mode":"remote"}}}'

# 3. Verify the backup before trusting it
jq -e '.version and .lineage' backups/prod-payments.serial-88.tfstate
sha256sum -c backups/prod-payments.serial-88.tfstate.sha256
jq '.resources|length' backups/prod-payments.serial-88.tfstate    # ~180?

# 4. Lock the new workspace to yourself, then push the backup as its first
#    state version. Keep the ORIGINAL lineage; serial can restart above 0.
terraform init          # cloud block pointing at the recreated workspace
terraform state push backups/prod-payments.serial-88.tfstate
```
Simulator equivalent of step 4:
```bash
cp backups/prod-payments.serial-88.tfstate restored.tfstate
$SL push restored.tfstate --to fresh-workspace.tfstate --force   # empty target
$SL show fresh-workspace.tfstate
```

```bash
# 5. Reconcile the gap between the backup and now (up to 24h of drift):
terraform plan -refresh-only
terraform apply -refresh-only
terraform plan            # anything left is a real delta - review line by line

# 6. Anything created AFTER the backup is missing from state -> import it
#    (challenge 07). Tag-based discovery finds them.

# 7. Unfreeze only after plan is clean.
```

## Verify

```bash
terraform state list | wc -l     # matches the real resource count
terraform plan                   # No changes
```

## Post-incident actions

* API tokens for cleanup automation get **read-only** or a scoped team token.
* Enable *"prevent destroy"* / workspace deletion guard: TFE refuses to delete a
  workspace with managed resources unless `force-delete` is used — ensure your
  automation never passes it.
* Sentinel/OPA policy: block `terraform destroy` runs in prod workspaces.
* Alert on `workspace.destroy` audit-log events.
* Record the RPO you actually achieved and compare it to the one you claimed.

## Capstone exercise

Chain them: corrupt the state (06) → discover no backup → rebuild by import (07)
→ split the result (09) → migrate to a new backend (08) → verify clean plans
throughout. Time yourself. That number is your real DR estimate.

## Discussion questions

1. TFE's state-version history vs. your S3 export: name three failure modes each
   one covers that the other does not.
2. What's your RPO for state, and what's your RTO for a 180-resource rebuild?
3. Why does restoring state *without* freezing runs frequently make it worse?
