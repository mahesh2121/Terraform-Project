# Challenge 02 — Roll back to a previous TFE state version after a bad apply

**Difficulty:** ★★★☆☆ · **Time:** 20 min

## Scenario

A merged PR changed an autoscaling group's launch template and, as a side
effect, removed a security group from state via a bad `for_each` key change.
The apply succeeded, so the workspace is at serial 31 — but 3 resources that
still exist in AWS are no longer tracked. You want the state **content** of
serial 29 back.

## Key concept

TFE state history is **immutable and append-only**. There is no "delete version"
or "set current = old version" operation. Restoring means:

```
download(sv with serial 29)  ->  serial = current + 1  ->  upload as new version
```

The history then reads: 29, 30, 31, **32 (= content of 29)**. Auditors love this;
it means a rollback is itself a recorded, reviewable event.

Also note: rolling back state does **not** roll back infrastructure. State is a
map, not a backup of your cloud. After restoring state you almost always need a
`terraform apply -refresh-only` and then a normal plan to reconcile.

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c02 && mkdir -p /tmp/c02 && cd /tmp/c02

$SL new work.tfstate --lineage app-lineage --serial 1
$SL add-res work.tfstate --type aws_security_group --name web --id sg-web
$SL add-res work.tfstate --type aws_security_group --name db  --id sg-db
$SL add-res work.tfstate --type aws_autoscaling_group --name app --id asg-app
$SL push work.tfstate --to remote.tfstate      # good version

# the bad apply drops both SGs from state
$SL rm-res work.tfstate --addr aws_security_group.web
$SL rm-res work.tfstate --addr aws_security_group.db
$SL push work.tfstate --to remote.tfstate      # accepted: serial went UP
$SL show remote.tfstate
```

Note this push is **legal** — higher serial, same lineage. Terraform's safety
checks do not protect you from a *correct* write of *wrong* content. Only
history does.

## Fix it

```bash
$SL versions remote.tfstate                 # find the 3-resource snapshot
$SL rollback remote.tfstate --to-serial 4   # writes it forward
$SL show remote.tfstate
$SL versions remote.tfstate                 # old versions still there
```

Real TFE:

```bash
API=https://app.terraform.io/api/v2; AUTH="Authorization: Bearer $TFE_TOKEN"
curl -s -H "$AUTH" "$API/workspaces/$WS/state-versions?page[size]=50" \
  | jq -r '.data[] | [.attributes.serial, .id, .attributes["created-at"]] | @tsv'

URL=$(curl -s -H "$AUTH" $API/state-versions/sv-GOOD | jq -r '.data.attributes["hosted-state-download-url"]')
curl -sL "$URL" -o good.tfstate

terraform state pull > backup.tfstate
jq ".serial = $(( $(jq .serial backup.tfstate) + 1 ))" good.tfstate > push.tfstate
terraform state push push.tfstate
```

UI path: *Workspace → States → pick a version → **Advanced → Rollback to this
state version*** (HCP Terraform / TFE v202207+). It performs exactly the
"write forward" above.

## Verify

```bash
terraform plan            # expect: No changes
terraform state list      # all 3 resources present
```

If the plan wants to *create* `aws_security_group.web`, the SG exists in AWS but
not in state → you restored too far back or the rollback didn't take.

## Gotchas

* **Outputs** live in state too; a rollback reverts them, which can break other
  workspaces consuming them via `tfe_outputs`/remote state data sources.
* If the bad apply actually **deleted** cloud resources, restoring state gives
  you a state that references dead IDs. Then you want `-refresh-only` + apply to
  recreate, not a state rollback.
* Rollback requires the workspace to be **locked by you**, and history retention
  (`Settings → Version retention`) must not have pruned the version.

## Discussion questions

1. Rollback vs. `terraform apply -refresh-only`: when is each correct?
2. Your workspace has 400 state versions and retention is 30 days. Design the
   backup so a 60-day-old restore is still possible.
3. Why can rolling back state be *more* dangerous than leaving it broken?
