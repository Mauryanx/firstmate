#!/usr/bin/env bash
# Run a gh checks read, optionally using a read-only GitHub App installation.
# Usage: fm-gh-checks-read.sh pr view <pr-url> --json <statusCheckRollup|headRefOid,statusCheckRollup>
#        fm-gh-checks-read.sh pr checks <pr-url> --required [--json name,state,bucket --jq <filter>]
#        fm-gh-checks-read.sh configured (exit 0 when App configuration exists)
# Only these read commands are accepted; writes always use the normal gh login.
# Configured reads accept the arguments above; unconfigured or uncovered reads
# forward the caller's view/checks arguments unchanged to gh.
# Opt-in: $FM_HOME/config/gh-checks-app.json with positive decimal string fields
# app_id, installation_id, and an absolute key_path. The key must be a regular,
# nonsymlink file owned by this uid with mode 600 or 400. Missing config executes
# gh unchanged; invalid config or failed App authentication refuses the read.
# Configured reads require the existing curl, jq, and openssl tools. JWTs use
# RS256, iat=now-60 and exp=now+540. The installation request explicitly narrows
# permissions to checks/statuses/metadata/pull_requests/actions read for the target repository on github.com.
# Tokens are never stored or cached: each read mints one fresh token and rejects
# expired responses. Secrets reach curl via stdin and gh only via GH_TOKEN,
# never argv. Repositories outside this installation use the normal login with
# a one-line diagnostic; every other authentication failure refuses.
# Shell tracing and gh/curl debug output are disabled for secrets.
# Normal login resolves PR/head/rollup identity before and after App reads.
# Changed identity, GraphQL errors, malformed data, or incomplete pagination
# refuse output. Views retain every context for merge verification.
# Required-check reporting selects the newest start per name/workflow/event
# before filtering by server isRequired; absent workflow fields use empty values,
# and legacy statuses group separately by context name.
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
  '{repositories:[$repo],permissions:{checks:"read",statuses:"read",metadata:"read",pull_requests:"read",actions:"read"}}')
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
shift 3
FILTER=.
if [ "$MODE" = view ]; then
  [ "$#" = 2 ] && [ "$1" = --json ] || die 'unsupported checks view arguments'
  case "$2" in statusCheckRollup|headRefOid,statusCheckRollup) VIEW_FIELDS=$2 ;; *) die 'unsupported checks view fields' ;; esac
else
  [ "${1:-}" = --required ] || die 'expected a required checks read'
  shift
  if [ "$#" -gt 0 ]; then
    [ "$#" = 4 ] && [ "$1" = --json ] && [ "$2" = name,state,bucket ] && [ "$3" = --jq ] \
      || die 'unsupported required checks arguments'
    FILTER=$4
  fi
fi
METADATA_QUERY=$(cat <<'GRAPHQL'
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) { pullRequest(number:$number) {
    id headRefOid headRefName
    commits(last:1) { nodes { commit { oid statusCheckRollup { id } } } }
  } }
}
GRAPHQL
)
read_identity() {
  local response
  response=$(gh api graphql --hostname github.com -f "query=$METADATA_QUERY" \
    -f "owner=${REPO%/*}" -f "repo=$REPO_NAME" -F "number=$FM_PR_NUMBER") || return 1
  printf '%s' "$response" | jq -ce '
    def nonempty: type == "string" and length > 0;
    if type != "object" or (has("errors") and ((.errors | type) != "array" or (.errors | length) > 0))
    then error("invalid metadata response or GraphQL errors") else .data.repository.pullRequest end
    | if type == "object" and (.id | nonempty) and (.headRefOid | nonempty)
        and (.headRefName | nonempty) and (.commits.nodes | type) == "array" and (.commits.nodes | length) == 1
      then . else error("incomplete pull-request identity") end
    | . as $pr | .commits.nodes[0].commit
    | if type != "object" or .oid != $pr.headRefOid or (has("statusCheckRollup") | not)
      then error("commit head mismatch or missing rollup metadata") else . end
    | if .statusCheckRollup == null or ((.statusCheckRollup | type) == "object" and (.statusCheckRollup.id | nonempty))
      then {id:$pr.id,head:$pr.headRefOid,branch:$pr.headRefName,rollup:(.statusCheckRollup.id // null)}
      else error("invalid rollup identity") end
  '
}
IDENTITY=$(read_identity) || die 'could not read pull-request and rollup identity with normal login'
HEAD=$(printf '%s' "$IDENTITY" | jq -r '.head')
fm_pr_head_valid "$HEAD" || die 'invalid pull-request head'
PR_ID=$(printf '%s' "$IDENTITY" | jq -r '.id')
ROLLUP_ID=$(printf '%s' "$IDENTITY" | jq -r '.rollup // empty')
CONTEXTS='[]'
if [ -n "$ROLLUP_ID" ]; then
  REQUIRED=false
  [ "$MODE" != checks ] || REQUIRED=true
  QUERY=$(cat <<'GRAPHQL'
query($id:ID!, $prID:ID!, $required:Boolean!, $endCursor:String) {
  node(id:$id) { ... on StatusCheckRollup {
    id __typename
    contexts(first:100, after:$endCursor) {
      nodes {
        __typename
        ... on CheckRun {
          name status conclusion startedAt completedAt detailsUrl
          isRequired(pullRequestId:$prID) @include(if:$required)
          checkSuite @include(if:$required) { workflowRun { event workflow { name } } }
        }
        ... on StatusContext {
          context state createdAt targetUrl
          isRequired(pullRequestId:$prID) @include(if:$required)
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  } }
}
GRAPHQL
)
  PAGES=$(GH_TOKEN="$TOKEN" gh api graphql --hostname github.com --paginate --slurp \
    -f "query=$QUERY" -f "id=$ROLLUP_ID" -f "prID=$PR_ID" -F "required=$REQUIRED") \
    || die 'could not read rollup check contexts'
  CONTEXTS=$(printf '%s' "$PAGES" | jq -ce --arg rollup "$ROLLUP_ID" --argjson required "$REQUIRED" '
    def text: type == "string";
    def nullable_text: . == null or text;
    def grouping_valid:
      has("checkSuite") and
      (.checkSuite == null or
        (.checkSuite | type == "object" and has("workflowRun") and
          (.workflowRun == null or
            (.workflowRun | type == "object" and (.event | text) and has("workflow") and
              (.workflow == null or (.workflow | type == "object" and (.name | text)))))));
    def context_valid:
      type == "object" and
      (if .__typename == "CheckRun" then
         (.name | text) and (.status | text and length > 0)
         and has("conclusion") and (.conclusion | nullable_text)
         and (.status != "COMPLETED" or (.conclusion | text and length > 0))
         and has("startedAt") and (.startedAt | nullable_text)
         and has("completedAt") and (.completedAt | nullable_text)
         and ($required == false or grouping_valid)
       elif .__typename == "StatusContext" then
         (.context | text) and (.state | text and length > 0)
       else false end)
      and ($required == false or ((.isRequired | type) == "boolean"));
    if type != "array" or length == 0 then error("missing checks pages") else . end
    | map(
        if type != "object" or (has("errors") and ((.errors | type) != "array" or (.errors | length) > 0))
        then error("invalid checks response or GraphQL errors") else .data.node end
        | if type == "object" and .id == $rollup and .__typename == "StatusCheckRollup"
            and (.contexts | type) == "object" and (.contexts.nodes | type) == "array"
            and all(.contexts.nodes[]; context_valid)
            and (.contexts.pageInfo.hasNextPage | type) == "boolean"
            and (.contexts.pageInfo.endCursor | nullable_text)
            and (.contexts.pageInfo.hasNextPage == false or (.contexts.pageInfo.endCursor | text and length > 0))
          then .contexts else error("invalid rollup identity, contexts or pagination") end)
    | if .[-1].pageInfo.hasNextPage == false and all(.[:-1][]; .pageInfo.hasNextPage == true)
      then [.[].nodes[]] else error("incomplete checks pagination") end
  ') || die 'invalid rollup check contexts'
fi
AFTER=$(read_identity) || die 'could not reread pull-request and rollup identity with normal login'
[ "$IDENTITY" = "$AFTER" ] || die 'pull-request head or rollup changed during checks read'
if [ "$MODE" = view ]; then
  jq -cn --arg head "$HEAD" --arg fields "$VIEW_FIELDS" --argjson checks "$CONTEXTS" '
    {statusCheckRollup:$checks} + (if $fields == "headRefOid,statusCheckRollup" then {headRefOid:$head} else {} end)'
else
  CHECKS=$(printf '%s' "$CONTEXTS" | jq -ce '
    def started_at:
      if .startedAt == null then [0,0]
      else .startedAt
        | (capture("^(?<second>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\\.(?<fraction>[0-9]{1,9}))?Z$")
            // error("invalid check start time"))
        | [(.second + "Z" | fromdateiso8601), ("0." + (.fraction // "0") | tonumber)]
      end;
    to_entries | sort_by([(.value | started_at), -.key]) | reverse | map(.value)
    | reduce .[] as $check ({seen:{},checks:[]};
        ($check | if .__typename == "StatusContext" then [.__typename,.context]
          else [.__typename, ([.name, (.checkSuite.workflowRun.workflow.name // ""),
            (.checkSuite.workflowRun.event // "")] | join("/"))] end | tojson) as $key
        | if .seen[$key] then . else .seen[$key] = true | .checks += [$check] end)
    | [.checks[] | select(.isRequired)
        | {name:(.name // .context),state:(.state // (if .status == "COMPLETED" then .conclusion else .status end))}
        | . + {bucket:(if .state == "SUCCESS" then "pass"
            elif .state == "SKIPPED" or .state == "NEUTRAL" then "skipping"
            elif .state == "ERROR" or .state == "FAILURE" or .state == "TIMED_OUT" or .state == "ACTION_REQUIRED" then "fail"
            elif .state == "CANCELLED" then "cancel" else "pending" end)} ]
  ') || die 'invalid required checks response'
  if [ "$CONTEXTS" = '[]' ] || [ "$CHECKS" = '[]' ]; then
    LABEL='no required checks'
    [ "$CONTEXTS" != '[]' ] || LABEL='no checks'
    printf "%s reported on the '%s' branch\n" "$LABEL" "$(printf '%s' "$IDENTITY" | jq -r '.branch')" >&2
    exit 1
  fi
  printf '%s' "$CHECKS" | jq -r "$FILTER"
fi
