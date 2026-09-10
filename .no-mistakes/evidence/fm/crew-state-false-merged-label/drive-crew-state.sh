#!/usr/bin/env bash
# Live driver: stand up a real firstmate crew fixture (real git worktree, real
# state/<id>.meta, real fake-CLI on PATH exactly as a live crew would have) and
# run the REAL bin/fm-crew-state.sh against it, the way the captain does.
set -u

BIN=${BIN:?set BIN to the bin/ dir under test}
ROOTDIR=$(mktemp -d /tmp/nm-crewstate.XXXXXX)
export GIT_CONFIG_GLOBAL=$ROOTDIR/gitconfig
git config --file "$ROOTDIR/gitconfig" user.name fmtest
git config --file "$ROOTDIR/gitconfig" user.email fmtest@example.invalid
git config --file "$ROOTDIR/gitconfig" init.defaultBranch main

fakebin=$ROOTDIR/fakebin; mkdir -p "$fakebin"
cat > "$fakebin/no-mistakes" <<'SH'
#!/usr/bin/env bash
set -u
case "${1:-}" in
  axi) shift
    case "${1:-}" in
      status) shift
        if [ "${1:-}" = --run ]; then printf '%s\n' "${FM_FAKE_AXI_STATUS_RUN:-}"
        else printf '%s\n' "${FM_FAKE_AXI_STATUS:-}"; fi ;;
      logs) printf '%s\n' "${FM_FAKE_CI_LOGS:-}" ;;
    esac ;;
  runs) printf '%s\n' "${FM_FAKE_RUNS_LIST:-}" ;;
  daemon) printf '%s\n' 'daemon running (pid 4242)'; exit 0 ;;
esac
exit 0
SH
cat > "$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -u
case "${1:-}" in
  display-message) printf '%%1\n' ;;
  capture-pane) printf 'all quiet\n> \n' ;;
esac
exit 0
SH
chmod +x "$fakebin/no-mistakes" "$fakebin/tmux"

# One crew: real repo checked out on its branch, meta pointing at it.
mk_crew() {  # <id> <branch>
  local id=$1 br=$2 wt
  wt="$ROOTDIR/wt-$id"
  mkdir -p "$wt" "$ROOTDIR/state"
  git -C "$wt" init -q
  git -C "$wt" commit -q --allow-empty -m init
  git -C "$wt" checkout -q -b "$br"
  printf 'window=fm:fm-%s\nworktree=%s\nkind=ship\n' "$id" "$wt" > "$ROOTDIR/state/$id.meta"
  git -C "$wt" rev-parse HEAD
}

read_state() {  # <id>
  PATH="$fakebin:$PATH" FM_STATE_OVERRIDE="$ROOTDIR/state" "$BIN/fm-crew-state.sh" "$1"
}

banner() { printf '\n========== %s ==========\n' "$*"; }

# ---------- S1: concluded run, PR still OPEN on the forge ----------
banner "S1  concluded validation run, PR #1 still OPEN, default branch untouched"
HEAD1=$(mk_crew feat-open fm/feat-open)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-open
  status: completed
  head: \"$HEAD1\"
  pr: \"https://github.com/o/r/pull/1\"
  findings: none
outcome: passed"
export FM_FAKE_AXI_STATUS_RUN="" FM_FAKE_RUNS_LIST="" FM_FAKE_CI_LOGS=""
echo "\$ bin/fm-crew-state.sh feat-open"
read_state feat-open

# ---------- S2: concluded run that produced NO PR at all ----------
banner "S2  concluded run carrying no PR URL (pr: empty)"
HEAD2=$(mk_crew feat-nopr fm/feat-nopr)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-nopr
  status: completed
  head: \"$HEAD2\"
  pr: \"\"
  findings: none
outcome: passed"
echo "\$ bin/fm-crew-state.sh feat-nopr"
read_state feat-nopr

# ---------- S2b: concluded run whose record omits the pr field entirely ----------
banner "S2b concluded run whose record has no pr field at all"
HEAD2B=$(mk_crew feat-noprfield fm/feat-noprfield)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-noprfield
  status: completed
  head: \"$HEAD2B\"
  findings: none
outcome: passed"
echo "\$ bin/fm-crew-state.sh feat-noprfield"
read_state feat-noprfield

# ---------- S3: sibling held-green path still surfaces its PR ----------
banner "S3  terminal failed run, only ci monitor failed, checks green (held-for-merge)"
HEAD3=$(mk_crew feat-held fm/feat-held)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-held
  status: completed
  head: \"$HEAD3\"
  pr: \"https://github.com/o/r/pull/7\"
  findings: none
  steps[6]{step,status,findings,duration_ms}:
    intent,completed,0,0
    review,completed,0,0
    test,completed,0,0
    lint,completed,0,0
    push,completed,0,0
    ci,failed,0,0
outcome: failed"
export FM_FAKE_CI_LOGS="all CI checks passed - still monitoring until merged or closed"
echo "\$ bin/fm-crew-state.sh feat-held"
read_state feat-held
export FM_FAKE_CI_LOGS=""

# ---------- S4: checks-passed outcome unchanged ----------
banner "S4  outcome=checks-passed (untouched sibling branch)"
HEAD4=$(mk_crew feat-cp fm/feat-cp)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-cp
  status: completed
  head: \"$HEAD4\"
  pr: \"https://github.com/o/r/pull/9\"
  findings: none
outcome: checks-passed"
echo "\$ bin/fm-crew-state.sh feat-cp"
read_state feat-cp

# ---------- S5: adversarial - a PR URL that itself contains 'merged'/'closed' words? ----------
banner "S5  adversarial: run passed with a branch/PR whose URL is long; assert no forge claim words"
HEAD5=$(mk_crew feat-adv fm/feat-adv)
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-adv
  status: completed
  head: \"$HEAD5\"
  pr: \"https://github.com/acme/widgets/pull/4242\"
  findings: none
outcome: passed"
echo "\$ bin/fm-crew-state.sh feat-adv"
read_state feat-adv

printf '\nfixture: %s\n' "$ROOTDIR"

# ---------- S6: adversarial - stale status-log event claiming the PR merged ----------
banner "S6  adversarial: concluded run + stale status-log event claiming 'PR merged'"
HEAD6=$(mk_crew feat-stalelog fm/feat-stalelog)
printf 'done: PR https://github.com/o/r/pull/3 merged and closed\n' > "$ROOTDIR/state/feat-stalelog.status"
export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-stalelog
  status: completed
  head: \"$HEAD6\"
  pr: \"https://github.com/o/r/pull/3\"
  findings: none
outcome: passed"
echo "\$ bin/fm-crew-state.sh feat-stalelog"
read_state feat-stalelog
