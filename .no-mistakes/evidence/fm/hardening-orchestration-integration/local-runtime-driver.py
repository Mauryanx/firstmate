"""Real daemon/tmux/CLI drive in disposable worktree homes, without agent/provider calls."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import signal
import subprocess
import sys
import time

root = Path(sys.argv[1])
evidence = Path(sys.argv[2])
work = root / 'state/scratchpad-validation'
socket = root / 'state/v.sock'
base_env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k not in ('STATE', 'TMUX', 'TMUX_PANE', 'PYTHONPATH')}
base_env.update(TMPDIR=str(work), PYTHONDONTWRITEBYTECODE='1')
fakebin = work / 'tmuxbin'
fakebin.mkdir(exist_ok=True)
wrapper = fakebin / 'tmux'
wrapper.write_text('#!/bin/sh\nexec /usr/bin/tmux -S ' + shlex.quote(str(socket)) + ' "$@"\n')
wrapper.chmod(0o750)
base_env['PATH'] = str(fakebin) + ':' + base_env['PATH']

def canonical(row):
    return json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

subprocess.run(['/usr/bin/tmux', '-S', str(socket), '-f', '/dev/null', 'new-session', '-d', '-s', 'validation',
                'printf "LOCAL_VALIDATION_BUSY\\n"; sleep 180'], env=base_env, check=True)
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pane = subprocess.check_output(['/usr/bin/tmux', '-S', str(socket), 'capture-pane', '-p', '-t', 'validation:0'], env=base_env, text=True)
        if 'LOCAL_VALIDATION_BUSY' in pane:
            break
        time.sleep(0.05)
    assert 'LOCAL_VALIDATION_BUSY' in pane
    print('Real isolated tmux pane contains:', pane.strip(), flush=True)
    for name, setting, expected in [('pending', '1', 'courier delivery unconfirmed'),
                                    ('disabled', '0', 'notification failed (exit 3)'),
                                    ('timeout', '1', 'courier delivery failed (exit 124)'),
                                    ('off', '1', 'alarm marker written')]:
        home = work / ('daemon-' + name)
        home.mkdir(mode=0o700)
        for child in ('state', 'config', 'outbox', 'inbox'):
            (home / child).mkdir(mode=0o750)
        state = home / 'state'
        (state / '.afk').write_text(str(int(time.time())))
        (state / '.subsuper-escalations').write_text('Synthetic pending validation item\n')
        (state / '.subsuper-escalations.since').write_text(str(int(time.time()) - 601))
        env = dict(base_env, FM_HOME=str(home), FM_ROOT_OVERRIDE=str(home), FM_STATE_OVERRIDE=str(state),
                   FM_CONFIG_OVERRIDE=str(home / 'config'), FM_SUPERVISOR_BACKEND='tmux', FM_SUPERVISOR_TARGET='validation:0',
                   FM_BUSY_REGEX='LOCAL_VALIDATION_BUSY', FM_HOUSEKEEPING_TICK='1', FM_MAX_DEFER_SECS='600',
                   FM_ESCALATE_BATCH_SECS='900', FM_POLL='1', FM_CHECK_INTERVAL='999999', FM_HEARTBEAT='999999',
                   FM_WEDGE_ALARM_CHANNEL='off' if name == 'off' else 'courier', FM_WEDGE_ALARM_TIMEOUT_SECS='1',
                   FM_COURIER_NOTIFY_TO='+12025550102', FM_COURIER_ENABLED=setting,
                   FM_COURIER_OUTBOX=str(home / 'outbox'), FM_COURIER_INBOX=str(home / 'inbox'),
                   FM_COURIER_RESULT_USER=pwd.getpwuid(os.getuid()).pw_name)
        if name == 'timeout':
            fault = home / 'fault'
            fault.mkdir()
            (fault / 'sitecustomize.py').write_text('''import os, time
from pathlib import Path
original = os.listdir
def delay(fd):
    if isinstance(fd, int):
        a, b = os.fstat(fd), os.stat(os.environ['FM_COURIER_INBOX'])
        if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
            Path(os.environ['FM_HOME'], 'scan-started').write_text('receipt scan delayed')
            time.sleep(30)
    return original(fd)
os.listdir = delay
''')
            env['PYTHONPATH'] = str(fault)
        output = (home / 'daemon-output.txt').open('w')
        daemon = subprocess.Popen([str(root / 'bin/fm-supervise-daemon.sh')], env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            log_path = state / '.supervise-daemon.log'
            while time.monotonic() < deadline:
                log = log_path.read_text() if log_path.exists() else ''
                if expected in log:
                    break
                if daemon.poll() is not None:
                    raise AssertionError((name, daemon.returncode, (home / 'daemon-output.txt').read_text(), log))
                time.sleep(0.1)
            assert expected in log, (name, log)
            assert (state / '.subsuper-inject-wedged').exists()
            assert (state / '.subsuper-escalations').read_text() == 'Synthetic pending validation item\n'
            proposals = list((home / 'outbox').glob('notify-*.json'))
            assert len(proposals) == (0 if name in ('disabled', 'off') else 1)
            (evidence / ('live-daemon-' + name + '.log')).write_text(log)
            print(name + ':\n' + log, flush=True)
            if name in ('pending', 'timeout'):
                identity = json.loads(next(line.split('recovery identity: ', 1)[1] for line in log.splitlines() if 'recovery identity: ' in line))
                proposal = json.loads(proposals[0].read_bytes())
                assert identity['id'] == proposal['id']
                assert identity['digest'] == hashlib.sha256(canonical({k: proposal[k] for k in ('channel', 'to', 'text', 'purpose', 'attachments')})).hexdigest()
                if name == 'timeout':
                    assert (home / 'scan-started').exists()
                    assert log.index('recovery identity: ') < log.index('notifier timed out')
                    # A synthetic immutable receipt exercises the Firstmate reader;
                    # it does not prove any courier approval/provider implementation.
                    proposals[0].unlink()
                    receipt = dict(kind='courier-result', id=identity['id'], digest=identity['digest'], result='sent',
                                   approval_ref=None, idempotency_key=identity['idempotency_key'], message_id='dummy-confirmed')
                    path = home / 'inbox' / ('result-' + hashlib.sha256(canonical(receipt)).hexdigest() + '.json')
                    path.write_bytes(canonical(receipt))
                    path.chmod(0o640)
                    recovery_env = dict(env)
                    recovery_env.pop('PYTHONPATH')
                    recovery = subprocess.run([sys.executable, str(root / 'bin/fm-courier.py'), 'delivery', identity['id'], identity['digest']],
                                              env=recovery_env, capture_output=True, text=True, timeout=10)
                    assert recovery.returncode == 0, recovery
                    assert json.loads(recovery.stdout)['delivered']
                    assert not list((home / 'outbox').glob('*.json'))
                    print('Recovery CLI after receipt watchdog: ' + recovery.stdout, flush=True)
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            finally:
                try:
                    os.killpg(daemon.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output.close()
    print('PASS: executed daemon publishes one proposal, preserves pending/error markers, logs identity before timeout and recovers without resubmission; disabled/off publish nothing', flush=True)
finally:
    subprocess.run(['/usr/bin/tmux', '-S', str(socket), 'kill-server'], env=base_env, capture_output=True)
