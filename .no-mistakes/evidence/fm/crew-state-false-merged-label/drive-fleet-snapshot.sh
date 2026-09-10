#!/usr/bin/env bash
# Live driver: the captain-facing fleet snapshot composition. Runs the REAL
# bin/fm-fleet-snapshot.sh over a real fixture home holding one crew whose
# no-mistakes run concluded while its PR is still open on the forge.
set -u
BIN=${BIN:?}
HOME_DIR=$(mktemp -d /tmp/nm-snapshot.XXXXXX)
export GIT_CONFIG_GLOBAL=$HOME_DIR/gitconfig
git config --file "$GIT_CONFIG_GLOBAL" user.name fmtest
git config --file "$GIT_CONFIG_GLOBAL" user.email fmtest@example.invalid
git config --file "$GIT_CONFIG_GLOBAL" init.defaultBranch main
mkdir -p "$HOME_DIR"/{state,data,config,projects}

fakebin=$HOME_DIR/fakebin; mkdir -p "$fakebin"
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
  daemon) printf 'daemon running (pid 4242)\n'; exit 0 ;;
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

wt=$HOME_DIR/wt-feat-open
mkdir -p "$wt"
git -C "$wt" init -q
git -C "$wt" commit -q --allow-empty -m init
git -C "$wt" checkout -q -b fm/feat-open
head=$(git -C "$wt" rev-parse HEAD)
printf 'window=fm:fm-feat-open\nworktree=%s\nkind=ship\n' "$wt" > "$HOME_DIR/state/feat-open.meta"

cat > "$HOME_DIR/data/backlog.md" <<'MD'
# Backlog

## In flight
- [ ] feat-open - ship the widget

## Queued

## Done
MD

export FM_FAKE_AXI_STATUS="run:
  id: \"01RUN\"
  branch: fm/feat-open
  status: completed
  head: \"$head\"
  pr: \"https://github.com/o/r/pull/1\"
  findings: none
outcome: passed"
export FM_FAKE_AXI_STATUS_RUN="" FM_FAKE_RUNS_LIST="" FM_FAKE_CI_LOGS=""

echo "\$ FM_HOME=<fixture> bin/fm-fleet-snapshot.sh --json | jq '.tasks[] | {id, current_state}'"
PATH="$fakebin:$PATH" FM_HOME="$HOME_DIR" "$BIN/fm-fleet-snapshot.sh" --json 2>/dev/null \
  | jq '.tasks[] | {id, current_state}'
