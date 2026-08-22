# Terraform State & Terraform Enterprise — 15 Hands-On Challenges

A lab for the questions that actually break production:

> *"Someone uploaded a state file with the wrong serial number to Terraform Enterprise.
> How do I restore it?"*

Every challenge is **runnable locally** — no AWS account, no TFE licence.
A small simulator (`lib/statelab.py`) reproduces the exact rules the real
Terraform CLI and the TFE state-versions API enforce.

---

## 1. First: what is a state file, really?

`terraform.tfstate` is JSON with a tiny header and a big `resources` array:

```json
{
  "version": 4,                 <- STATE FORMAT version (not Terraform's)
  "terraform_version": "1.9.5", <- CLI that last wrote it
  "serial": 42,                 <- increments on EVERY write. Optimistic lock.
  "lineage": "8f2c...-...",     <- UUID identifying this state's "bloodline"
  "outputs": {},
  "resources": [ ... ]          <- real-world IDs mapped to config addresses
}
```

Four rules that explain 90% of state incidents:

| Field | Rule | What goes wrong |
|---|---|---|
| `serial` | Must be **strictly greater** than the remote serial on write | Push a lower/equal serial → rejected, or **forced** → silent data loss |
| `lineage` | Must **match** the remote lineage | Two `terraform init`s made two bloodlines; forcing one over the other orphans real infra |
| `version` | Format version; newer Terraform can upgrade, never downgrade | "state snapshot was created by a newer version" |
| `resources` | The only link between config and real infra | Lose it → Terraform thinks nothing exists → next apply **recreates everything** |

> **The #1 mental model:** state is an *append-only log of snapshots*.
> You never "go back". A restore is a **forward write of older content**
> under a **new, higher serial**. TFE calls each write a *state version*.

---

## 2. Setup (30 seconds)

```bash
cd terraform/state-challenges
export SL="python3 $PWD/lib/statelab.py"
mkdir -p /tmp/lab && cd /tmp/lab

$SL new local.tfstate --lineage demo-lineage --serial 1
$SL add-res local.tfstate --type aws_instance --name web --id i-0abc
$SL push    local.tfstate --to remote.tfstate     # "remote" = the TFE backend
$SL show    local.tfstate
$SL versions remote.tfstate                       # state-version history
```

`statelab.py` commands: `new show bump set add-res rm-res mv-res corrupt push versions rollback`.
`push` enforces the real serial/lineage checks; `versions`/`rollback` emulate TFE's
state-version history (snapshots land in `.history/`).

---

## 3. The 15 challenges

| # | Folder | Scenario |
|---|---|---|
| 1 | [`01-tfe-incorrect-serial-upload`](01-tfe-incorrect-serial-upload/) | **A stale/incorrect serial was uploaded to TFE — restore the good state** |
| 2 | [`02-tfe-rollback-state-version`](02-tfe-rollback-state-version/) | Roll back to a previous TFE state version after a bad apply |
| 3 | [`03-state-push-serial-mismatch`](03-state-push-serial-mismatch/) | `cannot import state with serial N over state with serial M` |
| 4 | [`04-lineage-mismatch`](04-lineage-mismatch/) | `Invalid state lineage` after a backend re-init |
| 5 | [`05-stuck-state-lock`](05-stuck-state-lock/) | Killed apply left the workspace locked |
| 6 | [`06-corrupt-state-json`](06-corrupt-state-json/) | Truncated / malformed state uploaded to the backend |
| 7 | [`07-rebuild-state-after-destroy`](07-rebuild-state-after-destroy/) | State gone, infra alive — rebuild by import |
| 8 | [`08-backend-migration`](08-backend-migration/) | local → S3 → TFE migration with zero drift |
| 9 | [`09-split-state`](09-split-state/) | Split a monolith state into per-team workspaces |
| 10 | [`10-merge-state`](10-merge-state/) | Merge two workspaces into one |
| 11 | [`11-secrets-in-state`](11-secrets-in-state/) | Plaintext secrets in state pushed to TFE/Git |
| 12 | [`12-version-schema-mismatch`](12-version-schema-mismatch/) | State written by a newer Terraform / provider |
| 13 | [`13-refactor-moved-blocks`](13-refactor-moved-blocks/) | Rename refactor wants to destroy+recreate |
| 14 | [`14-out-of-band-drift`](14-out-of-band-drift/) | Resource deleted in the console, still in state |
| 15 | [`15-tfe-disaster-recovery`](15-tfe-disaster-recovery/) | TFE workspace/state deleted — full DR runbook |

Each folder has a `README.md` (scenario + **Break it** + **Fix it** + **Verify** + concept notes).
Work them in order — 1–4 are the serial/lineage core, the rest build on it.

---

## 4. Golden rules cheat-sheet

1. **Always `terraform state pull > backup-$(date +%s).json` before anything.**
2. Never edit remote state in place. Pull → edit → push with a higher serial.
3. `terraform state push -force` and `terraform force-unlock` are **break-glass**:
   they bypass the safety checks that exist to stop you losing infra.
4. Restoring = writing old content forward as a *new* version. History is never rewritten.
5. After any state surgery, the acceptance test is always the same:
   **`terraform plan` must show `No changes`.** If it wants to create things you
   already own, your state is still wrong — stop, do not apply.
6. State contains secrets in plaintext. Treat every backup as a secret.

---

## 5. Self-check

`./verify.sh` runs every simulator command used across the challenges and asserts
the expected accept/reject behaviour (16 checks). Run it after editing `lib/statelab.py`.
