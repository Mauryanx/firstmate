#!/usr/bin/env bash
# fm-voice-pending.sh - captain-message turn-start presenter for pending voice
# notes in a firstmate PRIMARY session, wired as the Claude primary's
# UserPromptSubmit hook (.claude/settings.json).
#
# Why it exists: a captain voice note reaches firstmate as a durable wake row
# keyed by fm-wake-lib.sh's FM_WAKE_VOICE_KEY_PATTERN, and bin/fm-wake-drain.sh
# presents it first at session start and at every wake-handling turn. A
# captain-message turn never runs the drain. On 2026-09-10 eleven spoken turns
# sat queued behind a captain text message that pulled firstmate onto other
# work, and none were answered. This hook runs at the start of every Claude
# captain-message turn and prints the pending voice rows under the same VOICE
# heading the drain prints, so the turn sees them before its first work; Claude
# Code adds a UserPromptSubmit hook's stdout to the turn's context.
#
# Read-only, presentation-only contract: this script never takes the queue
# lock, never drains, never acknowledges, never claims rows, never writes any
# file under state/, and never touches the recovery marker. The drain and its
# generation-bound --ack-through remain the only mutation and acknowledgement
# path. A torn read of the queue can mis-present at most one turn, and the next
# drain re-presents the row - the same reasoning fm_wake_actor_pending_count in
# bin/fm-wake-lib.sh gives for counting without the lock. One awk pass over
# the queue bounds the cost.
#
# Every failure path exits 0 silently: this hook must never block or fail a
# captain turn. Only the Claude primary has a turn-start hook point wired here;
# other primary harnesses present voice notes at session start and at
# wake-handling turns through the drain.
#
# Ships as a TRACKED hook target, so it is checked out into every worktree of
# this repo. It scopes itself to a genuine primary checkout - the main home or
# a marked secondmate home - through bin/fm-primary-scope-lib.sh and stays a
# silent no-op in child crew and scout worktrees, and it stands down on a
# Cursor-delivered payload through bin/fm-hook-host-lib.sh because Cursor loads
# this repo's .claude/settings.json beside its own registration.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-primary-scope-lib.sh
. "$SCRIPT_DIR/fm-primary-scope-lib.sh"
# shellcheck source=bin/fm-hook-host-lib.sh
. "$SCRIPT_DIR/fm-hook-host-lib.sh"

PAYLOAD=$(cat 2>/dev/null || true)
if fm_hook_payload_is_foreign_host "$PAYLOAD"; then
  exit 0
fi
fm_primary_scope_matches "$FM_ROOT" "$STATE" || exit 0

QUEUE="$STATE/.wake-queue"
{ [ -f "$QUEUE" ] && [ -r "$QUEUE" ]; } || exit 0

# shellcheck source=bin/fm-wake-lib.sh
. "$SCRIPT_DIR/fm-wake-lib.sh"

VOICE_VIEW=$(fm_wake_voice_rows "$QUEUE" 2>/dev/null) || exit 0
[ -n "$VOICE_VIEW" ] || exit 0
fm_wake_voice_heading "$(printf '%s\n' "$VOICE_VIEW" | awk 'END { print NR }')"
printf '%s\n' "$VOICE_VIEW"
exit 0
