# Challenge 14 — Resource deleted in the console; state still references it

**Difficulty:** ★★☆☆☆ · **Time:** 20 min

## Scenario

During an incident someone deleted a security group and resized an instance
directly in the AWS console. Now:

```
Error: Error refreshing state: InvalidGroup.NotFound: The security group
'sg-0abc' does not exist
```

and elsewhere the plan proposes reverting the instance type back to `t3.small`,
undoing the emergency fix.

## Key concept

**Drift** = reality ≠ state. Terraform discovers it during *refresh* and reacts:

| Situation | Terraform's default |
|---|---|
| Resource deleted out of band | Plans to **create** it (state entry removed on refresh) |
| Attribute changed out of band | Plans to **change it back** to config |
| Resource created out of band | Invisible — Terraform doesn't know it exists |

Your decision is always one of three:

1. **Reality is right** → update the config to match (and/or `-refresh-only` apply).
2. **Config is right** → let Terraform revert it.
3. **Resource shouldn't be managed here** → `state rm` (and delete it from config).

`terraform state rm` **forgets** a resource without destroying it. `terraform
destroy` deletes it for real. Confusing the two is a classic outage.

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c14 && mkdir -p /tmp/c14 && cd /tmp/c14

$SL new terraform.tfstate --lineage drift-demo --serial 6
$SL add-res terraform.tfstate --type aws_security_group --name web --id sg-0abc
$SL add-res terraform.tfstate --type aws_instance --name web --id i-1 \
   --attrs '{"instance_type":"t3.small"}'

# "cloud reality": sg-0abc deleted, instance resized to t3.large
$SL show terraform.tfstate
```

## Fix it

**Step 1 — see the drift without changing anything:**

```bash
terraform plan -refresh-only
```
This is the safest command in Terraform: it compares state to reality and
proposes only *state* updates, never infrastructure changes. Read it carefully.

**Step 2a — accept reality into state:**
```bash
terraform apply -refresh-only
```
State now says `t3.large` and the missing SG entry is dropped. **Then update the
HCL** to say `t3.large` too, or the next plan reverts it. Refresh-only fixes
state; only a config change makes it durable.

**Step 2b — the deleted SG:** decide.
```bash
# want it back exactly as coded?
terraform apply            # recreates it (new sg-id! update references)

# it should never have been managed here?
terraform state rm aws_security_group.web    # forget, don't destroy
# ...and delete the block from .tf, or use:
#   removed { from = aws_security_group.web  lifecycle { destroy = false } }
```
Simulator:
```bash
$SL rm-res terraform.tfstate --addr aws_security_group.web
$SL show terraform.tfstate
```

**Step 2c — force one resource to be rebuilt** (replaces the deprecated `taint`):
```bash
terraform plan  -replace=aws_instance.web
terraform apply -replace=aws_instance.web
```

## Verify

```bash
terraform plan -detailed-exitcode    # 0 = clean, 2 = drift remains
```
Run this on a schedule — a nightly `plan -detailed-exitcode` per workspace is
the cheapest drift detector you can build. TFE/HCP has **Drift Detection** built
in (health assessments), which does exactly this and raises a workspace alert.

## Gotchas

* `-refresh-only` still needs provider credentials and still **writes state** on
  apply — back up first.
* `refresh = false` in a plan hides drift; useful for speed in CI, dangerous as a
  default.
* Out-of-band *creates* are invisible: only tag-based auditing or Config/
  CloudTrail catches those.
* `state rm` on a resource that other resources depend on leaves dangling
  references in the plan graph — remove the config block in the same change.
* The real fix is organisational: remove console write access, or accept that
  break-glass changes must be back-ported to code within N hours.

## Discussion questions

1. Why did HashiCorp deprecate `taint` in favour of `-replace`?
2. Nightly drift detection is noisy on auto-scaling attributes. How do you tune
   it with `lifecycle { ignore_changes }` without hiding real drift?
3. Someone deleted a resource *and* it's referenced by 8 others. What order do
   you fix things in?
