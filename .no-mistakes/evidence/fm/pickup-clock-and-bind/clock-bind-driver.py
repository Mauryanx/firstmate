"""Live CLI check: clock steps are process-local, all product paths are disposable.
Oracle: author intent requires cadence to ignore wall steps and raw bind JSON to
be pickup-ready. Existing CLI contract specifies 2 s polling and 5/20 s retries.
No product modules are imported or product methods called by this driver.
"""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

root, temp, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
cli = root / 'bin/fm-inbox.sh'
pickup = root / 'bin/fm-courier-pickup.py'
processes = []
failures = []


def setup(name):
    home, courier = temp / name / 'home', temp / name / 'courier'
    (home / 'state').mkdir(parents=True)
    (home / 'state/.lock').write_text(owner + '\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
    env.update(FM_HOME=str(home), FM_NOTIFY_COURIER='1', FM_COURIER_ROOT=str(courier),
               FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name, PYTHONDONTWRITEBYTECODE='1')
    for name in ('inbound', 'outbox', 'inbox'):
        (courier / 'srv/courier' / name).mkdir(parents=True)
    (courier / 'etc/courier').mkdir(parents=True)
    (courier / 'etc/courier/policy.toml').write_text(
        'version = 1\ndefault = "deny"\nteam = []\n[captain]\nname = "Captain"\nto = "+12025550101"\nchannels = ["imessage"]\n')
    (courier / 'etc/courier/policy.toml').chmod(0o600)
    call(env, 'pilot-init', {'publication_policy': 'owner-authored-v2', 'destinations': ['imessage', 'elevenlabs']})
    binding = call(env, 'bind', {'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'imessage'})
    (home / 'state/imessage').mkdir()
    # For cadence checks alone, repair only the known D2 baseline fixture gap.
    # The separate bind check saves the response untouched and permits no repair.
    (home / 'state/imessage/binding.json').write_text(json.dumps(dict(binding, destination='imessage')))
    return home, courier, env, binding


def call(env, command, payload, code=0):
    r = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload),
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == code, (command, r.returncode, r.stderr)
    return json.loads(r.stdout) if code == 0 else r


def once(env):
    r = subprocess.run([sys.executable, str(pickup), 'once'], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r


def message(courier, key='m1'):
    row = {'kind': 'courier-inbound', 'version': 1, 'seq': 1, 'type': 'message',
           'published_at': '2026-10-09T01:00:00Z', 'message_id': key, 'chat_id': 'chat-1',
           'created_at': '2026-10-09T01:00:00Z', 'transcript': 'Test the clock', 'attachments': [], 'other_parts': 0}
    path = courier / 'srv/courier/inbound' / ('000000000001-message-' + key + '.json')
    path.write_text(json.dumps(row))
    path.chmod(0o640)


def state(home):
    path = home / 'state/courier-pickup/state.json'
    return json.loads(path.read_text()) if path.exists() else None


def outgoing(courier):
    return [p for p in (courier / 'srv/courier/outbox').glob('*.json') if not p.name.endswith(('.stage.json', '.votes.json'))]


def wait_for(predicate, timeout, label):
    started = time.monotonic()
    while not predicate():
        assert time.monotonic() - started < timeout, label
        time.sleep(0.02)


def start(home, env):
    offset = home / 'wall-offset'
    offset.write_text('0')
    # Run the shipped executable's __main__ in its normal Python runtime. Change
    # only time.time's system-clock input; monotonic and sleep remain real.
    wrapper = ('import pathlib,runpy,sys,time; offset=pathlib.Path(sys.argv.pop(1)); '
               'wall=time.time; time.time=lambda: wall()+float(offset.read_text()); '
               'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
    err = open(home / 'pickup.stderr', 'w')
    p = subprocess.Popen([sys.executable, '-c', wrapper, str(offset), str(pickup), 'run'],
                         env=env, stdout=subprocess.DEVNULL, stderr=err)
    processes.append((p, err))
    return p, offset


def stop(p):
    p.terminate()
    p.wait(timeout=15)


def binding_case():
    home, courier, env, bound = setup('bind')
    print('bind output:', json.dumps(dict(bound, credential='[redacted]')), flush=True)
    # Write exactly the public response; this is the runbook's activation path.
    (home / 'state/imessage/binding.json').write_text(json.dumps(bound))
    message(courier)
    r = subprocess.run([sys.executable, str(pickup), 'once'], env=env, capture_output=True, text=True, timeout=30)
    print('pickup startup exit:', r.returncode, 'stderr:', r.stderr.strip(), flush=True)
    assert r.returncode == 0, 'raw bind response cannot start pickup'
    assert set(bound) == {'conversation_id', 'credential', 'destination'} and bound['destination'] == 'imessage'
    assert call(env, 'bind', {'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'imessage'}) == bound
    for payload in ({'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'elevenlabs'},
                    {'conversation_id': 'text', 'authenticated_principal': 'other', 'destination': 'imessage'}):
        denied = call(env, 'bind', payload, 2)
        print('rebind refused:', denied.stderr.strip(), flush=True)
    voice = call(env, 'bind', {'conversation_id': 'call', 'authenticated_principal': 'captain'})
    assert set(voice) == {'conversation_id', 'credential'}
    [request] = call(env, 'audit', {'conversation_id': 'text'})['requests']
    assert request['request_id'] == 'imsg-req-m1'
    print('PASS: raw iMessage bind JSON starts pickup and files imsg-req-m1; repeat bind is identical, principal/destination changes refused, default voice shape unchanged', flush=True)


def reply_case(step):
    home, courier, env, bound = setup('reply-' + str(step))
    message(courier)
    once(env)
    call(env, 'accept', {'conversation_id': 'text'})
    p, offset = start(home, env)
    try:
        # A completed initial tick proves it scheduled its next read before the
        # input clock moves. Start with an unpublished request so no old text exists.
        wait_for(lambda: state(home)['marks']['m1']['stage'] == 'working', 5, 'initial running tick')
        initial = time.monotonic()
        time.sleep(0.3)
        published = time.monotonic()
        call(env, 'publish', {'conversation_id': 'text', 'request_id': 'imsg-req-m1', 'response_id': 'clock-answer',
                             'sequence': 1, 'kind': 'answer', 'final': True, 'destination': 'imessage',
                             'speech_text': 'Reply despite clock step ' + str(step)})
        offset.write_text(str(step))
        wait_for(lambda: outgoing(courier), 7, 'clock step stalled reply beyond normal cadence')
        elapsed = time.monotonic() - initial
        [path] = outgoing(courier)
        row = json.loads(path.read_text())
        assert row['text'] == 'Reply despite clock step ' + str(step), row
        print('wall step=%+ds; reply after initial tick=%.3fs; after publication=%.3fs; outbox=%s' %
              (step, elapsed, time.monotonic() - published, json.dumps(row)), flush=True)
        assert 1.4 <= elapsed < 5, 'wall-clock step rushed or stalled 2 s cadence'
        path.unlink()
        receipt = courier / 'srv/courier/inbox' / ('result-' + row['id'] + '.json')
        receipt.write_text(json.dumps({'kind': 'courier-result', 'id': row['id'], 'digest': 'd', 'result': 'sent',
                                      'approval_ref': None, 'idempotency_key': row['id'], 'message_id': 'm'}))
        receipt.chmod(0o640)
        wait_for(lambda: state(home)['marks']['m1']['stage'] == 'done', 5, 'sent receipt not completed')
        print('PASS: running pickup keeps 2 s reply cadence through wall step %+ds and completes after sent receipt' % step, flush=True)
    finally:
        stop(p)


def retry_case(step):
    home, courier, env, bound = setup('retry-' + str(step))
    (home / 'state/.lock').write_text('99999999\n')
    message(courier)
    p, offset = start(home, env)
    try:
        wait_for(lambda: state(home) and state(home)['pending'] and state(home)['pending']['attempts'] == 1,
                 5, 'first capture failure')
        first = time.monotonic()
        offset.write_text(str(step))
        wait_for(lambda: state(home)['pending'] is None or state(home)['pending']['attempts'] >= 2,
                 8, 'clock step stalled first 5 s retry')
        second = time.monotonic()
        print('wall step=%+ds; first retry delay=%.3fs' % (step, second - first), flush=True)
        assert 4.8 <= second - first < 8, 'wall-clock step rushed or stalled 5 s retry'
        wait_for(lambda: outgoing(courier), 24, 'clock step stalled second 20 s retry')
        elapsed = time.monotonic() - second
        [path] = outgoing(courier)
        row = json.loads(path.read_text())
        print('second retry delay=%.3fs; notice=%s' % (elapsed, json.dumps(row)), flush=True)
        assert 19.8 <= elapsed < 24, 'wall-clock step rushed or stalled 20 s retry'
        assert row['text'] == "I couldn't reach Firstmate." and state(home)['pending'] is None
        print('PASS: capture retries retain 5/20 s backoff through wall step %+ds and publish one failure notice' % step, flush=True)
    finally:
        stop(p)


def restart_case():
    home, courier, env, bound = setup('restart')
    (home / 'state/.lock').write_text('99999999\n')
    message(courier)
    once(env)
    persisted = state(home)
    assert persisted['pending']['attempts'] == 1
    # Persisted state is an intentional public recovery contract. Simulate a
    # pre-upgrade process's wall deadline, hours ahead after the rollback.
    persisted['pending']['not_before'] = time.time() + 3600
    (home / 'state/courier-pickup/state.json').write_text(json.dumps(persisted))
    (home / 'state/.lock').write_text(owner + '\n')
    started = time.monotonic()
    once(env)
    assert state(home)['pending'] is None, 'legacy deadline blocked retry after restart'
    [request] = call(env, 'audit', {'conversation_id': 'text'})['requests']
    assert request['request_id'] == 'imsg-req-m1'
    print('PASS: restart retries legacy pending capture immediately (%.3fs), ignores stale wall deadline and files exactly imsg-req-m1' %
          (time.monotonic() - started), flush=True)


try:
    for name, action in [('raw-bind', binding_case), ('reply-backward', lambda: reply_case(-600)),
                         ('reply-forward', lambda: reply_case(600)), ('retry-backward', lambda: retry_case(-600)),
                         ('retry-forward', lambda: retry_case(600)), ('legacy-restart', restart_case)]:
        if len(sys.argv) > 4 and name not in sys.argv[4:]:
            continue
        try:
            action()
        except Exception as exc:
            failures.append(name)
            print('FAIL:', name, repr(exc), flush=True)
finally:
    for p, err in processes:
        if p.poll() is None:
            stop(p)
        err.close()
sys.exit(bool(failures))
