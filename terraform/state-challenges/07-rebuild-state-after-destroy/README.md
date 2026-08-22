# Challenge 07 — State is gone, infrastructure is alive: rebuild by import

**Difficulty:** ★★★★★ · **Time:** 45 min

## Scenario

The state object was deleted from the bucket (no versioning — see challenge 06)
and there is no backup. The VPC, 4 subnets, an ALB, an ASG and an RDS instance
are all **running and serving traffic**. Terraform now believes nothing exists:

```
Plan: 12 to add, 0 to change, 0 to destroy.
```

Applying that would fail on name conflicts at best, and at worst build a
duplicate stack next to production.

## Key concept

State is a **map**, not a backup. Everything in it can, in principle, be
rediscovered from the cloud provider — that is exactly what `import` does:

```
terraform import <config address>  <provider-specific ID>
        │                              │
        └ must already exist in .tf    └ format differs per resource type!
```

Import writes the resource into state **without touching the cloud**. It is the
only Terraform operation that is guaranteed read-only against infrastructure.

Since 1.5 you can do it declaratively, which makes it reviewable:

```hcl
import {
  to = aws_vpc.main
  id = "vpc-0abc123"
}
```
and since 1.5 also generate the config you're missing:
```bash
terraform plan -generate-config-out=generated.tf
```

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c07 && mkdir -p /tmp/c07 && cd /tmp/c07

# pretend "the cloud" is this inventory file
cat > cloud-inventory.txt <<'EOF'
aws_vpc.main         vpc-0abc123
aws_subnet.a         subnet-0a1
aws_subnet.b         subnet-0b2
aws_lb.front         arn:aws:elasticloadbalancing:eu-west-1:111:loadbalancer/app/front/abc
aws_db_instance.main prod-db
EOF

$SL new terraform.tfstate --lineage rebuilt --serial 1
$SL show terraform.tfstate        # empty: 0 resources. This is the disaster.
```

## Fix it

**Step 0 — freeze.** Disable auto-apply, lock the workspace, tell the team. An
accidental apply right now is the actual outage.

**Step 1 — inventory reality.** Tag-driven discovery beats memory:

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Environment,Values=prod \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n'
```

**Step 2 — import, dependency order first** (VPC → subnets → SGs → LB → ASG → RDS):

```bash
while read -r addr id; do
  echo "importing $addr <- $id"
  terraform import "$addr" "$id"      # real CLI
done < cloud-inventory.txt
```

Simulator version (shows state filling up):
```bash
while read -r addr id; do
  t="${addr%%.*}"; n="${addr##*.}"
  $SL add-res terraform.tfstate --type "$t" --name "$n" --id "$id"
done < cloud-inventory.txt
$SL show terraform.tfstate
```

**Step 2-alt — declarative, reviewable in a PR:**
```hcl
# imports.tf
import { to = aws_vpc.main    id = "vpc-0abc123" }
import { to = aws_subnet.a    id = "subnet-0a1"  }
import { to = aws_lb.front    id = "arn:aws:...:loadbalancer/app/front/abc" }
```
```bash
terraform plan     # shows "N to import, 0 to add, 0 to change, 0 to destroy"
terraform apply    # performs the imports
```

**Step 3 — close the gap between imported attributes and config.** Import fills
state from the API; your `.tf` may disagree (missing tags, default values,
different `instance_type`). Iterate:

```bash
terraform plan            # every "~ update in place" is a config/reality mismatch
# fix the .tf to MATCH REALITY first; make intentional changes in a separate PR
```

## Verify

```bash
terraform state list | wc -l      # == number of real resources
terraform plan                    # No changes  <-- the only acceptance test
```

Do **not** accept "only a few in-place updates left". Each one is a change you'd
be applying to production blind.

## Gotchas

* Import IDs are **not** uniform: `aws_instance` uses `i-…`, `aws_lb` an ARN,
  `aws_iam_role_policy_attachment` a composite `role/policyArn`, `aws_route53_record`
  `ZONEID_name_TYPE`. Check each resource's docs "Import" section.
* `count`/`for_each` resources import per index: `terraform import 'aws_subnet.this["a"]' subnet-0a1`.
* Modules: `terraform import module.net.aws_vpc.main vpc-0abc123`.
* Some resources are **not importable** (random_*, tls_private_key, null_resource).
  Those must be re-created or stubbed — and if a `random_password` feeds an RDS
  master password, you have a rotation task, not an import task.
* Data sources are never imported; they resolve at plan time.

## Discussion questions

1. Why is `plan -generate-config-out` still not a full answer for a 200-resource stack?
2. How would `for_each` over a data source have made this recoverable in minutes?
3. What is the recovery cost difference between this challenge and challenge 15?
   What does that tell you about where to spend engineering effort?
