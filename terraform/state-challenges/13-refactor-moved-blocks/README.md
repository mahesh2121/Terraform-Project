# Challenge 13 — Refactor renames a resource; plan wants destroy + recreate

**Difficulty:** ★★★☆☆ · **Time:** 25 min

## Scenario

You rename `aws_instance.web` to `aws_instance.api`, and wrap the networking in
a module. The plan:

```
Plan: 6 to add, 0 to change, 6 to destroy.
```

Nothing about the infrastructure changed — only the *addresses* in your config.
Applying this would destroy and rebuild production.

## Key concept

Terraform identifies resources by their **address in state**
(`module.net.aws_subnet.a[0]`), not by their cloud ID. Rename the address and
Terraform sees:

* an entry in state with no matching config → **destroy**
* a config block with no matching state → **create**

The fix is to tell Terraform the old address and the new address are the same
resource. Two ways:

| | `moved` block | `terraform state mv` |
|---|---|---|
| Where | in `.tf`, committed | imperative CLI |
| Reviewable in PR | yes | no |
| Runs in CI/TFE | yes, automatically | needs a human with backend creds |
| Applies to every workspace/env | yes | once per state |
| Cleanup | delete the block after all envs applied | n/a |

**Prefer `moved` blocks.** `state mv` is for what `moved` can't express
(cross-state moves — challenge 09).

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c13 && mkdir -p /tmp/c13 && cd /tmp/c13

$SL new terraform.tfstate --lineage refactor --serial 7
$SL add-res terraform.tfstate --type aws_instance --name web --id i-keepme
$SL show terraform.tfstate      # config now says aws_instance.api -> mismatch
```

## Fix it

**Declarative (`moved` blocks, TF 1.1+):**

```hcl
# main.tf
resource "aws_instance" "api" { /* ... unchanged body ... */ }

moved {
  from = aws_instance.web
  to   = aws_instance.api
}

# moving into a module
moved {
  from = aws_subnet.a
  to   = module.network.aws_subnet.a
}

# count -> for_each
moved {
  from = aws_subnet.this[0]
  to   = aws_subnet.this["eu-west-1a"]
}

# a whole module rename
moved {
  from = module.vpc
  to   = module.network
}
```

```bash
terraform plan
# Plan: 0 to add, 0 to change, 0 to destroy.
#   aws_instance.web has moved to aws_instance.api
terraform apply    # rewrites state addresses only; no API calls to AWS
```

**Imperative equivalent:**

```bash
terraform state pull > backup.json
terraform state mv aws_instance.web aws_instance.api
terraform state mv aws_subnet.a 'module.network.aws_subnet.a'
terraform state mv 'aws_subnet.this[0]' 'aws_subnet.this["eu-west-1a"]'
```
Simulator:
```bash
$SL mv-res terraform.tfstate --from aws_instance.web --to aws_instance.api
$SL show terraform.tfstate     # same id i-keepme, new address
```

## The `count` → `for_each` trap

This is the most common real occurrence. Switching from `count` to `for_each`
changes every address from `[0]`,`[1]` to `["a"]`,`["b"]`, so a 30-subnet module
proposes 30 destroys. Worse, `count` indexes **shift**: deleting the first
element renumbers everything after it, so a one-line list change can propose
recreating half your fleet. `for_each` with stable keys is the cure — and
`moved` blocks are how you get there without downtime.

## Verify

```bash
terraform plan -detailed-exitcode    # exit 0 == no changes
terraform state list                 # new addresses
terraform state show aws_instance.api | grep '^  id'   # SAME cloud id as before
```

The critical check: **the cloud ID must be unchanged.** If it changed, you
destroyed and recreated, you didn't move.

## Gotchas

* Keep `moved` blocks until **every** environment has applied them, then remove.
  Deleting too early re-proposes the destroy for lagging workspaces.
* `moved` cannot cross state files or providers.
* `removed` blocks (1.7+) are the sibling: drop a resource from config *without*
  destroying it — `removed { from = aws_instance.old  lifecycle { destroy = false } }`.
* Module authors: ship `moved` blocks inside the module when you rename internal
  resources; that's the difference between a patch release and a breaking one.

## Discussion questions

1. Why can't Terraform infer the rename automatically by matching cloud IDs?
2. When is destroy+recreate actually *preferable* to a move?
3. How do `moved`, `removed` and `import` blocks together make state surgery a
   code-review artefact instead of a 2am console session?
