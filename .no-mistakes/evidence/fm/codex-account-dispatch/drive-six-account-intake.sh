#!/usr/bin/env bash
# Manual drive of the script-side per-account quota read the dispatch intake
# uses to rank the captain's six ChatGPT accounts (bin/fm-quota-axi-lib.sh's
# fm_quota_axi_read_codex_home). Real library, real subprocesses, fake quota-axi.
set -u
ROOT_REPO=${1:?repo root}
LAB=$(mktemp -d "${TMPDIR:-/tmp}/six-account-intake.XXXXXX")
FAKEBIN="$LAB/fakebin"; mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = --version ] && { printf 'quota-axi 0.1.29\n'; exit 0; }
case "${CODEX_HOME-}" in
  *.codex-1) pct=12 ;; *.codex-2) pct=71 ;; *.codex-3) pct=4 ;;
  *.codex-4) pct=96 ;; *.codex-5) pct=38 ;; *) pct=55 ;;
esac
printf 'provider,percentRemaining,codexHome\ncodex,%s,%s\n' "$pct" "${CODEX_HOME:-<ambient ~/.codex>}"
SH
chmod +x "$FAKEBIN/quota-axi"
export PATH="$FAKEBIN:$PATH" HOME="$LAB/user"
mkdir -p "$HOME"
for a in .codex .codex-1 .codex-2 .codex-3 .codex-4 .codex-5; do
  mkdir -p "$HOME/$a"; printf '{"placeholder":"fixture"}\n' > "$HOME/$a/auth.json"
done
mkdir -p "$HOME/.codex-signed-out"   # a sixth-account directory never signed in

# shellcheck source=/dev/null
. "$ROOT_REPO/bin/fm-timeout-lib.sh"
# shellcheck source=/dev/null
. "$ROOT_REPO/bin/fm-quota-axi-lib.sh"

printf '\n================ intake: one quota read per Codex account ================\n'
for a in .codex .codex-1 .codex-2 .codex-3 .codex-4 .codex-5; do
  printf '%-12s -> %s\n' "~/$a" "$(fm_quota_axi_read_codex_home "~/$a" | tail -1)"
done

printf '\n================ ADVERSARIAL: an account that was never signed in ================\n'
printf '$ fm_quota_axi_read_codex_home ~/.codex-signed-out\n'
fm_quota_axi_read_codex_home '~/.codex-signed-out'; printf 'exit=%s (no reading returned, so it cannot be ranked as if healthy)\n' "$?"

printf '\n================ ADVERSARIAL: a relative account spelling ================\n'
printf '$ fm_quota_axi_read_codex_home accounts/codex-1\n'
fm_quota_axi_read_codex_home 'accounts/codex-1'; printf 'exit=%s\n' "$?"

printf '\n================ a bounded read: hung quota-axi ends at the timeout ================\n'
cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = --version ] && { printf 'quota-axi 0.1.29\n'; exit 0; }
sleep 60
SH
chmod +x "$FAKEBIN/quota-axi"
printf '$ fm_quota_axi_read_codex_home --timeout 2 ~/.codex-4\n'
time fm_quota_axi_read_codex_home --timeout 2 '~/.codex-4'; printf 'exit=%s (124 = timed out, not a silent healthy reading)\n' "$?"
rm -rf "$LAB"
