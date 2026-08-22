# Challenge 11 — Plaintext secrets in state, pushed to TFE and committed to Git

**Difficulty:** ★★★☆☆ · **Time:** 25 min

## Scenario

Someone committed `terraform.tfstate` to the repo "as a backup". It contains the
RDS master password, a `random_password` result, and a service-account private
key — all in plaintext. The same state also lives in a TFE workspace that 40
people can read.

## Key concept

**Terraform state always stores attribute values in plaintext.** `sensitive = true`
only redacts *CLI output*; it changes nothing on disk:

```json
{ "type": "aws_db_instance", "instances": [
    { "attributes": { "password": "Sup3rSecret!", "id": "prod-db" } } ] }
```

There is no built-in state encryption in Terraform OSS (HCP Terraform/TFE encrypt
at rest and gate access via RBAC; OpenTofu has client-side state encryption).
Therefore the controls are: **don't put secrets in state, and treat state as a
secret when you must.**

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c11 && mkdir -p /tmp/c11 && cd /tmp/c11

$SL new terraform.tfstate --lineage secrets-demo --serial 3
$SL add-res terraform.tfstate --type aws_db_instance --name pg --id prod-db \
   --attrs '{"username":"admin","password":"Sup3rSecret!"}'

# find them the way an attacker would
grep -o '"password": *"[^"]*"' terraform.tfstate
jq -r '.resources[].instances[].attributes | to_entries[]
       | select(.key|test("pass|secret|token|key|private"))
       | "\(.key) = \(.value)"' terraform.tfstate
```

## Fix it — in this order

**1. Rotate. Immediately.** Scrubbing files does not un-leak a credential.
Assume compromise: rotate the DB password, revoke the key, invalidate tokens.
Everything below is cleanup, not remediation.

**2. Purge from Git history** (the file being deleted in HEAD is not enough):
```bash
git rm --cached terraform.tfstate && echo -e '*.tfstate\n*.tfstate.*\n.terraform/' >> .gitignore
git commit -m "remove state from vcs"
# then rewrite history:
git filter-repo --path terraform.tfstate --invert-paths     # preferred
# force-push, invalidate forks, and tell GitHub support to purge cached views
```

**3. Reduce exposure in TFE:** restrict workspace `read` (state access is a
distinct permission in TFE: *Read state versions*), enable audit logging, and
prune old state versions if your retention policy allows.

**4. Stop it recurring — design the secret out of state:**

```hcl
# a) Write-only / ephemeral arguments (TF 1.11+) - value never persists to state
resource "aws_db_instance" "pg" {
  password_wo         = var.db_password    # write-only argument
  password_wo_version = 1
}

# b) Let the provider manage the secret out of band
resource "aws_db_instance" "pg" {
  manage_master_user_password = true       # AWS stores it in Secrets Manager
}

# c) Reference, don't materialise
data "aws_secretsmanager_secret_version" "db" { secret_id = "prod/db" }
# NOTE: this STILL lands in state. Prefer (a)/(b) - a data source is not a fix.
```

**5. Encrypt the substrate:** S3 SSE-KMS with a restrictive key policy, bucket
policy denying `s3:GetObject` outside the pipeline role, versioning + object lock.

## Verify

```bash
terraform state pull | jq -r '..|strings' | grep -Ei 'BEGIN (RSA|PRIVATE)|Sup3rSecret' && echo LEAK || echo clean
git log --all --full-history -- '*.tfstate'    # must be empty after rewrite
```

Add a CI gate:
```bash
terraform state pull > /tmp/s.json
gitleaks detect --no-git --source /tmp/s.json   # or trufflehog
```

## Gotchas

* **Plan files** (`tfplan`) contain the same secrets — TFE stores them per run.
* `terraform output -json` and remote-state data sources re-expose everything.
* `random_password` values live in state forever; rotating means tainting it,
  which usually means downtime. Prefer provider-managed secrets.
* Backups you made during challenges 01–06 are also secret material. Encrypt and
  expire them.

## Discussion questions

1. Why is `sensitive = true` a UX feature and not a security control?
2. Write-only arguments solve the *new* write. What about the 4 years of history
   already in TFE?
3. Who in your org can read prod state today? Is that the same list as who can
   read the production database? Should it be?
