# Challenge 12 — State written by a newer Terraform / provider

**Difficulty:** ★★★☆☆ · **Time:** 20 min

## Scenario

A developer with Terraform 1.13 on their laptop ran an apply against a workspace
the CI pipeline pins to 1.5.7. Now every pipeline run fails:

```
Error: Failed to load state
state snapshot was created by Terraform v1.13.0, which is newer than current
v1.5.7; upgrade to Terraform v1.13.0 or greater to work with this state
```

A related variant, after a provider downgrade:

```
Error: Resource instance managed by newer provider version
The current state of aws_instance.web was created by a newer provider version
than is currently selected. Upgrade the aws provider to work with this state.
```

## Key concept

Three different versions live in state and they fail differently:

| Field | Example | Direction |
|---|---|---|
| `version` | `4` | **state format**. Terraform upgrades it silently; never downgrades. |
| `terraform_version` | `1.13.0` | CLI that last wrote. Refuses to load with an older CLI. |
| `schema_version` (per resource instance) | `2` | Provider's schema. Provider upgrades it on read; older provider refuses. |

**Upgrades are one-way.** There is no `terraform state downgrade`. The state
format and schema migrations are written by the newer code; the older code has
no idea how to reverse them.

So there are only two real outcomes: **everyone moves up**, or **you restore a
pre-upgrade state version and accept losing the changes made since**.

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c12 && mkdir -p /tmp/c12 && cd /tmp/c12

$SL new remote.tfstate --lineage version-demo --serial 5
$SL add-res remote.tfstate --type aws_instance --name web --id i-1
$SL push remote.tfstate --to backend.tfstate          # snapshot at 1.9.5

$SL set remote.tfstate --tf-version 1.13.0
$SL bump remote.tfstate
$SL push remote.tfstate --to backend.tfstate
jq -r .terraform_version backend.tfstate              # 1.13.0 -> old CI can't read it
```

## Fix it

**Option A — move everyone up (correct, do this):**

```bash
# 1. Pin the version explicitly so it can't drift again
cat >> versions.tf <<'EOF'
terraform {
  required_version = "~> 1.13.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}
EOF
# 2. TFE: Workspace > Settings > Terraform Version = 1.13.0
# 3. CI image: bump the terraform binary, commit the lockfile
terraform init -upgrade
git add .terraform.lock.hcl versions.tf
terraform plan     # No changes expected; a format bump alone changes nothing real
```

**Option B — roll the state back to the pre-upgrade version** (only if you cannot
upgrade CI today, and only if nothing real changed in that apply):

```bash
$SL versions backend.tfstate
$SL rollback backend.tfstate --to-serial 5
jq -r .terraform_version backend.tfstate
```
Real TFE: download the state version created *before* the rogue apply and push
it forward (challenge 02). **Caveat:** if the rogue apply also *changed
infrastructure*, the restored state no longer matches reality → run
`terraform apply -refresh-only` with the old CLI and inspect carefully.

**Never** hand-edit `terraform_version` downward to make the error go away.
The document may already contain 1.13-format constructs the old CLI mis-parses,
and you will silently corrupt resources instead of getting a clean error.

## Provider schema variant

```bash
terraform providers            # what the config wants
jq -r '.resources[] | "\(.type).\(.name) schema_version=\(.instances[0].schema_version)"' state.json
# fix = pin the provider forward in required_providers, then:
terraform init -upgrade
```
`.terraform.lock.hcl` is the control that prevents this — commit it, and let
Dependabot/Renovate raise version bumps as reviewable PRs.

## Verify

```bash
terraform version
terraform state pull | jq -r .terraform_version   # matches your pinned version
terraform plan                                    # No changes
```

## Prevention

* `required_version` with a tight constraint in every root module.
* Commit `.terraform.lock.hcl`; run `terraform providers lock` for all platforms
  your team uses (`-platform=darwin_arm64 -platform=linux_amd64`).
* TFE: set the workspace Terraform version explicitly; disable "latest".
* Block local applies against shared workspaces — TFE remote execution or a
  CI-only role solves this class of incident permanently.

## Discussion questions

1. Why can a *provider* upgrade state schema in place but not downgrade it?
2. State format v3 → v4 was the 0.12 upgrade. What made that one special?
3. Your fleet spans 1.5 → 1.13. Design a safe rollout order across 80 workspaces.
