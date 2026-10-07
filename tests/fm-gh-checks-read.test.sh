#!/usr/bin/env bash
# Behavior of optional GitHub App checks authentication through its public CLI.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP_ROOT=$(fm_test_tmproot fm-gh-checks-read-tests)
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
mkdir -p "$TMP_ROOT/home/config"
export FM_HOME="$TMP_ROOT/home" FM_TEST_CHECKS_ROOT="$TMP_ROOT"
export PATH="$FAKEBIN:$PATH" GH_TOKEN=ordinary_user_token
SCRIPT="$ROOT/bin/fm-gh-checks-read.sh"
KEY="$TMP_ROOT/key.pem"
openssl genrsa -out "$KEY" 2048 2>/dev/null
chmod 600 "$KEY"
openssl rsa -in "$KEY" -pubout -out "$TMP_ROOT/public.pem" 2>/dev/null
cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
set -eu
# Validate the secret-bearing header without logging it or putting it on argv.
[ "$1" = -q ] && [ "$2" = --config ] && [ "$3" = - ] || exit 80
[ -z "${TOKEN:-}${JWT:-}${RESPONSE:-}${SIGNATURE:-}" ] || exit 87
IFS= read -r header
jwt=${header#*Bearer }
jwt=${jwt%\"}
IFS=. read -r h p s <<< "$jwt"
# Prove the actual RS256 signature, issuer, and bounded JWT times.
printf '%s.%s' "$h" "$p" > "$FM_TEST_CHECKS_ROOT/signed"
printf '%s' "$s" | tr '_-' '/+' > "$FM_TEST_CHECKS_ROOT/signature.b64"
while [ "$(( $(wc -c < "$FM_TEST_CHECKS_ROOT/signature.b64") % 4 ))" -ne 0 ]; do
  printf '=' >> "$FM_TEST_CHECKS_ROOT/signature.b64"
done
openssl base64 -d -A -in "$FM_TEST_CHECKS_ROOT/signature.b64" -out "$FM_TEST_CHECKS_ROOT/signature"
openssl dgst -sha256 -verify "$FM_TEST_CHECKS_ROOT/public.pem" \
  -signature "$FM_TEST_CHECKS_ROOT/signature" "$FM_TEST_CHECKS_ROOT/signed" >/dev/null
printf '%s' "$p" | tr '_-' '/+' > "$FM_TEST_CHECKS_ROOT/payload.b64"
while [ "$(( $(wc -c < "$FM_TEST_CHECKS_ROOT/payload.b64") % 4 ))" -ne 0 ]; do
  printf '=' >> "$FM_TEST_CHECKS_ROOT/payload.b64"
done
openssl base64 -d -A -in "$FM_TEST_CHECKS_ROOT/payload.b64" | \
  jq -e --argjson now "$(date +%s)" '.iss == "5219477" and .iat <= $now and .exp > $now and .exp <= $now + 600' >/dev/null
for arg in "$@"; do
  case "$arg" in *"$jwt"*|*installation_secret*) exit 81 ;; esac
done
case "${*: -1}" in
  https://api.github.com/repos/*/installation)
    case "${FM_TEST_COVERAGE:-}" in
      missing) printf '{}\n404' ;;
      other) printf '{"id":123,"account":{"login":"example"}}\n200' ;;
      error) printf '{}\n401' ;;
      *) printf '{"id":168738038,"account":{"login":"example"}}\n200' ;;
    esac
    exit 0 ;;
esac
[ -z "${FM_TEST_MINT_FAIL:-}" ] || exit 88
case " $* " in
  *' --data {"repositories":["repo"],"permissions":{"checks":"read","statuses":"read","metadata":"read"}} https://api.github.com/app/installations/168738038/access_tokens '*) ;;
  *) exit 82 ;;
esac
count=0
[ ! -f "$FM_TEST_CHECKS_ROOT/mints" ] || count=$(cat "$FM_TEST_CHECKS_ROOT/mints")
count=$((count + 1))
printf '%s' "$count" > "$FM_TEST_CHECKS_ROOT/mints"
expiry=2099-01-01T00:00:00Z
if [ "${FM_TEST_EXPIRED:-}" = always ] || { [ "${FM_TEST_EXPIRED:-}" = first ] && [ "$count" = 1 ]; }; then
  expiry=2000-01-01T00:00:00Z
fi
printf '{"token":"installation_secret.-%s","expires_at":"%s"}' "$count" "$expiry"
SH
cat > "$FAKEBIN/gh" <<'SH'
#!/usr/bin/env bash
set -eu
for arg in "$@"; do
  case "$arg" in *installation_secret*|*ordinary_user_token*) exit 83 ;; esac
done
printf '%s\n' "$*" >> "$FM_TEST_CHECKS_ROOT/argv"
if [ "${1:-}" = pr ]; then
  [ "$GH_TOKEN" = ordinary_user_token ] || exit 86
  if [ -n "${FM_TEST_APP_ON:-}" ] && [ "${FM_TEST_COVERAGE:-}" != missing ] && [ "${FM_TEST_COVERAGE:-}" != other ]; then
    [ "$*" = "pr view https://github.com/example/repo/pull/7 --json headRefOid,id,headRefName" ] || exit 90
    printf '%s\n' '{"headRefOid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","id":"PR_test","headRefName":"feature"}'
  else
    printf 'normal read\n'
  fi
  exit 0
fi
if [ "$GH_TOKEN" = ordinary_user_token ]; then
  case "${*: -1}" in
    */rules/branches/main)
      if [ "${FM_TEST_RULES_UNAVAILABLE:-}" = 1 ]; then
        printf '%s' '[{"status":"403","message":"Upgrade to GitHub Pro or make this repository public to enable this feature."}]'
        exit 1
      elif [ "${FM_TEST_RULES_ERROR:-}" = 1 ]; then
        printf '%s' '[{"status":"403","message":"Resource not accessible by personal access token"}]'
        exit 1
      fi
      ;;
  esac
  case " $* " in
    *' api graphql '*)
      printf '%s\n' '{"data":{"node":{"headRefOid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","baseRefName":"main","baseRef":{"branchProtectionRule":{"requiredStatusCheckContexts":["ci","neutral","cancelled"]}}}}}' ;;
    *'/rules/branches/main '*) printf '%s\n' '[[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"required-status"}]}}]]' ;;
    *'/rules/branches/main') printf '%s\n' '[[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"required-status"}]}}]]' ;;
    *) exit 91 ;;
  esac
  exit 0
fi
[ "$GH_TOKEN" = "installation_secret.-$(cat "$FM_TEST_CHECKS_ROOT/mints")" ] || exit 84
[ -z "${GH_DEBUG:-}" ] || exit 85
case " $* " in *' api graphql '*) exit 92 ;; esac
[ "${FM_TEST_CHECKS_ERROR:-}" != 1 ] || exit 93
head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
[ "${FM_TEST_HEAD_MISMATCH:-}" != 1 ] || head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
case "${*: -1}" in
  */check-runs*)
    case "${*: -1}" in *'filter=latest') ;; *) exit 95 ;; esac
    runs='[{"name":"ci","status":"completed","conclusion":"success"},{"name":"optional","status":"completed","conclusion":"failure"},{"name":"neutral","status":"completed","conclusion":"neutral"},{"name":"cancelled","status":"completed","conclusion":"cancelled"}]'
    [ "${FM_TEST_NO_CHECKS:-}" != 1 ] || runs='[]'
    jq -cn --arg head "$head" --argjson runs "$runs" '[{check_runs:[$runs[:2][] | . + {head_sha:$head}]},{check_runs:[$runs[2:][] | . + {head_sha:$head}]}]' ;;
  */status*)
    statuses='[{"context":"required-status","state":"pending"}]'
    [ "${FM_TEST_NO_CHECKS:-}" != 1 ] || statuses='[]'
    jq -cn --arg head "$head" --argjson statuses "$statuses" '[{sha:$head,statuses:$statuses}]' ;;
  *) exit 94 ;;
esac
SH
chmod +x "$FAKEBIN/gh" "$FAKEBIN/curl"
URL=https://github.com/example/repo/pull/7
out=$("$SCRIPT" pr view "$URL" --json statusCheckRollup)
[ "$out" = 'normal read' ] && [ ! -f "$TMP_ROOT/mints" ] || fail 'unconfigured path changed authentication'
pass 'unconfigured reads use the ordinary login without minting'
jq -n --arg key "$KEY" '{app_id:"5219477",installation_id:"168738038",key_path:$key}' > "$FM_HOME/config/gh-checks-app.json"
export FM_TEST_APP_ON=1 GH_DEBUG=api
export TOKEN=ambient_token JWT=ambient_jwt RESPONSE=ambient_response SIGNATURE=ambient_signature
bash -x "$SCRIPT" pr view "$URL" --json statusCheckRollup > "$TMP_ROOT/out" 2> "$TMP_ROOT/trace"
jq -e '.statusCheckRollup | length == 5' "$TMP_ROOT/out" >/dev/null || fail 'configured read did not return every App checks page'
[ "$GH_TOKEN" = ordinary_user_token ] || fail 'App token escaped into caller'
if rg 'installation_secret|eyJhbGci' "$TMP_ROOT/trace" "$TMP_ROOT/argv"; then fail 'secret leaked into logs or argv'; fi
pass 'configured reads verify RS256 JWT and pass secrets without argv/debug/trace leakage'
out=$("$SCRIPT" pr checks "$URL" --required --json name,state,bucket --jq '.')
printf '%s' "$out" | jq -e '
  length == 4 and any(.[]; .name == "ci" and .bucket == "pass")
    and any(.[]; .name == "required-status" and .bucket == "pending")
    and any(.[]; .name == "cancelled" and .bucket == "cancel")
    and any(.[]; .name == "neutral" and .bucket == "skipping")
    and all(.[]; .name != "optional")
' >/dev/null || fail 'required checks lost filtering, states, pagination, or latest-run selection'
"$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup >/dev/null
[ "$(cat "$TMP_ROOT/mints")" = 3 ] || fail 'tokens persisted across reads'
pass 'normal-login PR identity and paginated App commit checks preserve required-check behavior'
for scenario in FM_TEST_HEAD_MISMATCH FM_TEST_CHECKS_ERROR; do
  if (export "$scenario=1"; "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup) > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then
    fail 'invalid or mismatched App commit checks accepted'
  fi
  [ ! -s "$TMP_ROOT/out" ] || fail 'invalid checks published partial output'
done
out=$(FM_TEST_NO_CHECKS=1 "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup)
printf '%s' "$out" | jq -e '.headRefOid == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" and .statusCheckRollup == []' >/dev/null \
  || fail 'empty commit rollup lost its head binding'
if FM_TEST_NO_CHECKS=1 "$SCRIPT" pr checks "$URL" --required > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'no required checks reported readiness'; fi
assert_grep 'no checks reported' "$TMP_ROOT/err" 'empty checks diagnostic missing'
out=$(FM_TEST_RULES_UNAVAILABLE=1 "$SCRIPT" pr checks "$URL" --required --json name,state,bucket --jq '.')
printf '%s' "$out" | jq -e 'length == 3 and all(.[]; .name != "required-status")' >/dev/null \
  || fail 'plan-gated rules lost classic branch-protection requirements'
if FM_TEST_RULES_ERROR=1 "$SCRIPT" pr checks "$URL" --required > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'unreadable branch rules accepted'; fi
rm "$TMP_ROOT/mints"
if FM_TEST_EXPIRED=always "$SCRIPT" pr checks "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'expired token accepted'; fi
[ ! -s "$TMP_ROOT/out" ] && [ "$(cat "$TMP_ROOT/mints")" = 1 ] || fail 'expiry refusal was not bounded'
pass 'expired token refuses after a single mint'
"$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup >/dev/null
[ "$(cat "$TMP_ROOT/mints")" = 2 ] || fail 'next read did not mint a fresh token'
pass 'a new read refreshes credentials after an expired response'
for coverage in missing other; do
  out=$(FM_TEST_COVERAGE=$coverage "$SCRIPT" pr checks "$URL" 2> "$TMP_ROOT/err")
  [ "$out" = 'normal read' ] && [ "$(cat "$TMP_ROOT/mints")" = 2 ] || fail 'uncovered repository did not fall back without minting'
  assert_grep 'outside the configured installation' "$TMP_ROOT/err" 'fallback reason missing'
done
pass 'missing installation and different installation preserve ordinary-login reads'
if FM_TEST_COVERAGE=error "$SCRIPT" pr checks "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'coverage authentication failure fell back'; fi
pass 'coverage authentication errors refuse instead of falling back'
before=$(wc -l < "$TMP_ROOT/argv")
if FM_TEST_MINT_FAIL=1 "$SCRIPT" pr checks "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'mint failure accepted'; fi
[ "$(wc -l < "$TMP_ROOT/argv")" = "$before" ] || fail 'failed App authentication used the normal login'
pass 'failed App token mint refuses without falling back to the ordinary login'
chmod 644 "$KEY"
if "$SCRIPT" pr checks "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'public key permissions accepted'; fi
pass 'insecure private key permissions refuse authentication'
chmod 600 "$KEY"
printf '{}' > "$FM_HOME/config/gh-checks-app.json"
if "$SCRIPT" pr checks "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'invalid config fell back to normal login'; fi
pass 'invalid configuration fails closed'
if "$SCRIPT" pr merge "$URL" > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'helper accepted write'; fi
pass 'helper refuses write commands'
