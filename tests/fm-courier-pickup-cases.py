"""Executable cases for bin/fm-courier-pickup.py, driven by fm-courier-pickup.test.sh, entirely offline.

The test process plays the courier: it publishes spool records and result
receipts the way the courier does, and reads the outbox requests Firstmate
publishes. Firstmate's side runs through its real CLI against a real pilot
conversation transport, so a record becomes a real vc- note and check wake.
"""
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys
import time

root, temp, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
home, courier = temp / 'home', temp / 'courier'
inbound, outbox, receipts = (courier / 'srv/courier' / name for name in ('inbound', 'outbox', 'inbox'))
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env.update(FM_HOME=str(home), FM_NOTIFY_COURIER='1', FM_COURIER_ROOT=str(courier),
           FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name)
cli, pickup = root / 'bin/fm-inbox.sh', root / 'bin/fm-courier-pickup.py'
CAPTAIN = '+12025550101'


def conversation(command, payload, code=0):
    result = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload), text=True,
                            capture_output=True, env=env, timeout=30)
    assert result.returncode == code, (command, result)
    return json.loads(result.stdout) if code == 0 else result


def once(code=0, extra=None):
    result = subprocess.run([sys.executable, str(pickup), 'once'], text=True, capture_output=True,
                            env=dict(env, **(extra or {})), timeout=60)
    assert result.returncode == code, result
    return result


seq = 0


def record(kind, key, fields, mode=0o640, seq_in_body=None, raw=None):
    """Publish one spool record the way the courier does: private temporary, mode, rename."""
    global seq
    seq += 1
    body = dict(fields, kind='courier-inbound', version=1, seq=seq if seq_in_body is None else seq_in_body,
                type=kind, published_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    data = raw if raw is not None else json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    name = '%012d-%s-%s.json' % (seq, kind, key)
    tmp = inbound / ('.tmp-' + secrets.token_hex(4))
    tmp.write_bytes(data)
    tmp.chmod(mode)
    os.rename(tmp, inbound / name)
    return name


def message(key, transcript='Ship the fix', attachments=(), other=0, **extra):
    return record('message', key, dict({'message_id': key, 'chat_id': 'chat-1', 'created_at': '2026-10-09T01:00:00Z',
                                        'transcript': transcript, 'attachments': list(attachments),
                                        'other_parts': other}, **extra))


def notes():
    return sorted(p.name for p in (home / 'state/inbox').glob('vc-*.note'))


def wakes():
    queue = home / 'state/.wake-queue'
    return [line for line in queue.read_text().splitlines() if '\tinbox:vc-' in line] if queue.exists() else []


def requests():
    return conversation('audit', {'conversation_id': 'text'})['requests']


def outgoing(suffix):
    found = {}
    for path in sorted(outbox.glob('*' + suffix)):
        if suffix == '.json' and path.name.endswith(('.stage.json', '.votes.json')):
            continue
        found[path.name] = json.loads(path.read_text())
    return found


def stages():
    return [(row['message_id'], row['stage']) for row in outgoing('.stage.json').values()]


def courier_takes():
    """Consume every published text request and publish its sent receipt, as the courier does."""
    sent = []
    for name, row in outgoing('.json').items():
        (outbox / name).unlink()
        receipt = receipts / ('result-%s.%s.json' % (row['id'], secrets.token_hex(4)))
        receipt.write_text(json.dumps({'kind': 'courier-result', 'id': row['id'], 'digest': 'd', 'result': 'sent',
                                       'approval_ref': None, 'idempotency_key': row['id'], 'message_id': 'm'}))
        receipt.chmod(0o640)
        sent.append(row)
    return sent


# A pilot home whose policy authorizes texting, and its bound iMessage conversation.
(home / 'state').mkdir(parents=True)
(home / 'state/.lock').write_text(owner + '\n')
conversation('pilot-init', {'publication_policy': 'owner-authored-v2', 'destinations': ['imessage']})
binding = conversation('bind', {'conversation_id': 'text', 'authenticated_principal': 'captain',
                                'destination': 'imessage'})
(home / 'state/imessage').mkdir(mode=0o700)
(home / 'state/imessage/binding.json').write_text(json.dumps(dict(binding, destination='imessage')))
for directory in (inbound, outbox, receipts, courier / 'etc/courier'):
    directory.mkdir(parents=True)
(courier / 'etc/courier/policy.toml').write_text(
    'version = 1\ndefault = "deny"\nteam = []\n[captain]\nname = "Captain"\nto = "%s"\nchannels = ["imessage"]\n' % CAPTAIN)

# Off unless the courier route is opted in: no I/O at all.
once(code=3, extra={'FM_NOTIFY_COURIER': ''})
assert not (home / 'state/courier-pickup').exists()
print('PASS: without FM_NOTIFY_COURIER=1 the pickup exits 3 and touches nothing')

# A record in the spool becomes exactly one conversation turn, filed as the direct bridge files it.
message('m1', 'Ship the fix\n[attachment: /srv/courier/inbox/media/m1.png (image/png, 10 bytes)]',
        attachments=[{'line': '[attachment: /srv/courier/inbox/media/m1.png (image/png, 10 bytes)]', 'saved': True}])
once()
assert len(notes()) == 1 and len(wakes()) == 1, (notes(), wakes())
[filed] = requests()
assert filed['turn_id'] == 'imsg-m1' and filed['request_id'] == 'imsg-req-m1' and filed['previous_turn_id'] is None
event = json.loads((home / 'state/inbox' / notes()[0]).read_text().split('\n--\n', 1)[1])
assert event['committed_transcript'].endswith('(image/png, 10 bytes)]') and event['created_at'] == '2026-10-09T01:00:00Z'
assert stages() == [('m1', 'filed')], stages()
once()
assert len(notes()) == 1 and len(wakes()) == 1 and len(stages()) == 1
print('PASS: a spool record becomes exactly one turn and one wake, acknowledged filed; a rescan files nothing again')

# Duplicates and malformed records are refused, and the record after them is still filed in order.
message('m1')  # the same message key again under a later sequence
message('m-bad-mode', mode=0o600)
message('m-bad-seq', seq_in_body=999)
message('m-extra', extra_field=True)
record('message', 'm-dupkey', {}, raw=b'{"kind":"courier-inbound","kind":"courier-inbound"}')
record('message', 'm-nan', {}, raw=b'{"other_parts":NaN}')
record('vote', 'v-bad', {'chosen': 'yes', 'request_id': 'r', 'digest': 'd', 'poll_message_id': 'p'})
seq += 1
(inbound / ('%012d-message-m-link.json' % seq)).symlink_to(inbound / sorted(os.listdir(inbound))[0])
seq += 1
os.link(inbound / sorted(os.listdir(inbound))[0], inbound / ('%012d-message-m-hard.json' % seq))
message('m2', 'Then deploy')
refused = once()
assert len(notes()) == 2 and len(wakes()) == 2, notes()
assert [r['turn_id'] for r in requests()] == ['imsg-m1', 'imsg-m2'] and requests()[1]['previous_turn_id'] == 'imsg-m1'
for name in ('message-m1', 'm-bad-mode', 'm-bad-seq', 'm-extra', 'm-dupkey', 'm-nan', 'v-bad', 'm-link', 'm-hard'):
    assert name in refused.stderr, (name, refused.stderr)
assert 'Ship the fix' not in refused.stderr  # refusals name the record, never his words
print('PASS: duplicate, wrong-mode, mismatched, extra-field, duplicate-key, NaN, symlinked and hard-linked records are refused')

# Firstmate's acceptance moves his message to working; a numbered question goes out as text plus poll.
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-m1'
once()
assert stages()[-1] == ('m1', 'working'), stages()
question = 'Which one?\n1. Ship it\n2. Wait'
conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-m1', 'response_id': 'q1', 'sequence': 1,
                         'kind': 'question', 'final': False, 'question_binding': 'b1', 'destination': 'imessage',
                         'speech_text': question})
once()
[asked] = outgoing('.json').values()
assert asked['to'] == CAPTAIN and asked['text'] == question and asked['poll_options'] == ['1. Ship it', '2. Wait']
assert asked['channel'] == 'imessage' and asked['attachments'] == []
assert stages()[-1] == ('m1', 'working')  # the question's stage follows its text, never before it
courier_takes()
once()
replies = conversation('poll', binding)['replies']
assert replies[0]['delivery']['state'] == 'completed', replies
assert stages()[-1] == ('m1', 'question'), stages()
print('PASS: accepted turns show working; a numbered question is texted with its poll; its stage follows the sent receipt')

# His vote is filed once, as his bound answer to that question; another vote on it is not.
record('vote', 'vote1', {'chosen': ['2. Wait'], 'request_id': asked['id'], 'digest': 'd', 'poll_message_id': 'p1'})
record('vote', 'vote2', {'chosen': ['1. Ship it'], 'request_id': asked['id'], 'digest': 'd', 'poll_message_id': 'p1'})
record('vote', 'vote3', {'chosen': ['Yes'], 'request_id': 'fm-notify-poll', 'digest': 'd', 'poll_message_id': 'p2'})
once()
voted = [r for r in requests() if r['turn_id'].startswith('imsg-vote-')]
assert len(voted) == 1 and voted[0]['question_binding'] == 'b1', requests()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-m2'
answer = conversation('accept', {'conversation_id': 'text'})['input']
assert answer['committed_transcript'] == '2. Wait' and answer['question_binding'] == 'b1'
print('PASS: a vote on a question it asked is filed once with that question\'s binding; other votes are not filed')

# The final answer to his vote finishes his original message, and nothing moves it backwards.
conversation('publish', {'conversation_id': 'text', 'request_id': answer['request_id'], 'response_id': 'a1',
                         'sequence': 1, 'kind': 'answer', 'final': True, 'destination': 'imessage',
                         'speech_text': 'Waiting, then.'})
once()
assert [row['text'] for row in courier_takes()] == ['Waiting, then.']
once()
assert stages()[-1] == ('m1', 'done') and ('m2', 'working') in stages(), stages()
before = stages()
once()
assert stages() == before
print('PASS: a final answer marks done; stages are published in order and never backwards')

# A message with no text is told so, and one with a missing attachment is filed with a notice.
message('m3', '')
message('m4', 'see this\n[attachment: not saved]', attachments=[{'line': '[attachment: not saved]', 'saved': False}])
once()
assert not any(r['turn_id'] == 'imsg-m3' for r in requests()) and requests()[-1]['turn_id'] == 'imsg-m4'
told = [row['text'] for row in outgoing('.json').values()]
assert told == ['Only text and attachments reach Firstmate from here, so that message was not filed.'], told
courier_takes()
once()
assert [row['text'] for row in courier_takes()] == ['Firstmate has that message, but not everything attached to it.']
assert ('m3', 'failed') in stages() and ('m4', 'filed') in stages(), stages()
print('PASS: an empty message is told NOT_TEXT and marked failed; an unsaved attachment is filed with its notice')

# A message nobody can file - no live session - is told the failure sentence after its retries.
(home / 'state/.lock').write_text('99999999\n')
message('m5', 'anyone there?')
state_path = home / 'state/courier-pickup/state.json'
for _ in range(3):
    once()
    state = json.loads(state_path.read_text())
    if state['pending'] is not None:
        state['pending']['not_before'] = 0  # skip the retry wait, not the retry
        state_path.write_text(json.dumps(state))
assert json.loads(state_path.read_text())['pending'] is None
assert [row['text'] for row in courier_takes()] == ["I couldn't reach Firstmate."]
once()
assert ('m5', 'failed') in stages()
(home / 'state/.lock').write_text(owner + '\n')
print('PASS: a message that cannot be filed is retried, then told the failure sentence and marked failed')

# Pickup latency: from the courier's rename to the turn's check wake, while running.
running = subprocess.Popen([sys.executable, str(pickup), 'run'], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)
try:
    time.sleep(1.0)
    samples = []
    for n in range(5):
        count = len(wakes())
        started = time.monotonic()
        message('lat%d' % n, 'latency %d' % n)
        while len(wakes()) == count:
            assert time.monotonic() - started < 10, 'pickup never filed the record'
            time.sleep(0.01)
        samples.append(time.monotonic() - started)
    print('pickup latency (rename to queued wake), seconds: ' + ' '.join('%.3f' % s for s in samples))
    assert max(samples) < 1.0, samples
finally:
    running.terminate()
    running.wait(timeout=10)
second = subprocess.run([sys.executable, str(pickup), 'once'], env=env, capture_output=True, text=True, timeout=30)
assert second.returncode == 0
print('PASS: each record reaches the wake queue in under a second while the pickup runs')

# Firstmate never wrote, renamed or deleted anything in the courier's spool.
assert all(not name.startswith('.') for name in os.listdir(inbound))
assert len(os.listdir(inbound)) == seq
print('PASS: the spool is left exactly as the courier published it')
