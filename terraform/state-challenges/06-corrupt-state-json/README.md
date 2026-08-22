# Challenge 06 — Corrupt / truncated state uploaded to the backend

**Difficulty:** ★★★★☆ · **Time:** 25 min

## Scenario

A pipeline uploaded state through a flaky proxy and the object was truncated at
60%. Now every command fails before it starts:

```
Error: Failed to load state: unexpected end of JSON input
```

or

```
Error: Invalid JSON in state file ... invalid character ',' looking for beginning of object key
```

You cannot plan, cannot apply, cannot even `state list`.

## Key concept

State is a **single JSON document**. There is no partial-read recovery, no
per-resource framing. Corruption is all-or-nothing, which is exactly why every
serious backend layers *versioning* underneath:

* S3 → bucket versioning (`aws s3api list-object-versions`)
* TFE/HCP → immutable state-version history
* local → `terraform.tfstate.backup` (previous serial only — one deep!)
* gcs → object generations
* azurerm → blob snapshots

**Recovery order of preference:** backend version history → your own external
backup → hand-repair the JSON → rebuild by import (challenge 07). Hand-repair is
third, not first.

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c06 && mkdir -p /tmp/c06 && cd /tmp/c06

$SL new work.tfstate --lineage corrupt-demo --serial 1
$SL add-res work.tfstate --type aws_vpc      --name main --id vpc-1
$SL add-res work.tfstate --type aws_instance --name web  --id i-1
$SL add-res work.tfstate --type aws_db_instance --name db --id db-1
$SL push work.tfstate --to remote.tfstate       # healthy version archived

cp remote.tfstate terraform.tfstate.backup      # local backend also keeps this
$SL corrupt remote.tfstate --mode truncate

jq . remote.tfstate ; echo "jq exit=$?"         # parse failure
```

Try the other modes too: `--mode badjson` (syntax), `--mode nulls` (parses fine,
but Terraform rejects the schema — the nastiest kind, because it looks valid).

## Fix it

**Route 1 — restore from backend history (do this first):**

```bash
$SL versions remote.tfstate
$SL rollback remote.tfstate --to-serial 4
jq -e . remote.tfstate >/dev/null && echo "valid JSON"
```

S3 equivalent:
```bash
aws s3api list-object-versions --bucket my-bucket --prefix prod/terraform.tfstate \
  | jq -r '.Versions[] | [.LastModified,.VersionId,.Size] | @tsv' | sort
aws s3api get-object --bucket my-bucket --key prod/terraform.tfstate \
  --version-id <GOOD_VERSION_ID> good.tfstate
jq -e . good.tfstate && terraform state push good.tfstate
```

TFE: download the previous state version and push it forward (challenge 02).

**Route 2 — local backup file:**
```bash
cp terraform.tfstate.backup terraform.tfstate   # only ONE serial back
```

**Route 3 — hand-repair (last resort, when no backup exists):**

```bash
python3 - <<'PY'
import json
raw = open('remote.tfstate').read()
# salvage the header
hdr = {}
for k in ("version","terraform_version","serial","lineage"):
    i = raw.find(f'"{k}"')
    print(k, raw[i:i+60].splitlines()[0] if i>=0 else "MISSING")
PY
```
Rebuild a minimal valid skeleton, then **re-import every resource** you can
identify (challenge 07). Validate before pushing:
```bash
jq -e '.version and .serial and .lineage and (.resources|type=="array")' fixed.tfstate
terraform state push -force fixed.tfstate     # -force justified: remote is garbage
terraform plan
```

## Verify

```bash
jq -e . <state>            # parses
terraform state list       # lists every expected address
terraform plan             # No changes
```

## Prevention

* Turn on **S3 versioning + MFA delete**, or use TFE. Non-negotiable for prod.
* Never `aws s3 cp` state by hand; let the backend write it.
* Nightly external export with checksum (challenge 15).
* `terraform state pull | jq -e .` in CI as a cheap corruption canary.

## Discussion questions

1. Why does Terraform keep only one `.tfstate.backup` locally?
2. `--mode nulls` produces valid JSON that Terraform still rejects. What is the
   difference between JSON validity and *state schema* validity?
3. State is 400 MB and truncation happens often. What does that tell you about
   how the root module is structured, and how would you fix the root cause?
