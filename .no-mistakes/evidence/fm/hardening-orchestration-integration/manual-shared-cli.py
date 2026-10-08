import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

root, scratch, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env['PYTHONDONTWRITEBYTECODE'] = '1'
cli = root / 'bin/fm-inbox.sh'
try:
    os.kill(1, 0)
except PermissionError:
    pass
else:
    raise RuntimeError('PID 1 must be live and unsignalable for this regression')

def invoke(home, verb, payload, expected=0):
    process = subprocess.Popen([str(cli), 'conversation', verb], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(env, FM_HOME=str(home)), text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(json.dumps(payload), timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
        raise AssertionError('conversation ' + verb + ' did not return promptly') from None
    p = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    assert p.returncode == expected, (verb, p.returncode, p.stdout, p.stderr)
    return json.loads(p.stdout) if expected == 0 else p

def setup(name):
    home = scratch / name
    invoke(home, 'lab-init', {'speech_catalog': {'answer': 'Synthetic answer.'}})
    (home / 'state/.lock').write_text(owner + '\n')
    stage = home / '.fm-voice-shared-interface'
    stage.mkdir(mode=0o750)
    for name, mode in [('conversation', 0o660), ('note', 0o640), ('wake', 0o660), ('wake-lock', 0o770)]:
        p = stage / name
        p.write_text('metadata only\n')
        p.chmod(mode)
    connection = invoke(home, 'bind', {'conversation_id': 'manual', 'authenticated_principal': 'captain'})
    event = dict(connection, turn_id='t1', request_id='r1', committed_transcript='Synthetic permission probe',
                 revision=1, previous_turn_id=None, created_at='2026-10-08T00:00:00Z')
    return home, event

def held_capture(home, event):
    p = subprocess.Popen([str(cli), 'conversation', 'capture'], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=dict(env, FM_HOME=str(home)), start_new_session=True)
    try:
        output = p.communicate(json.dumps(event), timeout=2)
        raise AssertionError(('capture reclaimed a live unsignalable lock', p.returncode, output))
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGTERM)
        p.communicate(timeout=5)

for family in (() if len(sys.argv) > 4 and sys.argv[4] == 'staging' else ('.wake-queue.lock', '.watcher-down.lock')):
    for depth in (0, 2):
        home, event = setup(f'live-{family[1:]}-{depth}')
        state = home / 'state'
        paths = []
        for level in range(depth + 1):
            name = family + '.steal' * level
            lockowner = state / (name + '.owner.LIVE')
            lockowner.mkdir(mode=0o770)
            (lockowner / 'pid').write_text(('1' if level == depth else '99999999') + '\n')
            (lockowner / 'pid').chmod(0o660)
            os.utime(lockowner, (1, 1))
            lock = state / name
            lock.symlink_to(lockowner)
            paths.append((lock, lockowner))
        held_capture(home, event)
        for lock, lockowner in paths:
            assert lock.readlink() == lockowner
        assert (paths[-1][1] / 'pid').read_text() == '1\n'
        journal = json.loads((state / 'voice-conversation/journal.json').read_text())
        assert list(journal['requests'].values())[0]['state'] == 'saved'
        print(f'LIVE: capture preserves live EPERM owner at {family}' + '.steal' * depth + '; request stays saved')
        for lock, lockowner in reversed(paths):
            lock.unlink()
            shutil.rmtree(lockowner)
        assert invoke(home, 'capture', event)['state'] == 'saved'
        assert 'vc-' in (state / '.wake-queue').read_text()
        print(f'LIVE: retry after releasing {family} queues the same saved turn')

for template in ('wake', 'wake-lock'):
    for invalid in ('missing', 'mode'):
        home, event = setup(f'invalid-{template}-{invalid}')
        alias = home.with_name(home.name + '-link')
        alias.symlink_to(home, target_is_directory=True)
        home = alias
        path = home / '.fm-voice-shared-interface' / template
        if invalid == 'missing':
            path.unlink()
        else:
            path.chmod(0o600)
        start = time.monotonic()
        p = invoke(home, 'capture', event, expected=2)
        assert time.monotonic() - start < 5
        assert 'input saved; wake failed; retry capture' in p.stderr
        journal = json.loads((home / 'state/voice-conversation/journal.json').read_text())
        assert list(journal['requests'].values())[0]['state'] == 'saved'
        print(f'LIVE: capture via symlinked FM_HOME refuses {invalid} {template} template promptly; turn stays saved')
        path.write_text('metadata only\n')
        path.chmod(0o770 if template == 'wake-lock' else 0o660)
        assert invoke(home, 'capture', event)['state'] == 'saved'
        assert 'vc-' in (home / 'state/.wake-queue').read_text()
        print(f'LIVE: repaired {template} template lets capture retry enqueue the saved turn')
