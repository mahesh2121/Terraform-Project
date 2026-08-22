# Challenge 04 — `Invalid state lineage` after a backend re-init

**Difficulty:** ★★★★☆ · **Time:** 25 min

## Scenario

A workspace's S3 backend key was mistyped in a PR. Terraform found no state at
the new key, so `terraform init` created a **brand-new empty state** with a
**new lineage**, and the next apply built a parallel copy of the stack. Someone
noticed, fixed the key back — and now pushing gives:

```
Error: Invalid state lineage; lineage "3f9a...-b1" does not match "8c22...-7d"
```

## Key concept

`lineage` is a UUID minted **once**, when a state file is first created. It
answers: *"are these two files snapshots of the same state, or two unrelated
states that happen to describe similar resources?"*

Serial says *how new*. Lineage says *whose*. A serial comparison between
different lineages is meaningless — that's why Terraform refuses before it even
looks at the serial.

Seeing this error means one of:

| Cause | Real meaning |
|---|---|
| Backend key/workspace changed | You're pushing into a different state's slot |
| Someone ran `init` on an empty backend | A second bloodline was created and applied |
| Restoring from a backup of a *different* workspace | Wrong file entirely — stop |
| State was deleted and recreated | The old lineage is gone; infra may be double-managed |

**The dangerous part is not the error — it's the duplicate infrastructure.**
Two lineages that both "own" resources means one of them will destroy the
other's on next apply.

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c04 && mkdir -p /tmp/c04 && cd /tmp/c04

$SL new remote.tfstate --lineage AAAA-original --serial 5
$SL add-res remote.tfstate --type aws_vpc --name main --id vpc-real

# accidental re-init created a second bloodline that also applied
$SL new orphan.tfstate --lineage BBBB-accidental --serial 3
$SL add-res orphan.tfstate --type aws_vpc --name main --id vpc-duplicate

$SL push orphan.tfstate --to remote.tfstate ; echo "exit=$?"
```

## Fix it

**Step 1 — decide which lineage is the source of truth.** Compare resource IDs
against reality:

```bash
$SL show remote.tfstate     # vpc-real
$SL show orphan.tfstate     # vpc-duplicate
aws ec2 describe-vpcs --vpc-ids vpc-real vpc-duplicate   # both exist?
```

**Step 2 — deal with the duplicate infrastructure first.** Either destroy the
orphan stack using the orphan state (`terraform destroy` with that backend
configured), or keep it and abandon the other. Do this *before* any state
surgery, while you still have a state that can address the resources.

**Step 3 — reconcile into one lineage.** Two options:

*A. Keep the canonical state, import anything missing (preferred):*
```bash
terraform state pull > canonical.tfstate     # lineage AAAA
terraform import aws_vpc.other vpc-somethingelse
```

*B. Adopt the orphan's content under the canonical lineage (surgical):*
```bash
cp remote.tfstate backup-before-lineage-fix.json
jq --arg l "$(jq -r .lineage remote.tfstate)" \
   --argjson s "$(( $(jq .serial remote.tfstate) + 1 ))" \
   '.lineage=$l | .serial=$s' orphan.tfstate > adopted.tfstate
terraform state push adopted.tfstate    # no -force needed: lineage+serial now valid
```
Simulator equivalent:
```bash
$SL set  orphan.tfstate --lineage AAAA-original --serial 7
$SL push orphan.tfstate --to remote.tfstate
```

> Rewriting lineage is **lying to Terraform** about provenance. Only do it when
> you have verified the resource IDs are the ones you actually want managed, and
> only with a backup in hand.

## Verify

```bash
terraform plan     # No changes
# and: no duplicate VPC/SG/LB left running in the account
```

## Prevention

* Pin backend config in code and review changes to `key`/`workspace` like prod code.
* Use `terraform init -backend-config=env/prod.hcl` files, never hand-typed keys.
* Enable S3 bucket versioning + a DynamoDB lock table; TFE gives you both free.
* Alert on "apply created N resources in a workspace that normally changes 0".

## Discussion questions

1. Why doesn't Terraform just merge two states with the same resources?
2. `-force` bypasses lineage too. Describe a scenario where forcing it destroys
   production even though the plan looked clean.
3. How could `terraform import` blocks (1.5+) make Step 3A reviewable in a PR?
