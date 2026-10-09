"""Drive pickup's real CLI under read-only courier mounts; oracle is the requested ownership boundary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

os.umask(0o077)
root, case, evidence, owner = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
home, courier = case / 'home', case / 'courier'
inbound, receipts, outbox = [courier / 'srv/courier' / n for n in ('inbound', 'inbox', 'outbox')]
for d in (home / 'state', inbound, receipts, outbox, courier / 'etc/courier', courier / 'secrets', case / 'tmp'):
    d.mkdir(parents=True, exist_ok=True)
(home / 'state/.lock').write_text(owner + '\n')
(home / 'state/.lock').chmod(0o600)
credential = courier / 'secrets/linq-token'
credential.write_text('DISPOSABLE-COURIER-CREDENTIAL-NEVER-READ')
credential.chmod(0o600)
policy = courier / 'etc/courier/policy.toml'
policy.write_text('version=1\ndefault="deny"\n[captain]\nto="+12025550101"\n')
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env.update(FM_HOME=str(home), FM_NOTIFY_COURIER='1', FM_COURIER_ROOT=str(courier),
           FM_COURIER_USER='root', PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(case / 'tmp'))
cli = root / 'bin/fm-inbox.sh'
pickup = root / 'bin/fm-courier-pickup.py'

def call(command, payload):
    r = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload), env=env,
                       text=True, capture_output=True, timeout=30)
    assert r.returncode == 0, (command, r.returncode, r.stderr)
    return json.loads(r.stdout)

call('pilot-init', {'publication_policy': 'owner-authored-v2', 'destinations': ['imessage']})
binding = call('bind', {'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'imessage'})
(home / 'state/imessage').mkdir()
(home / 'state/imessage/binding.json').write_text(json.dumps(dict(binding, destination='imessage')))
sequence = 0

def record(kind, key, fields):
    global sequence
    sequence += 1
    fields.update(kind='courier-inbound', version=1, seq=sequence, type=kind,
                  published_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    path = inbound / ('%012d-%s-%s.json' % (sequence, kind, key))
    path.write_text(json.dumps(fields))
    path.chmod(0o640)
    return path

def message(key, text):
    return record('message', key, dict(message_id=key, chat_id='isolated', created_at='2026-10-09T01:00:00Z',
                                      transcript=text, attachments=[], other_parts=0))

def snapshot(directory):
    return {p.name: (p.stat().st_ino, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in directory.iterdir()}

n = 0

def once(extra=None):
    global n
    n += 1
    before = (snapshot(inbound), snapshot(receipts), credential.read_bytes())
    trace = evidence / ('boundary-trace-%d.log' % n)
    command = ['bwrap', '--unshare-user', '--uid', '0', '--gid', '0', '--ro-bind', '/', '/', '--dev', '/dev',
               '--bind', str(home), str(home), '--bind', str(outbox), str(outbox),
               '--bind', str(case / 'tmp'), str(case / 'tmp'), '--bind', str(evidence), str(evidence),
               '--tmpfs', str(credential.parent), '--', 'strace', '-f', '-e', 'trace=%file',
               '-o', str(trace), sys.executable, str(pickup), 'once']
    r = subprocess.run(command, env=dict(env, **(extra or {})), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert before == (snapshot(inbound), snapshot(receipts), credential.read_bytes())
    trace_data = trace.read_text()
    assert str(credential) not in trace_data and str(credential.parent) not in trace_data
    assert 'EROFS' not in trace_data, r.stderr
    print('pickup once #%d stderr: %s' % (n, r.stderr.strip()))
    return r

message('isolated-1', 'Please ask before proceeding.')
once()
assert len(list((home / 'state/inbox').glob('vc-*.note'))) == 1
assert (home / 'state/voice-conversation/policy.json').stat().st_mode & 0o777 == 0o600
assert (home / 'state/.lock').stat().st_mode & 0o777 == 0o600
print('OBSERVED: owner capture succeeds with 0600 session lock and transport policy; courier inbound and receipts are read-only mounts; credential is hidden.')
call('accept', {'conversation_id': 'text'})
call('publish', dict(conversation_id='text', request_id='imsg-req-isolated-1', response_id='isolated-question',
                     sequence=1, kind='question', final=False, question_binding='isolated-binding',
                     destination='imessage', speech_text='Proceed?\n1. Yes\n2. No'))
once()
texts = [json.loads(p.read_text()) for p in outbox.glob('*.json') if not p.name.endswith('.stage.json')]
[question] = texts
assert question['to'] == '+12025550101' and question['poll_options'] == ['1. Yes', '2. No']
print('OUTBOX QUESTION: ' + json.dumps(question, sort_keys=True))
(outbox / (question['id'] + '.json')).unlink()
receipt = receipts / ('result-' + question['id'] + '.boundary.json')
receipt.write_text(json.dumps(dict(kind='courier-result', id=question['id'], result='sent')))
receipt.chmod(0o640)
record('vote', 'isolated-vote', dict(chosen=['1. Yes'], request_id=question['id'], digest='disposable', poll_message_id='poll-1'))
once()
answer = call('accept', {'conversation_id': 'text'})['input']
assert answer['committed_transcript'] == '1. Yes' and answer['question_binding'] == 'isolated-binding'
call('publish', dict(conversation_id='text', request_id=answer['request_id'], response_id='isolated-final',
                     sequence=1, kind='answer', final=True, destination='imessage', speech_text='Proceeding.'))
once()
texts = [json.loads(p.read_text()) for p in outbox.glob('*.json') if not p.name.endswith('.stage.json')]
[final] = texts
print('OUTBOX ANSWER: ' + json.dumps(final, sort_keys=True))
(outbox / (final['id'] + '.json')).unlink()
receipt = receipts / ('result-' + final['id'] + '.boundary.json')
receipt.write_text(json.dumps(dict(kind='courier-result', id=final['id'], result='sent')))
receipt.chmod(0o640)
once()
stages = [json.loads(p.read_text()) for p in sorted(outbox.glob('*.stage.json'))]
assert [(r['message_id'], r['stage']) for r in stages] == [('isolated-1', s) for s in ('filed', 'working', 'question', 'done')]
assert all(p.stat().st_uid == os.getuid() and p.stat().st_mode & 0o777 == 0o640 for p in outbox.iterdir())
print('PUBLISHED REACTIONS: ' + json.dumps(stages, sort_keys=True))
print('OBSERVED: bound vote completes its original message; all requests are Firstmate-owned 0640; spool, receipts and credential unchanged through five real pickup processes.')

# The public input-owner contract must reject an otherwise valid record from another UID.
message('wrong-owner', 'This record must not reach the captain conversation.')
r = once({'FM_COURIER_USER': 'nobody'})
assert 'untrusted file metadata' in r.stderr
assert len(call('audit', {'conversation_id': 'text'})['requests']) == 2
print('OBSERVED: a valid record with the wrong owner is refused, with no new conversation request.')

# A policy writable by peers must not be used for outbound addressing.
message('policy-test', 'Please answer this.')
once()
call('accept', {'conversation_id': 'text'})
call('publish', dict(conversation_id='text', request_id='imsg-req-policy-test', response_id='policy-answer',
                     sequence=1, kind='answer', final=True, destination='imessage', speech_text='Policy must protect this address.'))
policy.chmod(0o666)
r = once()
assert 'courier policy is not root-owned and protected' in r.stderr
assert not any(p.name.endswith('.json') and not p.name.endswith('.stage.json') for p in outbox.iterdir())
print('OBSERVED: a peer-writable courier policy prevents outbound publication.')
print('PASS: sandboxed pickup completes without courier writes or credential reads; wrong-owner inbound and peer-writable policy are refused.')
