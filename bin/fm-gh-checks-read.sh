#!/usr/bin/env bash
# Run a gh checks read, optionally using a read-only GitHub App installation.
# Usage: fm-gh-checks-read.sh pr <view|checks> <pr-url> <ordinary gh arguments...>
#        fm-gh-checks-read.sh configured (exit 0 when App configuration exists)
# Only these read commands are accepted; writes always use the normal gh login.
# Opt-in: $FM_HOME/config/gh-checks-app.json with positive decimal string fields
# app_id, installation_id, and an absolute key_path. The key must be a regular,
# nonsymlink file owned by this uid with mode 600 or 400. Missing config executes
# gh unchanged; invalid config or failed App authentication refuses the read.
# Configured reads require the existing curl, jq, and openssl tools. JWTs use
# RS256, iat=now-60 and exp=now+540. The installation request explicitly narrows
# permissions to checks/statuses/metadata/pull_requests read for the target repository on github.com.
# Tokens are never stored or cached: each read mints one fresh token and rejects
# expired responses. Secrets reach curl via stdin and gh only via GH_TOKEN,
# never argv. Repositories outside this installation use the normal login with
# a one-line diagnostic; every other authentication failure refuses.
# Shell tracing and gh/curl debug output are disabled for secrets.
set -eu
case $- in *x*) set +x ;; esac
set -o pipefail

# A caller can preserve its existing single-read path when not configured.
if [ "${1:-}" = configured ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
  CONFIG="${FM_HOME:-$FM_ROOT}/config/gh-checks-app.json"
  [ -e "$CONFIG" ] || [ -L "$CONFIG" ]
  exit $?
fi
[ "$#" -ge 2 ] && [ "$1" = pr ] || exit 2
case "$2" in view|checks) ;; *) exit 2 ;; esac
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-$FM_ROOT}"
CONFIG="$FM_HOME/config/gh-checks-app.json"
if [ ! -e "$CONFIG" ] && [ ! -L "$CONFIG" ]; then
  exec gh "$@"
fi

die() { printf 'gh checks App: %s\n' "$1" >&2; exit 1; }
[ -f "$CONFIG" ] && [ ! -L "$CONFIG" ] && [ -r "$CONFIG" ] \
  || die 'configuration is unavailable'
for tool in curl jq openssl; do
  command -v "$tool" >/dev/null 2>&1 || die "configured reads require $tool"
done
FIELDS=$(jq -er '
  def id: type == "string" and test("^[1-9][0-9]*$");
  if type == "object" and (.app_id | id) and (.installation_id | id)
    and (.key_path | type == "string" and startswith("/") and (test("[\\x00-\\x1f\\x7f]") | not))
  then .app_id, .installation_id, .key_path else error("invalid configuration") end
' "$CONFIG" 2>/dev/null) || die 'invalid configuration'
{ IFS= read -r APP_ID; IFS= read -r INSTALLATION_ID; IFS= read -r KEY_PATH; } <<< "$FIELDS"
[ -f "$KEY_PATH" ] && [ ! -L "$KEY_PATH" ] && [ -r "$KEY_PATH" ] \
  || die 'private key is unavailable'
KEY_STAT=$(stat -c '%u:%a' "$KEY_PATH" 2>/dev/null) \
  || KEY_STAT=$(stat -f '%u:%Lp' "$KEY_PATH" 2>/dev/null) \
  || die 'could not inspect private key'
case "$KEY_STAT" in "$(id -u):600"|"$(id -u):400") ;; *) die 'private key must be owned by this uid with mode 600 or 400' ;; esac

# Parse the canonical github.com target before any secret-bearing request.
# shellcheck source=bin/fm-pr-lib.sh
. "$SCRIPT_DIR/fm-pr-lib.sh"
fm_pr_url_parse "${3:-}" && [ "$FM_PR_PROVIDER" = github ] \
  || die 'expected a canonical GitHub pull-request URL'
REPO=$FM_PR_PATH
REPO_NAME=${REPO#*/}
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
unset GH_DEBUG
export GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1
export -n TOKEN JWT RESPONSE SIGNATURE
NOW=$(date +%s)
HEADER=$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url)
PAYLOAD=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$((NOW - 60))" "$((NOW + 540))" "$APP_ID" | b64url)
SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$KEY_PATH" 2>/dev/null | b64url) \
  || die 'could not sign App authentication'
JWT="$HEADER.$PAYLOAD.$SIGNATURE"
# Resolve coverage with App authentication, before minting a repository token.
# -q is first to ignore ~/.curlrc (including trace/header/output options).
RESPONSE=$(printf 'header = "Authorization: Bearer %s"\n' "$JWT" | \
  curl -q --config - --silent --connect-timeout 10 --max-time 30 \
    --header 'Accept: application/vnd.github+json' --write-out '\n%{http_code}' \
    "https://api.github.com/repos/$REPO/installation" 2>/dev/null) \
  || die 'could not resolve repository installation'
HTTP_CODE=${RESPONSE##*$'\n'}
RESPONSE=${RESPONSE%$'\n'*}
COVERED_ID=
if [ "$HTTP_CODE" = 200 ]; then
  COVERED_ID=$(printf '%s' "$RESPONSE" | jq -er '
    .id | select(type == "number" and . > 0 and floor == .) | tostring
  ' 2>/dev/null) || die 'invalid repository installation response'
fi
if [ "$HTTP_CODE" = 404 ] || { [ "$HTTP_CODE" = 200 ] && [ "$COVERED_ID" != "$INSTALLATION_ID" ]; }; then
  printf 'gh checks App: repository is outside the configured installation; using normal login\n' >&2
  unset JWT SIGNATURE RESPONSE
  exec gh "$@"
fi
[ "$HTTP_CODE" = 200 ] || die 'could not resolve repository installation'
printf '%s' "$RESPONSE" | jq -e --arg repo "$REPO" '
  (.account.login | ascii_downcase) == ($repo | split("/")[0] | ascii_downcase)
' >/dev/null 2>&1 || die 'invalid repository installation response'
BODY=$(jq -cn --arg repo "$REPO_NAME" \
  '{repositories:[$repo],permissions:{checks:"read",statuses:"read",metadata:"read",pull_requests:"read"}}')
RESPONSE=$(printf 'header = "Authorization: Bearer %s"\n' "$JWT" | \
  curl -q --config - --silent --fail --connect-timeout 10 --max-time 30 \
    --request POST --header 'Accept: application/vnd.github+json' \
    --header 'Content-Type: application/json' --data "$BODY" \
    "https://api.github.com/app/installations/$INSTALLATION_ID/access_tokens" 2>/dev/null) \
  || die 'could not mint installation token'
unset JWT SIGNATURE
TOKEN=$(printf '%s' "$RESPONSE" | jq -er '
  if (.token | type == "string" and length > 0 and all(explode[]; . >= 33 and . <= 126))
    and (.expires_at | type == "string")
  then .token else error("invalid installation token") end
' 2>/dev/null) || die 'invalid installation token response'
EXPIRES=$(printf '%s' "$RESPONSE" | jq -er '.expires_at | fromdateiso8601' 2>/dev/null) \
  || die 'invalid installation token expiry'
unset RESPONSE
NOW=$(date +%s)
[ "$EXPIRES" -gt "$((NOW + 60))" ] || die 'installation token expired'
READ_ARGS=("$@")
MODE=$2
URL=$3
shift 3
if [ "$MODE" = view ]; then
  [ "$#" = 2 ] && [ "$1" = --json ] || die 'unsupported checks view arguments'
  case "$2" in statusCheckRollup|headRefOid,statusCheckRollup) VIEW_FIELDS=$2 ;; *) die 'unsupported checks view fields' ;; esac
fi
METADATA=$(gh pr view "$URL" --json headRefOid,id,headRefName) \
  || die 'could not read pull-request identity with normal login'
HEAD=$(printf '%s' "$METADATA" | jq -er '.headRefOid') \
  || die 'missing pull-request head'
fm_pr_head_valid "$HEAD" || die 'invalid pull-request head'
if [ "$MODE" = checks ]; then
  RESULT_CODE=0
  RESULT=$(GH_HOST=github.com GH_TOKEN="$TOKEN" gh "${READ_ARGS[@]}") || RESULT_CODE=$?
  AFTER=$(gh pr view "$URL" --json headRefOid --jq .headRefOid) \
    || die 'could not reread pull-request head with normal login'
  [ "$HEAD" = "$AFTER" ] || die 'pull-request head changed during required checks read'
  [ -z "$RESULT" ] || printf '%s\n' "$RESULT"
  exit "$RESULT_CODE"
fi
PR_ID=$(printf '%s' "$METADATA" | jq -er '.id | select(type == "string" and length > 0)') \
  || die 'missing pull-request identity'
QUERY=$(cat <<'GRAPHQL'
query($id:ID!, $endCursor:String) {
  node(id:$id) { ... on PullRequest {
    commits(last:1) { nodes { commit {
      oid
      statusCheckRollup { contexts(first:100, after:$endCursor) {
        nodes {
          __typename
          ... on CheckRun { name status conclusion startedAt completedAt detailsUrl }
          ... on StatusContext { context state createdAt targetUrl }
        }
        pageInfo { hasNextPage endCursor }
      } }
    } } }
  } }
}
GRAPHQL
)
PAGES=$(GH_TOKEN="$TOKEN" gh api graphql --hostname github.com --paginate --slurp \
  -f "query=$QUERY" -f "id=$PR_ID") || die 'could not read pull-request commit checks'
CONTEXTS=$(printf '%s' "$PAGES" | jq -ce --arg head "$HEAD" '
  if type != "array" or length == 0 then error("missing checks pages") else . end
  | [ .[]
      | if (.errors // [] | length) > 0 then error("GraphQL errors") else .data.node.commits.nodes end
      | if type == "array" and length == 1 then .[0].commit else error("missing head commit") end
      | if .oid == $head then . else error("commit head mismatch") end
      | if .statusCheckRollup == null then []
        elif (.statusCheckRollup.contexts.nodes | type) == "array" then .statusCheckRollup.contexts.nodes
        else error("invalid checks response") end
    ] | add
') || die 'invalid commit checks response or head mismatch'
jq -cn --arg head "$HEAD" --arg fields "$VIEW_FIELDS" --argjson checks "$CONTEXTS" '
  {statusCheckRollup:$checks} + (if $fields == "headRefOid,statusCheckRollup" then {headRefOid:$head} else {} end)'
