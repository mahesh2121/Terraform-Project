# Challenge 09 — Split a monolithic state into per-team workspaces

**Difficulty:** ★★★★☆ · **Time:** 35 min

## Scenario

One workspace holds 600 resources: networking, databases, and 4 application
stacks. Every plan takes 11 minutes, every apply risks unrelated blast radius,
and three teams queue behind one lock. Split it into `network`, `data`, `apps`
without recreating anything.

## Key concept

`terraform state mv -state-out=` moves a resource **between state files** while
preserving its real-world ID. The resource is removed from the source state and
inserted into the destination state — the cloud is never called.

```
monolith.tfstate ──mv──► network.tfstate
                 ──mv──► data.tfstate
                 └─ remainder stays
```

The hard part isn't the move, it's the **references**. `aws_instance.web` in the
apps state used `aws_subnet.a.id` from the same state. After the split that
reference must become a `terraform_remote_state`/`tfe_outputs` data source, or a
variable. Split along dependency boundaries, not along whim.

## Break it / Do it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c09 && mkdir -p /tmp/c09 && cd /tmp/c09

$SL new monolith.tfstate --lineage mono --serial 20
$SL add-res monolith.tfstate --type aws_vpc         --name main --id vpc-1
$SL add-res monolith.tfstate --type aws_subnet      --name a    --id subnet-a
$SL add-res monolith.tfstate --type aws_db_instance --name pg   --id db-pg
$SL add-res monolith.tfstate --type aws_instance    --name web  --id i-web
$SL show monolith.tfstate

cp monolith.tfstate SAFETY-BACKUP.json           # non-negotiable

# carve out the network state
$SL new network.tfstate --lineage net --serial 1
$SL add-res network.tfstate --type aws_vpc    --name main --id vpc-1
$SL add-res network.tfstate --type aws_subnet --name a    --id subnet-a
$SL rm-res  monolith.tfstate --addr aws_vpc.main
$SL rm-res  monolith.tfstate --addr aws_subnet.a

# carve out data
$SL new data.tfstate --lineage data --serial 1
$SL add-res data.tfstate --type aws_db_instance --name pg --id db-pg
$SL rm-res  monolith.tfstate --addr aws_db_instance.pg

$SL show network.tfstate; $SL show data.tfstate; $SL show monolith.tfstate
```

Real CLI:

```bash
terraform state pull > SAFETY-BACKUP.json

terraform state mv -state-out=network.tfstate aws_vpc.main    aws_vpc.main
terraform state mv -state-out=network.tfstate aws_subnet.a    aws_subnet.a
terraform state mv -state-out=data.tfstate    aws_db_instance.pg aws_db_instance.pg
# whole modules move in one shot:
terraform state mv -state-out=network.tfstate module.vpc module.vpc
```

Then, in each new directory, configure its backend and push:

```bash
cd ../network && terraform init && terraform state push ../monolith/network.tfstate
terraform plan     # No changes
```

## Wiring the references back up

```hcl
# in the apps stack
data "terraform_remote_state" "network" {
  backend = "s3"
  config  = { bucket = "acme-tfstate", key = "prod/network/terraform.tfstate", region = "eu-west-1" }
}

resource "aws_instance" "web" {
  subnet_id = data.terraform_remote_state.network.outputs.subnet_a_id
}
```
…which means the network stack must first **export** those outputs and apply.
Order: add outputs → apply network → split → repoint apps → plan clean.

On TFE prefer `tfe_outputs` (respects workspace access controls, no bucket perms):
```hcl
data "tfe_outputs" "network" { organization = "acme"  workspace = "prod-network" }
```

## Verify

```bash
# 1. Nothing lost: union of new states == original
comm -3 <(terraform state list) <(...)      # per stack
# 2. Each stack plans clean
for d in network data apps; do (cd $d && terraform plan -detailed-exitcode); done
#    exit 0 = no changes, 2 = changes pending (investigate!), 1 = error
```

## Gotchas

* `state mv` between files does **not** copy outputs — recreate them by hand.
* Moving a resource that others still reference in the *same* config = plan error
  until you refactor the HCL. Do HCL and state in the same change window.
* Alternative to mv-surgery: `removed` blocks (TF 1.7+) + `import` blocks in the
  new stack — fully declarative and PR-reviewable:
  ```hcl
  removed { from = aws_vpc.main  lifecycle { destroy = false } }   # old stack
  import  { to   = aws_vpc.main  id = "vpc-1" }                    # new stack
  ```
* Splitting too far creates a distributed monolith: 40 workspaces with 40
  cross-references is worse than one slow plan.

## Discussion questions

1. What's the right split axis: team, lifecycle/change-rate, or blast radius?
2. Why is `removed` + `import` safer than `state mv` for a reviewed workflow?
3. How do you split when a single `module.eks` owns 90 of the 600 resources?
