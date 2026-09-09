#!/usr/bin/env bash
# Manual end-to-end drive of the Codex ACCOUNT axis on the quota watch.
# Runs the REAL bin/fm-procevent-quota.sh against an isolated firstmate home
# with a fake quota-axi that reports a DIFFERENT remaining quota per
# CODEX_HOME, so the transcript shows which account the watch actually read.
set -u
ROOT_REPO=${1:?repo root}
BIN="$ROOT_REPO/bin"
LAB=$(mktemp -d "${TMPDIR:-/tmp}/codex-account-quota.XXXXXX")
FAKEBIN="$LAB/fakebin"; mkdir -p "$FAKEBIN"
ARGV_LOG="$LAB/quota-axi.argv"

# Fake quota-axi: the captain's fleet, one remaining-% per account home.
cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then printf 'quota-axi 0.1.29\n'; exit 0; fi
printf 'quota-axi invoked with CODEX_HOME=%s argv=%s\n' "${CODEX_HOME-<unset>}" "$*" >> "${QUOTA_AXI_ARGV_LOG:-/dev/null}"
case "${CODEX_HOME-}" in
  *.codex-3) pct=4  ;;   # the account this worker was dispatched onto: nearly out
  *.codex-1) pct=88 ;;
  "")        pct=92 ;;   # the AMBIENT default ~/.codex account: healthy
  *)         pct=50 ;;
esac
printf '{"schemaVersion":5,"providers":[{"provider":"codex","quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":%s,"runway":{"status":"through_reset"}}]}}]}\n' "$pct"
SH
chmod +x "$FAKEBIN/quota-axi"

mk_acct() { mkdir -p "$1"; printf '{"placeholder":"fixture-not-a-real-credential"}\n' > "$1/auth.json"; }
A3="$LAB/accounts/.codex-3"; A1="$LAB/accounts/.codex-1"; OUT="$LAB/accounts/.codex-signed-out"
mk_acct "$A3"; mk_acct "$A1"; mkdir -p "$OUT"
FMH="$LAB/home"; mkdir -p "$FMH"; mkdir -m 700 "$FMH/state"
export FM_HOME="$FMH" FM_PROCEVENT_CLAIM_ROOT="$LAB/claims" QUOTA_AXI_ARGV_LOG="$ARGV_LOG"
export PATH="$FAKEBIN:$PATH"

hr() { printf '\n================ %s ================\n' "$1"; }

hr "fleet: ambient ~/.codex = 92%% remaining, .codex-1 = 88%%, .codex-3 = 4%%"

hr "1. watch on the worker's OWN account (.codex-3, nearly out)"
: > "$ARGV_LOG"
printf '$ fm-procevent-quota.sh poll --interval 0.01 --threshold 10 --provider codex --codex-home %s --timeout 5\n' "$A3"
"$BIN/fm-procevent-quota.sh" poll --interval 0.01 --threshold 10 --provider codex --codex-home "$A3" --timeout 5
printf -- '--- what quota-axi was actually asked ---\n'; cat "$ARGV_LOG"

hr "2. the SAME moment without --codex-home reads the ambient account and stays healthy"
: > "$ARGV_LOG"
printf '$ timeout 3 fm-procevent-quota.sh poll --interval 0.5 --threshold 10 --provider codex --timeout 5\n'
timeout 3 "$BIN/fm-procevent-quota.sh" poll --interval 0.5 --threshold 10 --provider codex --timeout 5
printf 'exit=%s (124 = still polling: the ambient account never crossed the threshold)\n' "$?"
printf -- '--- what quota-axi was actually asked ---\n'; head -2 "$ARGV_LOG"

hr "3. arm a per-account watch, list the registration, then retire it"
printf '$ fm-procevent-quota.sh arm --interval 30 --threshold 15 --provider codex --codex-home %s\n' "$A3"
"$BIN/fm-procevent-quota.sh" arm --interval 30 --threshold 15 --provider codex --codex-home "$A3"
SID=$("$BIN/fm-procevent-quota.sh" source-id --provider codex --codex-home "$A3")
printf -- '--- registered poll command for %s ---\n' "$SID"
cat "$FMH/state/procevent/$SID.source"
printf '\n$ fm-procevent-quota.sh arm --interval 30 --threshold 15 --provider codex --codex-home %s\n' "$A1"
"$BIN/fm-procevent-quota.sh" arm --interval 30 --threshold 15 --provider codex --codex-home "$A1"
printf -- '--- one watch per account now registered ---\n'
ls -1 "$FMH/state/procevent"/*.source

hr "4. an account reached through an alias is ONE watch, and retire still finds it after the alias is deleted"
ln -s "$A3" "$LAB/accounts/codex-alias"
printf '$ source-id --provider codex --codex-home <alias>  -> %s\n' \
  "$("$BIN/fm-procevent-quota.sh" source-id --provider codex --codex-home "$LAB/accounts/codex-alias")"
printf '  (the physical account id is                      %s)\n' "$SID"
printf '$ arm --interval 30 --threshold 15 --provider codex --codex-home <alias>\n'
"$BIN/fm-procevent-quota.sh" arm --interval 30 --threshold 15 --provider codex --codex-home "$LAB/accounts/codex-alias"
printf -- '--- still one watch per account (no duplicate for the alias) ---\n'
ls -1 "$FMH/state/procevent"/*.source
rm -f "$LAB/accounts/codex-alias"
printf '$ retire --provider codex --codex-home <the now-deleted alias>\n'
"$BIN/fm-procevent-quota.sh" retire --provider codex --codex-home "$LAB/accounts/codex-alias"
printf -- '--- registrations left ---\n'; ls -1 "$FMH/state/procevent"/*.source

hr "5. ADVERSARIAL: the account is signed out mid-watch"
: > "$ARGV_LOG"
printf '$ fm-procevent-quota.sh poll --interval 0.01 --threshold 10 --provider codex --codex-home %s --timeout 5\n' "$OUT"
"$BIN/fm-procevent-quota.sh" poll --interval 0.01 --threshold 10 --provider codex --codex-home "$OUT" --timeout 5
printf -- '--- did it fall back to the ambient (healthy) account? ---\n'
[ -s "$ARGV_LOG" ] && { echo "FELL BACK:"; cat "$ARGV_LOG"; } || echo "quota-axi was never reached; no ambient reading was reported"

hr "6. ADVERSARIAL: --codex-home on a non-codex watch"
printf '$ fm-procevent-quota.sh arm --provider claude --codex-home %s\n' "$A3"
"$BIN/fm-procevent-quota.sh" arm --provider claude --codex-home "$A3"; printf 'exit=%s\n' "$?"
printf '$ fm-procevent-quota.sh arm --codex-home %s   (aggregate watch)\n' "$A3"
"$BIN/fm-procevent-quota.sh" arm --codex-home "$A3"; printf 'exit=%s\n' "$?"

hr "7. ADVERSARIAL: arm on an account that was never signed in"
printf '$ fm-procevent-quota.sh arm --provider codex --codex-home %s\n' "$OUT"
"$BIN/fm-procevent-quota.sh" arm --provider codex --codex-home "$OUT"; printf 'exit=%s\n' "$?"
printf -- '--- registrations after the refusal ---\n'; ls -1 "$FMH/state/procevent"/*.source 2>/dev/null || echo "(none)"

hr "8. ADVERSARIAL: no shasum/sha256sum on PATH (the source id must not silently drift)"
NOSHA="$LAB/nosha"; mkdir -p "$NOSHA"
IFS=: read -ra dirs <<< "$PATH"
for d in "${dirs[@]}"; do [ -d "$d" ] || continue; for e in "$d"/*; do n=${e##*/}
  case "$n" in shasum|sha256sum) continue ;; esac
  [ -e "$NOSHA/$n" ] || ln -s "$e" "$NOSHA/$n" 2>/dev/null || true; done; done
printf '$ PATH=<no shasum, no sha256sum> fm-procevent-quota.sh source-id --provider codex --codex-home %s\n' "$A3"
PATH="$NOSHA" "$BIN/fm-procevent-quota.sh" source-id --provider codex --codex-home "$A3"; printf 'exit=%s\n' "$?"

hr "done"
rm -rf "$LAB"
