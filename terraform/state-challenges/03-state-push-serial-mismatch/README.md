# Challenge 03 — `cannot import state with serial N over state with serial M`

**Difficulty:** ★★☆☆☆ · **Time:** 15 min

## Scenario

You pulled state this morning to do a surgical `terraform state rm`. While you
were working, a colleague's pipeline applied twice. Your push now fails:

```
Error: cannot import state with serial 12 over state with serial 14;
use the -force flag to disable this check
```

## Key concept

This is a **lost-update conflict**, identical to a git non-fast-forward push.
Your edit was made against an old base. `-force` is `git push --force`: it
throws away their two applies. Almost never the right answer.

The right answer is **rebase**: pull the current state, re-apply your *edit*
(not your file) on top, push with a serial above theirs.

```
        their applies
serial 12 ──► 13 ──► 14 (remote)
   │
   └── your edited copy (12')      X  cannot push
   
   fix: pull 14 ──► redo edit ──► 15 ──► push  ✔
```

## Break it

```bash
export SL="python3 /home/user/Terraform-Project/terraform/state-challenges/lib/statelab.py"
rm -rf /tmp/c03 && mkdir -p /tmp/c03 && cd /tmp/c03

$SL new remote.tfstate --lineage team-lineage --serial 10
$SL add-res remote.tfstate --type aws_instance --name web  --id i-web
$SL add-res remote.tfstate --type aws_instance --name api  --id i-api

cp remote.tfstate mine.tfstate            # you "terraform state pull"

# colleague applies twice
$SL add-res remote.tfstate --type aws_lb    --name front --id lb-1
$SL add-res remote.tfstate --type aws_route53_record --name www --id r-1

# your surgical edit, then push
$SL rm-res mine.tfstate --addr aws_instance.api
$SL push mine.tfstate --to remote.tfstate ; echo "exit=$?"
```

## Fix it — the safe rebase

```bash
cp remote.tfstate rescue-backup.json          # ALWAYS

cp remote.tfstate fresh.tfstate               # 1. re-pull
$SL rm-res fresh.tfstate --addr aws_instance.api   # 2. redo the EDIT (auto-bumps serial)
$SL push fresh.tfstate --to remote.tfstate    # 3. accepted
$SL show remote.tfstate                       # lb + route53 survived, api gone
```

Real CLI:

```bash
terraform state pull > fresh.tfstate
# redo your edit against fresh.tfstate, or better: use the CLI verbs which
# handle serial for you and never require a manual push at all:
terraform state rm aws_instance.api
```

> **Best practice:** prefer `terraform state rm|mv|import` over
> `pull → hand-edit → push`. The CLI verbs lock the backend, apply the change,
> bump the serial and write atomically. Manual push is for the cases the verbs
> can't express (bulk surgery, format repair).

## When `-force` IS legitimate

* Restoring a known-good snapshot during an incident, workspace locked, after
  you have backed up the current state (challenge 01).
* Repairing a corrupt state where the remote serial is meaningless (challenge 06).
* Never: "the error was annoying so I forced it."

## Verify

```bash
$SL show remote.tfstate     # 3 resources: web, front, www
terraform plan              # No changes
```

## Discussion questions

1. Why does Terraform use a serial instead of comparing content hashes?
2. Two engineers push serial 15 simultaneously with different content. What
   stops one from silently winning? (Hint: backend locking, not the serial.)
3. What does `terraform state rm` do to the serial, and why by exactly that much?
