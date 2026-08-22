# Challenge 08 — Migrate state: local → S3 → Terraform Enterprise, zero drift

**Difficulty:** ★★★☆☆ · **Time:** 30 min

## Scenario

A team started with local state on one laptop. You must move it to S3 (shared,
locked), then onboard the workspace to Terraform Enterprise — without a single
resource being recreated, and with a clean rollback plan at each hop.

## Key concept

Migration is just **copying the state document to a new home and repointing the
backend block**. `terraform init` detects the change and offers to copy:

```
Do you want to copy existing state to the new backend?
```

What actually happens:
* the state's **lineage is preserved** (same bloodline, new location);
* the serial continues (it may bump by one for the write);
* **nothing in the cloud is touched.**

The failure modes are all human: answering "no" and losing the pointer, running
`init` before committing the backend change, or two people migrating at once.

## Break it / Do it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c08 && mkdir -p /tmp/c08 && cd /tmp/c08

$SL new local.tfstate --lineage migrate-me --serial 4
$SL add-res local.tfstate --type aws_vpc      --name main --id vpc-1
$SL add-res local.tfstate --type aws_instance --name web  --id i-1

# hop 1: local -> "s3"
$SL push local.tfstate --to s3.tfstate
$SL show s3.tfstate            # same lineage 'migrate-me' -> proof it's a move

# hop 2: s3 -> "tfe"
cp s3.tfstate tfe.tfstate      # `terraform init -migrate-state` does this for you
$SL show tfe.tfstate
```

Real sequence, hop 1:

```hcl
# backend.tf
terraform {
  backend "s3" {
    bucket         = "acme-tfstate"
    key            = "prod/network/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "terraform-locks"   # or use_lockfile = true on TF >= 1.10
    encrypt        = true
  }
}
```
```bash
cp terraform.tfstate pre-migration-$(date +%s).tfstate    # 1. backup
terraform init -migrate-state                              # 2. answer "yes"
terraform plan                                             # 3. MUST be No changes
git add backend.tf && git commit -m "move state to s3"     # 4. only now
```

Hop 2, onto TFE with the `cloud` block:

```hcl
terraform {
  cloud {
    organization = "acme"
    workspaces { name = "prod-network" }
  }
}
```
```bash
terraform state pull > pre-tfe-$(date +%s).tfstate
terraform init                 # detects s3 -> cloud, offers migration
terraform plan
```

Alternative when `init` migration is refused (e.g. cross-org, or you want it
explicit):
```bash
terraform state pull > move.tfstate     # from OLD backend
# repoint backend, then:
terraform init -reconfigure             # do NOT copy
terraform state push move.tfstate       # first write into the empty workspace
```

## `-migrate-state` vs `-reconfigure` — the exam question

| Flag | Meaning |
|---|---|
| `-migrate-state` | "Copy the state from the old backend into the new one." |
| `-reconfigure` | "Forget the old backend entirely; do **not** copy." Starts empty. |
| `-backend=false` | Skip backend init (useful for `validate`-only CI jobs). |

Choosing `-reconfigure` when you meant `-migrate-state` is how you end up in
challenge 04 (a fresh lineage) and challenge 07 (an empty state).

## Verify at every hop

```bash
terraform state list | sort > after.txt
diff before.txt after.txt         # must be empty
terraform plan                    # No changes
jq -r .lineage <old> <new>        # lineage identical
```

## Gotchas

* Delete/lock down the **old** state afterwards, or someone will `init` against
  it and you get two live lineages.
* Workspaces (`terraform workspace`) migrate all-or-nothing; S3 stores them under
  `env:/<name>/<key>` while TFE wants separate workspaces — plan the name mapping.
* TFE runs execute *remotely* by default: provider credentials must be set as
  workspace variables, not just on your laptop. Or set execution mode to `local`.
* `.terraform/` is a cache, not state. Deleting it is always safe.

## Discussion questions

1. Why must `terraform plan` be clean *before* you commit the backend change?
2. You migrated 12 workspaces; one shows "3 to add". What went wrong, and which
   of challenges 04/07 is it?
3. What's the rollback procedure at hop 2, at 02:00, with a locked workspace?
