# Challenge 05 — Stuck state lock after a killed apply

**Difficulty:** ★★☆☆☆ · **Time:** 15 min

## Scenario

A CI runner was terminated mid-`apply` (spot reclaim). Every subsequent run:

```
Error: Error acquiring the state lock
Lock Info:
  ID:        7f0e4b0c-2f83-...
  Path:      my-bucket/prod/terraform.tfstate
  Operation: OperationTypeApply
  Who:       runner@ci-77d
  Created:   2026-08-22 09:14:03 UTC
```

## Key concept

The serial protects against *stale writes*; the **lock** protects against
*concurrent writes*. Backends implement it differently:

| Backend | Lock mechanism |
|---|---|
| S3 | DynamoDB item `LockID = <bucket>/<key>` (or S3 native lockfile, TF 1.10+) |
| TFE / HCP | Workspace lock (also settable by humans in the UI) |
| azurerm | Blob lease |
| gcs | Object generation precondition |
| local | `.terraform.tfstate.lock.info` file |

A crashed process leaves the lock claimed but no writer alive. **The real danger:
you cannot tell from the lock alone whether the apply is dead or still running.**
Force-unlocking a *live* apply lets a second apply run concurrently → interleaved
writes → corrupt state (challenge 06) and duplicated resources.

## Break it (local backend, fully offline)

```bash
rm -rf /tmp/c05 && mkdir -p /tmp/c05 && cd /tmp/c05
cat > terraform.tfstate <<'EOF'
{"version":4,"terraform_version":"1.9.5","serial":3,"lineage":"lock-demo","outputs":{},"resources":[]}
EOF
cat > .terraform.tfstate.lock.info <<'EOF'
{"ID":"7f0e4b0c-2f83-4c1d-9a11-abc123456789","Operation":"OperationTypeApply",
 "Info":"","Who":"runner@ci-77d","Version":"1.9.5","Created":"2026-08-22T09:14:03Z",
 "Path":"terraform.tfstate"}
EOF
cat .terraform.tfstate.lock.info | jq .
```

## Fix it — the checklist, in order

```bash
# 1. PROVE the holder is dead. Never skip this.
#    - CI: open the run URL from "Who"/"Info". Is the job still executing?
#    - EC2/runner: is the process alive?  ps -ef | grep terraform
#    - TFE: Workspace > Runs. Is there a run in "Applying"?
#    Wait if in doubt. An apply can legitimately take 45+ minutes.

# 2. Back up state before unlocking (an unlock precedes a write).
terraform state pull > pre-unlock-$(date +%s).tfstate

# 3. Release the lock with the ID from the error message.
terraform force-unlock 7f0e4b0c-2f83-4c1d-9a11-abc123456789
#    add -force to skip the interactive confirm (CI only)

# local backend simulation:
rm .terraform.tfstate.lock.info

# 4. Reconcile: the killed apply may have created resources not in state.
terraform plan -refresh-only
terraform apply -refresh-only        # adopt drift into state
terraform plan                       # now judge the real delta
```

Backend-specific release when the CLI can't help:

```bash
# S3 + DynamoDB
aws dynamodb delete-item --table-name terraform-locks \
  --key '{"LockID":{"S":"my-bucket/prod/terraform.tfstate"}}'
# also check the digest item: "<bucket>/<key>-md5"

# TFE / HCP
curl -XPOST -H "Authorization: Bearer $TFE_TOKEN" \
  https://app.terraform.io/api/v2/workspaces/$WS/actions/force-unlock
```

## The dangerous half nobody mentions

A killed apply is a **partial apply**. Terraform writes state incrementally, so
usually the created resources *are* recorded — but resources created in the
final, unflushed moments may not be. After unlocking, always assume orphans:

```bash
terraform plan       # "1 to add" for something that already exists = orphan
terraform import aws_nat_gateway.this nat-0abc   # adopt it
```

## Verify

```bash
terraform plan     # runs to completion, no lock error, No changes
```

## Prevention

* Set CI job timeouts *longer* than your slowest apply, and trap SIGTERM to let
  Terraform finish or unlock cleanly.
* Use TFE/HCP: its run queue serialises applies per workspace by design.
* DynamoDB lock table with PITR so you can audit lock history.
* Alert on locks older than N minutes rather than letting engineers guess.

## Discussion questions

1. Why is `force-unlock` more dangerous than `state push -force` in some ways?
2. Design a "is the lock holder alive?" check for GitHub Actions runners.
3. TF 1.10 added S3-native locking. What does that remove, and what breaks if
   half your fleet still uses the DynamoDB table?
