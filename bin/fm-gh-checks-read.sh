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
# permissions to checks/statuses/metadata read for the target repository on github.com.
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
  '{repositories:[$repo],permissions:{checks:"read",statuses:"read",metadata:"read"}}')
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
MODE=$2
URL=$3
shift 3
FILTER=.
case "$MODE" in
  view)
    [ "$#" = 2 ] && [ "$1" = --json ] || die 'unsupported checks view arguments'
    case "$2" in statusCheckRollup|headRefOid,statusCheckRollup) VIEW_FIELDS=$2 ;; *) die 'unsupported checks view fields' ;; esac
    ;;
  checks)
    [ "${1:-}" = --required ] || die 'expected a required checks read'
    shift
    if [ "$#" -gt 0 ]; then
      [ "$#" = 4 ] && [ "$1" = --json ] && [ "$2" = name,state,bucket ] && [ "$3" = --jq ] \
        || die 'unsupported required checks arguments'
      FILTER=$4
    fi
    ;;
esac
METADATA=$(gh pr view "$URL" --json headRefOid,id,headRefName) \
  || die 'could not read pull-request identity with normal login'
HEAD=$(printf '%s' "$METADATA" | jq -er '.headRefOid') \
  || die 'missing pull-request head'
fm_pr_head_valid "$HEAD" || die 'invalid pull-request head'
REQUIRED_NAMES='[]'
if [ "$MODE" = checks ]; then
  PR_ID=$(printf '%s' "$METADATA" | jq -er '.id | select(type == "string" and length > 0)') \
    || die 'missing pull-request identity'
  POLICY_QUERY=$(cat <<'GRAPHQL'
query($id:ID!) { node(id:$id) { ... on PullRequest {
  headRefOid baseRefName baseRef { branchProtectionRule { requiredStatusCheckContexts } }
} } }
GRAPHQL
)
  POLICY=$(gh api graphql --hostname github.com -f "query=$POLICY_QUERY" -f "id=$PR_ID") \
    || die 'could not read required-check policy with normal login'
  REQUIRED_NAMES=$(printf '%s' "$POLICY" | jq -ce --arg head "$HEAD" '
    if (.errors // [] | length) == 0 and .data.node.headRefOid == $head
      and (.data.node.baseRefName | type) == "string" and .data.node.baseRef != null
    then (.data.node.baseRef.branchProtectionRule.requiredStatusCheckContexts // [])
    else error("invalid pull-request policy or head mismatch") end
  ') || die 'invalid required-check policy or head mismatch'
  BASE=$(printf '%s' "$POLICY" | jq -er '.data.node.baseRefName | @uri')
  if ! RULES=$(gh api --hostname github.com --paginate --slurp "repos/$REPO/rules/branches/$BASE" 2>/dev/null); then
    printf '%s' "$RULES" | jq -e '
      length == 1 and .[0].status == "403" and .[0].message == "Upgrade to GitHub Pro or make this repository public to enable this feature."
    ' >/dev/null 2>&1 || die 'could not read branch rules with normal login'
    RULES='[]'
  fi
  REQUIRED_NAMES=$(jq -cn --argjson classic "$REQUIRED_NAMES" --argjson rules "$RULES" '
    $classic + [$rules[][] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context]
    | unique
  ') || die 'invalid required-check rules'
fi
RUN_PAGES=$(GH_TOKEN="$TOKEN" gh api --hostname github.com --paginate --slurp \
  "repos/$REPO/commits/$HEAD/check-runs?per_page=100&filter=latest") \
  || die 'could not read commit check runs'
STATUS_PAGES=$(GH_TOKEN="$TOKEN" gh api --hostname github.com --paginate --slurp \
  "repos/$REPO/commits/$HEAD/status?per_page=100") \
  || die 'could not read commit statuses'
CONTEXTS=$(jq -cn --arg head "$HEAD" --argjson runs "$RUN_PAGES" --argjson statuses "$STATUS_PAGES" '
  if ($runs | type) != "array" or ($runs | length) == 0
    or ($statuses | type) != "array" or ($statuses | length) == 0
    or any($runs[]; (.check_runs | type) != "array")
    or any($statuses[]; .sha != $head or (.statuses | type) != "array")
    or any($runs[].check_runs[]; .head_sha != $head)
  then error("invalid checks response or head mismatch") else
    [$runs[].check_runs[] | {
      __typename:"CheckRun",name,status:(.status | ascii_upcase),
      conclusion:(if .conclusion == null then null else .conclusion | ascii_upcase end),
      startedAt:.started_at,completedAt:.completed_at,detailsUrl:.details_url
    }] + [$statuses[].statuses[] | {
      __typename:"StatusContext",context,state:(.state | ascii_upcase),createdAt:.created_at,targetUrl:.target_url
    }]
  end
') || die 'invalid commit checks response or head mismatch'
if [ "$MODE" = view ]; then
  jq -cn --arg head "$HEAD" --arg fields "$VIEW_FIELDS" --argjson checks "$CONTEXTS" '
    {statusCheckRollup:$checks} + (if $fields == "headRefOid,statusCheckRollup" then {headRefOid:$head} else {} end)'
else
  CHECKS=$(printf '%s' "$CONTEXTS" | jq -ce --argjson required "$REQUIRED_NAMES" '
    [ .[] | select((.name // .context) as $name | $required | index($name) != null)
        | {name:(.name // .context), state:(.state // (if .status == "COMPLETED" then .conclusion else .status end))}
        | . + {bucket:(if .state == "SUCCESS" then "pass"
            elif .state == "SKIPPED" or .state == "NEUTRAL" then "skipping"
            elif .state == "ERROR" or .state == "FAILURE" or .state == "TIMED_OUT" or .state == "ACTION_REQUIRED" then "fail"
            elif .state == "CANCELLED" then "cancel" else "pending" end)} ]
  ') || die 'invalid required checks response'
  if [ "$CONTEXTS" = '[]' ]; then
    printf "no checks reported on the '%s' branch\n" "$(printf '%s' "$METADATA" | jq -r '.headRefName')" >&2
    exit 1
  fi
  if [ "$CHECKS" = '[]' ]; then
    printf "no required checks reported on the '%s' branch\n" "$(printf '%s' "$METADATA" | jq -r '.headRefName')" >&2
    exit 1
  fi
  printf '%s' "$CHECKS" | jq -r "$FILTER"
fi
