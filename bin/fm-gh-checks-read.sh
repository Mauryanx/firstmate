#!/usr/bin/env bash
# Run a gh checks read, optionally using a read-only GitHub App installation.
# Usage: fm-gh-checks-read.sh pr <view|list|checks> <ordinary gh arguments...>
# Only these read commands are accepted; writes always use the normal gh login.
# Opt-in: $FM_HOME/config/gh-checks-app.json with positive decimal string fields
# app_id, installation_id, and an absolute key_path. The key must be a regular,
# nonsymlink file owned by this uid with mode 600 or 400. Missing config executes
# gh unchanged; invalid config or failed App authentication refuses the read.
# Configured reads require the existing curl, jq, and openssl tools. JWTs use
# RS256, iat=now-60 and exp=now+540. The installation request explicitly narrows
# permissions to checks/statuses/metadata read on github.com. Tokens are never stored or
# cached: each read mints a fresh token, rejects expired responses, and retries
# one mint on expiry. Secrets reach curl via stdin and gh only via GH_TOKEN,
# never argv. Shell tracing and gh/curl debug output are disabled for secrets.
set -eu
case $- in *x*) set +x ;; esac
set -o pipefail

[ "$#" -ge 2 ] && [ "$1" = pr ] || exit 2
case "$2" in view|list|checks) ;; *) exit 2 ;; esac
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

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
# Avoid inheriting debug switches that could emit authentication headers.
unset GH_DEBUG
export GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1
export -n TOKEN JWT RESPONSE SIGNATURE
TOKEN=
for attempt in 1 2; do
  NOW=$(date +%s)
  HEADER=$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url)
  PAYLOAD=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$((NOW - 60))" "$((NOW + 540))" "$APP_ID" | b64url)
  SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$KEY_PATH" 2>/dev/null | b64url) \
    || die 'could not sign App authentication'
  JWT="$HEADER.$PAYLOAD.$SIGNATURE"
  # -q is first to ignore ~/.curlrc (including trace/header/output options).
  RESPONSE=$(printf 'header = "Authorization: Bearer %s"\n' "$JWT" | \
    curl -q --config - --silent --fail --connect-timeout 10 --max-time 30 \
      --request POST --header 'Accept: application/vnd.github+json' \
      --header 'Content-Type: application/json' \
      --data '{"permissions":{"checks":"read","statuses":"read","metadata":"read"}}' \
      "https://api.github.com/app/installations/$INSTALLATION_ID/access_tokens" 2>/dev/null) \
    || die 'could not mint installation token'
  unset JWT SIGNATURE
  TOKEN=$(printf '%s' "$RESPONSE" | jq -er '
    if (.token | type == "string" and test("^[A-Za-z0-9_]+$"))
      and (.expires_at | type == "string")
    then .token else error("invalid installation token") end
  ' 2>/dev/null) || die 'invalid installation token response'
  EXPIRES=$(printf '%s' "$RESPONSE" | jq -er '.expires_at | fromdateiso8601' 2>/dev/null) \
    || die 'invalid installation token expiry'
  unset RESPONSE
  NOW=$(date +%s)
  if [ "$EXPIRES" -gt "$((NOW + 60))" ]; then
    break
  fi
  TOKEN=
done
[ -n "$TOKEN" ] || die 'installation token expired'
GH_HOST=github.com GH_TOKEN="$TOKEN" gh "$@"
