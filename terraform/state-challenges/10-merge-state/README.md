# Challenge 10 — Merge two workspaces' state into one

**Difficulty:** ★★★★☆ · **Time:** 30 min

## Scenario

Two teams built `legacy-api` and `new-api` as separate workspaces; they share a
VPC, an ALB and half their security groups, and every change now requires two
coordinated applies in a fixed order. Consolidate them into one workspace.

## Key concept

Merging is challenge 09 in reverse, but with two extra hazards:

1. **Address collisions.** Both states may contain `aws_security_group.web`
   pointing at *different* SGs. You must rename during the move.
2. **Lineage.** The merged state keeps exactly one lineage — the destination's.
   The other lineage simply ends. Its history stays in TFE but will never be
   appended to again; document which workspace absorbed which.

The serial of the destination just keeps counting up; there is no "merge serial".

## Break it / Do it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c10 && mkdir -p /tmp/c10 && cd /tmp/c10

$SL new a.tfstate --lineage lineage-A --serial 9
$SL add-res a.tfstate --type aws_vpc            --name main --id vpc-1
$SL add-res a.tfstate --type aws_security_group --name web  --id sg-legacy

$SL new b.tfstate --lineage lineage-B --serial 4
$SL add-res b.tfstate --type aws_security_group --name web  --id sg-new     # COLLISION
$SL add-res b.tfstate --type aws_lb             --name front --id lb-1

cp a.tfstate a.BACKUP.json ; cp b.tfstate b.BACKUP.json

# resolve the collision BEFORE merging
$SL mv-res b.tfstate --from aws_security_group.web --to aws_security_group.newapi

# merge b -> a
$SL add-res a.tfstate --type aws_security_group --name newapi --id sg-new
$SL add-res a.tfstate --type aws_lb             --name front  --id lb-1
$SL rm-res  b.tfstate --addr aws_security_group.newapi
$SL rm-res  b.tfstate --addr aws_lb.front

$SL show a.tfstate      # 4 resources, lineage-A
$SL show b.tfstate      # empty
```

Real CLI (run from the *source* directory, pushing into a local copy of the dest):

```bash
cd new-api
terraform state pull > ../b.tfstate
cd ../legacy-api
terraform state pull > ../a.tfstate

# move each resource from b into a, renaming collisions
terraform state mv -state=../b.tfstate -state-out=../a.tfstate \
  aws_security_group.web aws_security_group.newapi
terraform state mv -state=../b.tfstate -state-out=../a.tfstate \
  aws_lb.front aws_lb.front

# push the merged result (serial auto-handled if you use the verbs against the
# live backend instead; with files, bump it explicitly)
jq ".serial = $(( $(terraform state pull | jq .serial) + 1 ))" ../a.tfstate > push.tfstate
terraform state push push.tfstate
```

Then decommission the source workspace **without destroying infrastructure**:

```hcl
# in new-api, before deleting the workspace
removed { from = aws_lb.front  lifecycle { destroy = false } }
```
or simply delete the workspace in TFE (deleting a workspace never touches cloud
resources — but it *does* discard the state, so archive it first: challenge 15).

## Also merge the HCL

State surgery without config surgery leaves you with a state that plans
"1 to destroy" for everything you moved. The merged workspace's `.tf` must now
declare **all** resources, with the names you chose during the move. Do both in
one change window, plan-only, before touching the backend.

## Verify

```bash
terraform state list | sort            # union of both, no duplicates
terraform plan                         # No changes
# source workspace:
terraform state list                   # empty
```

## Gotchas

* Two resources with the same *cloud* identity in both states (genuinely double
  managed) → `state rm` from one, don't move.
* Outputs must be re-declared in the destination; consumers referencing the old
  workspace's outputs break at the same moment. Sequence the cutover.
* Provider configuration/aliases must exist in the destination or the moved
  resources are unmanageable (`provider configuration not present`).
* Keep the source state archived for at least one incident cycle.

## Discussion questions

1. Why does merging usually *increase* blast radius, and when is it still right?
2. How do `moved` blocks (challenge 13) differ from cross-state `state mv`?
3. What breaks if the two workspaces used different provider major versions?
