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
  *' --data {"repositories":["repo"],"permissions":{"checks":"read","statuses":"read","metadata":"read","pull_requests":"read"}} https://api.github.com/app/installations/168738038/access_tokens '*) ;;
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
  printf 'normal read\n'
  exit 0
fi
[ "$1 $2" = 'api graphql' ] || exit 92
if [ "$GH_TOKEN" = ordinary_user_token ]; then
  case " $* " in *' number=7 '*) ;; *' number=7') ;; *) exit 91 ;; esac
  marker="$FM_TEST_CHECKS_ROOT/metadata-$(cat "$FM_TEST_CHECKS_ROOT/mints")"
  head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  rollup=ROLLUP_test
  if [ -f "$marker" ]; then
    [ "${FM_TEST_REQUIRED_HEAD_RACE:-}" != 1 ] || head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    [ "${FM_TEST_ROLLUP_RACE:-}" != 1 ] || rollup=ROLLUP_changed
  fi
  : > "$marker"
  payload=$(jq -cn --arg head "$head" --arg rollup "$rollup" '
    {data:{repository:{pullRequest:{id:"PR_test",headRefOid:$head,headRefName:"feature",
      commits:{nodes:[{commit:{oid:$head,statusCheckRollup:{id:$rollup}}}]}}}}}')
  if [ "${FM_TEST_NO_ROLLUP:-}" = 1 ]; then
    payload=$(printf '%s' "$payload" | jq '.data.repository.pullRequest.commits.nodes[0].commit.statusCheckRollup = null')
  fi
  case "${FM_TEST_METADATA_INVALID:-}" in
    errors) payload=$(printf '%s' "$payload" | jq '.errors = [{message:"denied"}]') ;;
    missing) payload=$(printf '%s' "$payload" | jq 'del(.data.repository.pullRequest.commits.nodes[0].commit.statusCheckRollup)') ;;
    mismatch) payload=$(printf '%s' "$payload" | jq '.data.repository.pullRequest.commits.nodes[0].commit.oid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"') ;;
  esac
  printf '%s\n' "$payload"
  exit 0
fi
[ "$GH_TOKEN" = "installation_secret.-$(cat "$FM_TEST_CHECKS_ROOT/mints")" ] || exit 84
[ -z "${GH_DEBUG:-}" ] || exit 85
case " $* " in *' id=ROLLUP_test '*|*' id=ROLLUP_test') ;; *) exit 94 ;; esac
case " $* " in *' prID=PR_test '*|*' prID=PR_test') ;; *) exit 95 ;; esac
[ "${FM_TEST_CHECKS_ERROR:-}" != 1 ] || exit 93
case " $* " in *workflowRun*) printf 'Resource not accessible by integration\n' >&2; exit 93 ;; esac
nodes='[{"__typename":"CheckRun","name":"ci","status":"COMPLETED","conclusion":"SUCCESS","startedAt":"2026-01-01T00:00:01Z","completedAt":"2026-01-01T00:00:10Z","isRequired":true,"app":{"id":1}},{"__typename":"CheckRun","name":"ci","status":"COMPLETED","conclusion":"FAILURE","startedAt":"2026-01-01T00:00:09Z","completedAt":"2026-01-01T00:00:09Z","isRequired":false,"app":{"id":2}},{"__typename":"CheckRun","name":"ci","status":"COMPLETED","conclusion":"FAILURE","startedAt":"2026-01-01T00:00:00Z","completedAt":"2026-01-01T00:00:00Z","isRequired":true,"app":{"id":1}},{"__typename":"CheckRun","name":"neutral","status":"COMPLETED","conclusion":"NEUTRAL","isRequired":true},{"__typename":"CheckRun","name":"cancelled","status":"COMPLETED","conclusion":"CANCELLED","isRequired":true},{"__typename":"StatusContext","context":"required-status","state":"PENDING","isRequired":true}]'
[ "${FM_TEST_NO_CHECKS:-}" != 1 ] || nodes='[]'
payload=$(jq -cn --argjson nodes "$nodes" '
  $nodes | map(if .__typename == "CheckRun" then . + {startedAt:(.startedAt // null),completedAt:(.completedAt // null)} else . end)
  | [ .[:2], .[2:] ] | to_entries
  | map({data:{node:{id:"ROLLUP_test",__typename:"StatusCheckRollup",
      contexts:{nodes:.value,pageInfo:{hasNextPage:(.key == 0),endCursor:(if .key == 0 then "next" else null end)}}}}})')
case "${FM_TEST_INVALID_PAGE:-}" in
  errors) payload=$(printf '%s' "$payload" | jq '.[0].errors = [{message:"denied"}]') ;;
  error_type) payload=$(printf '%s' "$payload" | jq '.[0].errors = {}') ;;
  context_type) payload=$(printf '%s' "$payload" | jq '.[0].data.node.contexts.nodes = "bad"') ;;
  unknown_context) payload=$(printf '%s' "$payload" | jq '.[0].data.node.contexts.nodes[0].__typename = "Other"') ;;
  conclusion_type) payload=$(printf '%s' "$payload" | jq '.[0].data.node.contexts.nodes[0].conclusion = 7') ;;
  required_missing) payload=$(printf '%s' "$payload" | jq 'del(.[0].data.node.contexts.nodes[0].isRequired)') ;;
  pagination) payload=$(printf '%s' "$payload" | jq '.[-1].data.node.contexts.pageInfo.hasNextPage = true | .[-1].data.node.contexts.pageInfo.endCursor = "another"') ;;
  rollup) payload=$(printf '%s' "$payload" | jq '.[0].data.node.id = "ROLLUP_other"') ;;
esac
[ "${FM_TEST_HEAD_MISMATCH:-}" != 1 ] || payload=$(printf '%s' "$payload" | jq '.[0].data.node.id = "ROLLUP_other"')
printf '%s\n' "$payload"
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
jq -e '.statusCheckRollup | length == 6' "$TMP_ROOT/out" >/dev/null || fail 'configured read did not return every App checks page'
jq -e '.statusCheckRollup | [.[] | select(.name == "ci")] | length == 3 and any(.[]; .conclusion == "FAILURE")' "$TMP_ROOT/out" >/dev/null \
  || fail 'overlapping old-success/new-failure contexts were filtered'
[ "$GH_TOKEN" = ordinary_user_token ] || fail 'App token escaped into caller'
if rg 'installation_secret|eyJhbGci' "$TMP_ROOT/trace" "$TMP_ROOT/argv"; then fail 'secret leaked into logs or argv'; fi
pass 'configured reads verify RS256 JWT and pass secrets without argv/debug/trace leakage'
out=$("$SCRIPT" pr checks "$URL" --required --json name,state,bucket --jq '.')
printf '%s' "$out" | jq -e '
  length == 5 and any(.[]; .name == "ci" and .bucket == "pass")
    and any(.[]; .name == "required-status" and .bucket == "pending")
    and any(.[]; .name == "cancelled" and .bucket == "cancel")
    and any(.[]; .name == "neutral" and .bucket == "skipping")
    and ([.[] | select(.name == "ci")] | length == 2)
    and ([.[] | select(.name == "ci") | .state] == ["SUCCESS", "FAILURE"])
' >/dev/null || fail 'server required classification or context ordering changed'
"$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup >/dev/null
[ "$(cat "$TMP_ROOT/mints")" = 3 ] || fail 'tokens persisted across reads'
pass 'normal-login rollup identity and paginated App contexts preserve required-check behavior'
for scenario in FM_TEST_HEAD_MISMATCH FM_TEST_CHECKS_ERROR; do
  if (export "$scenario=1"; "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup) > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then
    fail 'invalid or mismatched App rollup contexts accepted'
  fi
  [ ! -s "$TMP_ROOT/out" ] || fail 'invalid checks published partial output'
done
out=$(FM_TEST_NO_CHECKS=1 "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup)
printf '%s' "$out" | jq -e '.headRefOid == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" and .statusCheckRollup == []' >/dev/null \
  || fail 'empty commit rollup lost its head binding'
if FM_TEST_NO_CHECKS=1 "$SCRIPT" pr checks "$URL" --required > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'no required checks reported readiness'; fi
assert_grep 'no checks reported' "$TMP_ROOT/err" 'empty checks diagnostic missing'
if FM_TEST_REQUIRED_HEAD_RACE=1 "$SCRIPT" pr checks "$URL" --required --json name,state,bucket --jq '.' > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'required checks accepted a changed head'; fi
[ ! -s "$TMP_ROOT/out" ] || fail 'required checks published output after a head change'
for invalid in errors error_type context_type unknown_context conclusion_type pagination rollup; do
  if FM_TEST_INVALID_PAGE=$invalid "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail "invalid rollup page accepted: $invalid"; fi
  [ ! -s "$TMP_ROOT/out" ] || fail 'invalid pages published partial contexts'
done
for invalid in errors missing mismatch; do
  if FM_TEST_METADATA_INVALID=$invalid "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail "invalid metadata accepted: $invalid"; fi
  [ ! -s "$TMP_ROOT/out" ] || fail 'invalid metadata published checks'
done
if FM_TEST_INVALID_PAGE=required_missing "$SCRIPT" pr checks "$URL" --required --json name,state,bucket --jq '.' > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'missing server required classification accepted'; fi
[ ! -s "$TMP_ROOT/out" ] || fail 'missing classification published required checks'
if FM_TEST_ROLLUP_RACE=1 "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup > "$TMP_ROOT/out" 2> "$TMP_ROOT/err"; then fail 'rollup identity change accepted'; fi
[ ! -s "$TMP_ROOT/out" ] || fail 'rollup change published checks'
out=$(FM_TEST_NO_ROLLUP=1 "$SCRIPT" pr view "$URL" --json headRefOid,statusCheckRollup)
printf '%s' "$out" | jq -e '.headRefOid == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" and .statusCheckRollup == []' >/dev/null \
  || fail 'null rollup did not return head-bound empty contexts'
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
