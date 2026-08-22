#!/usr/bin/env bash
# Smoke-test every challenge's simulator commands. Run: ./verify.sh
set -uo pipefail
SL="python3 $(cd "$(dirname "$0")" && pwd)/lib/statelab.py"
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
pass=0; fail=0
ok(){ echo "  PASS $1"; pass=$((pass+1)); }
no(){ echo "  FAIL $1"; fail=$((fail+1)); }

t(){ # t <name> <expect-ok|expect-fail> <cmd...>
  local n=$1 e=$2; shift 2
  if "$@" >/dev/null 2>&1; then [ "$e" = ok ] && ok "$n" || no "$n (expected failure)"
  else [ "$e" = fail ] && ok "$n" || no "$n (expected success)"; fi
}

echo "01/03 serial rules"
cd $W && mkdir -p c1 && cd c1
$SL new remote.tfstate --lineage L --serial 1 >/dev/null
$SL add-res remote.tfstate --type aws_vpc --name main --id vpc-1 >/dev/null
$SL add-res remote.tfstate --type aws_subnet --name a --id sub-a >/dev/null
mkdir -p .history && cp remote.tfstate .history/remote.tfstate.000003.json
$SL new stale.tfstate --lineage L --serial 2 >/dev/null
t "lower serial rejected"  fail $SL push stale.tfstate --to remote.tfstate
t "force accepted"         ok   $SL push stale.tfstate --to remote.tfstate --force
t "rollback"               ok   $SL rollback remote.tfstate --to-serial 3
[ "$(jq '.resources|length' remote.tfstate)" = 2 ] && ok "content restored" || no "content restored"
[ "$(jq .serial remote.tfstate)" -gt 3 ] && ok "serial moved forward" || no "serial moved forward"

echo "04 lineage"
cd $W && mkdir -p c4 && cd c4
$SL new r.tfstate --lineage A --serial 5 >/dev/null
$SL new o.tfstate --lineage B --serial 9 >/dev/null
t "lineage mismatch rejected" fail $SL push o.tfstate --to r.tfstate
$SL set o.tfstate --lineage A --serial 6 >/dev/null
t "adopted lineage accepted"  ok   $SL push o.tfstate --to r.tfstate

echo "06 corruption"
cd $W && mkdir -p c6 && cd c6
$SL new s.tfstate --lineage C --serial 1 >/dev/null
$SL add-res s.tfstate --type aws_vpc --name m --id v-1 >/dev/null
$SL push s.tfstate --to remote.tfstate >/dev/null
$SL corrupt remote.tfstate --mode truncate >/dev/null
t "corrupt state unparseable" fail jq -e . remote.tfstate
t "restore from history"      ok   $SL rollback remote.tfstate --to-serial 2
t "valid after restore"       ok   jq -e . remote.tfstate

echo "09/10/13 mv + rm"
cd $W && mkdir -p c9 && cd c9
$SL new m.tfstate --lineage D --serial 1 >/dev/null
$SL add-res m.tfstate --type aws_instance --name web --id i-keep >/dev/null
$SL mv-res m.tfstate --from aws_instance.web --to aws_instance.api >/dev/null
[ "$(jq -r '.resources[0].name' m.tfstate)" = api ] && ok "address renamed" || no "address renamed"
[ "$(jq -r '.resources[0].instances[0].attributes.id' m.tfstate)" = i-keep ] \
  && ok "cloud id preserved" || no "cloud id preserved"
t "rm-res"          ok   $SL rm-res m.tfstate --addr aws_instance.api
t "rm missing fails" fail $SL rm-res m.tfstate --addr aws_instance.nope

echo "11 secrets"
cd $W && mkdir -p c11 && cd c11
$SL new s.tfstate --lineage E --serial 1 >/dev/null
$SL add-res s.tfstate --type aws_db_instance --name pg --id db --attrs '{"password":"Sup3rSecret!"}' >/dev/null
grep -q 'Sup3rSecret!' s.tfstate && ok "secret is plaintext in state" || no "secret is plaintext in state"

echo "12 versions"
cd $W && mkdir -p c12 && cd c12
$SL new s.tfstate --lineage F --serial 1 >/dev/null
$SL set s.tfstate --tf-version 1.13.0 >/dev/null
[ "$(jq -r .terraform_version s.tfstate)" = 1.13.0 ] && ok "tf version set" || no "tf version set"

echo
echo "passed=$pass failed=$fail"
[ $fail -eq 0 ]
