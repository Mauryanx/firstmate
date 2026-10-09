"""Executable cases for bin/fm-courier-pickup.py, driven by fm-courier-pickup.test.sh, entirely offline.

The test process plays the courier: it publishes spool records and result
receipts the way the courier does, and reads the outbox requests Firstmate
publishes. Firstmate's side runs through its real CLI against a real pilot
conversation transport, so a record becomes a real vc- note and check wake.
"""
import fcntl
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
state_path = home / 'state/courier-pickup/state.json'
CAPTAIN = '+12025550101'


def conversation(command, payload, code=0):
    result = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload), text=True,
                            capture_output=True, env=env, timeout=30)
    assert result.returncode == code, (command, result)
    return json.loads(result.stdout) if code == 0 else result


def once(code=0, extra=None, clock_offset=0):
    command = [sys.executable, str(pickup), 'once']
    if clock_offset:
        command = [sys.executable, '-c',
                   'import runpy,sys,time; clock=time.time; offset=float(sys.argv.pop(1)); '
                   'time.time=lambda: clock()+offset; sys.argv=sys.argv[1:]; '
                   'runpy.run_path(sys.argv[0], run_name="__main__")', str(clock_offset), str(pickup), 'once']
    result = subprocess.run(command, text=True, capture_output=True,
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


def courier_receipt(row, result='sent'):
    receipt = receipts / ('result-%s.%s.json' % (row['id'], secrets.token_hex(4)))
    receipt.write_text(json.dumps({'kind': 'courier-result', 'id': row['id'], 'digest': 'd', 'result': result,
                                   'approval_ref': None, 'idempotency_key': row['id'], 'message_id': 'm'}))
    receipt.chmod(0o640)
    return receipt


def courier_takes(receipted=True):
    """Consume published texts and optionally publish their sent receipts, as the courier does."""
    sent = []
    for name, row in outgoing('.json').items():
        (outbox / name).unlink()
        if receipted:
            courier_receipt(row)
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
once(clock_offset=2 * 86400)
assert asked['id'] in json.loads(state_path.read_text())['polls']
state = json.loads(state_path.read_text())
assert state['polls'][asked['id']]['opened'] is None
state['polls'][asked['id']]['opened'] = time.time()
state_path.write_text(json.dumps(state))
once(clock_offset=2 * 86400)
assert asked['id'] in json.loads(state_path.read_text())['polls']
assert list(outgoing('.json').values()) == [asked]
courier_takes()
delivered = time.time()
once()
assert delivered <= json.loads(state_path.read_text())['polls'][asked['id']]['opened'] <= time.time()
replies = conversation('poll', binding)['replies']
assert replies[0]['delivery']['state'] == 'completed', replies
assert stages()[-1] == ('m1', 'question'), stages()
print('PASS: accepted turns show working; a numbered question is texted with its poll; its stage follows the sent receipt')

conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-m1', 'response_id': 'q1-progress',
                         'sequence': 2, 'kind': 'progress', 'final': False, 'destination': 'imessage',
                         'speech_text': 'Considering the options.'})
once()
assert [row['text'] for row in courier_takes()] == ['Considering the options.']
once()
assert json.loads(state_path.read_text())['marks']['m1']['stage'] == 'question'
assert stages()[-1] == ('m1', 'question'), stages()

record('vote', 'vote-empty', {'chosen': [], 'request_id': asked['id'], 'digest': 'd', 'poll_message_id': 'p1'})
once()
assert not any(r['turn_id'].startswith('imsg-vote-') for r in requests()), requests()

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
print('PASS: a question delayed two days keeps its watch across restart and accepts a vote after delivery')
print('PASS: an empty vote leaves the question watch available for a later nonempty vote across restart')

conversation('publish', {'conversation_id': 'text', 'request_id': answer['request_id'], 'response_id': 'a1-progress',
                         'sequence': 1, 'kind': 'progress', 'final': False, 'destination': 'imessage',
                         'speech_text': 'Preparing to wait.'})
once()
assert [row['text'] for row in courier_takes()] == ['Preparing to wait.']
once()
mark = json.loads(state_path.read_text())['marks']['m1']
assert mark['stage'] == 'question' and mark['follows'] == 'vote-vote1', mark

conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-m1', 'response_id': 'q1-late',
                         'sequence': 3, 'kind': 'progress', 'final': False, 'destination': 'imessage',
                         'speech_text': 'The original request is still in progress.'})
once()
assert [row['text'] for row in courier_takes()] == ['The original request is still in progress.']
once()
mark = json.loads(state_path.read_text())['marks']['m1']
assert mark['stage'] == 'question' and mark['follows'] == 'vote-vote1', mark
print('PASS: direct and propagated progress preserve question rank; a late original reply preserves its follower link')

message('unrelated', 'Another request while you wait.')
once()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-unrelated'
mark = json.loads(state_path.read_text())['marks']['m1']
assert mark['follows'] == 'vote-vote1', mark

# The final answer to his vote finishes his original message, and nothing moves it backwards.
conversation('publish', {'conversation_id': 'text', 'request_id': answer['request_id'], 'response_id': 'a1',
                         'sequence': 2, 'kind': 'answer', 'final': True, 'destination': 'imessage',
                         'speech_text': 'Waiting, then.'})
once()
assert [row['text'] for row in courier_takes()] == ['Waiting, then.']
once()
assert stages()[-1] == ('m1', 'done') and ('m2', 'working') in stages(), stages()
before = stages()
once()
assert stages() == before
assert not json.loads(state_path.read_text())['marks']['m1'].get('awaiting_answer')
print('PASS: a final answer marks done; stages are published in order and never backwards')
print('PASS: an unrelated text preserves the established vote follower and its original done reaction')

for answer_type, timing in (('vote', 'together'), ('text', 'together'), ('vote', 'delayed'), ('text', 'delayed')):
    original = 'receipt-' + answer_type + '-' + timing
    message(original, 'Ask before proceeding.')
    once()
    assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-' + original
    conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-' + original,
                             'response_id': 'q-' + original, 'sequence': 1, 'kind': 'question', 'final': False,
                             'question_binding': 'b-' + original, 'destination': 'imessage',
                             'speech_text': question})
    once()
    [waiting_question] = courier_takes(receipted=timing == 'together')
    assert (original, 'question') not in stages(), stages()
    assert (original, 'working') in stages(), stages()
    if answer_type == 'vote':
        record('vote', 'v-' + original, {'chosen': ['1. Ship it'], 'request_id': waiting_question['id'],
                                        'digest': 'd', 'poll_message_id': 'p-' + original})
        answer_request = 'imsg-req-vote-v-' + original
    else:
        message('a-' + original, 'Ship it')
        answer_request = 'imsg-req-a-' + original
    once()
    assert ((original, 'question') in stages()) == (timing == 'together'), stages()
    captured = conversation('accept', {'conversation_id': 'text'})['input']
    assert captured['request_id'] == answer_request, captured
    assert captured['committed_transcript'] == ('1. Ship it' if answer_type == 'vote' else 'Ship it'), captured
    if timing == 'delayed':
        mark = json.loads(state_path.read_text())['marks'][original]
        assert mark['stage'] == 'working' and mark['follows'], mark
        courier_receipt(waiting_question)
        once()
        assert (original, 'question') not in stages(), stages()
    if answer_type == 'text':
        record('vote', 'later-' + original, {'chosen': ['2. Wait'], 'request_id': waiting_question['id'],
                                            'digest': 'd', 'poll_message_id': 'p-' + original})
        once()
        assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-vote-later-' + original
    conversation('publish', {'conversation_id': 'text', 'request_id': answer_request,
                             'response_id': 'done-' + original, 'sequence': 1, 'kind': 'answer', 'final': True,
                             'destination': 'imessage', 'speech_text': 'Proceeding with ' + answer_type})
    once()
    assert [row['text'] for row in courier_takes()] == ['Proceeding with ' + answer_type]
    once()
    assert (original, 'done') in stages(), stages()
    assert not json.loads(state_path.read_text())['marks'][original].get('awaiting_answer')
    print('PASS: a %s answer with %s receipt preserves the original done reaction and clears its answer flag'
          % (answer_type, timing))

conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-unrelated', 'response_id': 'q-unrelated',
                         'sequence': 1, 'kind': 'question', 'final': False, 'question_binding': 'b-unrelated',
                         'destination': 'imessage', 'speech_text': 'Can we proceed?'})
conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-unrelated', 'response_id': 'e-unrelated',
                         'sequence': 2, 'kind': 'error', 'final': True, 'destination': 'imessage',
                         'speech_text': 'Unable to proceed.'})
once()
assert json.loads(state_path.read_text())['marks']['unrelated']['awaiting_answer']
[unnumbered] = courier_takes()
assert unnumbered['text'] == 'Can we proceed?' and 'poll_options' not in unnumbered
once()
assert [row['text'] for row in courier_takes()] == ['Unable to proceed.']
once()
assert ('unrelated', 'failed') in stages(), stages()
assert not json.loads(state_path.read_text())['marks']['unrelated'].get('awaiting_answer')
print('PASS: a question without poll options awaits an answer at claim and clears the flag on failure')

message('receipt-recovery', 'Recover an old delivery receipt.')
once()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-receipt-recovery'
for number, words in ((1, 'Original portion: café'), (2, 'Recovery complete.')):
    conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-receipt-recovery',
                             'response_id': 'recovery-%d' % number, 'sequence': number,
                             'kind': 'progress' if number == 1 else 'answer', 'final': number == 2,
                             'destination': 'imessage', 'speech_text': words})
once()
[original] = outgoing('.json').values()
original_bytes = (outbox / (original['id'] + '.json')).read_bytes()
assert [row['id'] for row in courier_takes()] == [original['id']]
for receipt_path in receipts.glob('result-' + original['id'] + '.*.json'):
    receipt_path.unlink()
once(clock_offset=2 * 86400)
assert (outbox / (original['id'] + '.json')).read_bytes() == original_bytes
assert list(outgoing('.json').values()) == [original]
assert json.loads(state_path.read_text())['texts'][0]['id'] == original['id']
courier_takes(receipted=False)
once(clock_offset=2 * 86400 + 30)
assert not outgoing('.json'), outgoing('.json')
inflight_receipt = courier_receipt(original, result='pending')
once(clock_offset=2 * 86400 + 61)
assert not outgoing('.json'), outgoing('.json')
inflight_receipt.unlink()
once(clock_offset=2 * 86400 + 61)
assert (outbox / (original['id'] + '.json')).read_bytes() == original_bytes
assert [row['id'] for row in courier_takes()] == [original['id']]
once()
assert [row['text'] for row in courier_takes()] == ['Recovery complete.']
once()
assert ('receipt-recovery', 'done') in stages(), stages()
assert all(row['delivery']['state'] == 'completed'
           for row in conversation('poll', binding)['replies'] if row['response_id'].startswith('recovery-'))
print('PASS: a pruned receipt is recovered by byte-identical same-ID republication, limited to once per 60 s')

message('best-effort-poll', 'Ask with an optional native poll.')
once()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-best-effort-poll'
conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-best-effort-poll',
                         'response_id': 'best-effort-question', 'sequence': 1, 'kind': 'question', 'final': False,
                         'question_binding': 'best-effort-binding', 'destination': 'imessage', 'speech_text': question})
conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-best-effort-poll',
                         'response_id': 'best-effort-progress', 'sequence': 2, 'kind': 'progress', 'final': False,
                         'destination': 'imessage', 'speech_text': 'Continue after the question text.'})
once()
[optional_poll] = courier_takes(receipted=False)
assert optional_poll['text'] == question and optional_poll['poll_options'] == ['1. Ship it', '2. Wait']
unknown_receipt = courier_receipt(optional_poll, result='unknown')
unknown_receipt.rename(receipts / ('result-' + optional_poll['id'] + '.a.json'))
pending_receipt = courier_receipt(optional_poll, result='pending')
pending_receipt.rename(receipts / ('result-' + optional_poll['id'] + '.z.json'))
settled = time.time()
once()
assert ('best-effort-poll', 'question') in stages(), stages()
assert settled <= json.loads(state_path.read_text())['polls'][optional_poll['id']]['opened'] <= time.time()
assert next(row for row in conversation('poll', binding)['replies']
            if row['response_id'] == 'best-effort-question')['delivery']['state'] == 'completed'
assert [row['text'] for row in courier_takes()] == ['Continue after the question text.']
once(clock_offset=301)
assert not outgoing('.json'), outgoing('.json')
record('vote', 'best-effort-vote', {'chosen': ['2. Wait'], 'request_id': optional_poll['id'],
                                    'digest': 'd', 'poll_message_id': 'best-effort-message'})
once()
captured = conversation('accept', {'conversation_id': 'text'})['input']
assert captured['request_id'] == 'imsg-req-vote-best-effort-vote' and captured['question_binding'] == 'best-effort-binding'
conversation('publish', {'conversation_id': 'text', 'request_id': captured['request_id'],
                         'response_id': 'best-effort-answer', 'sequence': 1, 'kind': 'answer', 'final': True,
                         'destination': 'imessage', 'speech_text': 'Waiting after the optional poll.'})
once()
assert [row['text'] for row in courier_takes()] == ['Waiting after the optional poll.']
once()
assert ('best-effort-poll', 'done') in stages(), stages()
print('PASS: an unknown poll receipt settles its question text, starts the vote watch and releases the next reply')

message('ordered', 'Keep these portions in order.')
once()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-ordered'
for number, words in ((1, 'First portion'), (2, question)):
    conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-ordered',
                             'response_id': 'ordered-%d' % number, 'sequence': number,
                             'kind': 'progress' if number == 1 else 'question',
                             'question_binding': 'ordered-binding' if number == 2 else None,
                             'final': False, 'destination': 'imessage', 'speech_text': words})
once()
[first] = courier_takes(receipted=False)
assert first['text'] == 'First portion'
courier_receipt(first, result='unknown')
message('blocked-notext', '')
once(clock_offset=301)
assert not outgoing('.json'), outgoing('.json')
assert ('blocked-notext', 'failed') not in stages(), stages()
assert json.loads(state_path.read_text())['texts'][0]['id'] == first['id']
assert next(row for row in conversation('poll', binding)['replies']
            if row['response_id'] == 'ordered-1')['delivery']['state'] != 'completed'
courier_receipt(first)
once()
[second_portion] = courier_takes(receipted=False)
assert second_portion['text'] == question
assert next(row for row in conversation('poll', binding)['replies']
            if row['response_id'] == 'ordered-1')['delivery']['state'] == 'completed'
courier_receipt(second_portion, result='failed')
once()
assert second_portion['id'] not in json.loads(state_path.read_text())['polls']
assert ('blocked-notext', 'failed') not in stages(), stages()
[blocked_notice] = courier_takes(receipted=False)
assert blocked_notice['text'] == 'Only text and attachments reach Firstmate from here, so that message was not filed.'
once()
assert ('blocked-notext', 'failed') not in stages(), stages()
courier_receipt(blocked_notice)
once()
assert ('blocked-notext', 'failed') in stages(), stages()
assert not json.loads(state_path.read_text())['texts']
print('PASS: a consumed text with an unknown result holds later portions past 300 s until a terminal receipt')
print('PASS: a refused poll has no vote watch; a queued failure reaction waits for its own notice receipt')

message('expired-poll', 'Ask a question with an expiring poll.')
once()
assert conversation('accept', {'conversation_id': 'text'})['input']['request_id'] == 'imsg-req-expired-poll'
conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-expired-poll',
                         'response_id': 'expired-question', 'sequence': 1, 'kind': 'question', 'final': False,
                         'question_binding': 'expired-binding', 'destination': 'imessage', 'speech_text': question})
once()
[expired_poll] = courier_takes()
once()
once(clock_offset=86401)
record('vote', 'expired-vote', {'chosen': ['1. Ship it'], 'request_id': expired_poll['id'],
                                'digest': 'd', 'poll_message_id': 'expired-message'})
once()
assert not any(r['turn_id'] == 'imsg-vote-expired-vote' for r in requests()), requests()
print('PASS: a delivered poll expires after its delivery-based watch lifetime')

# A message with no text is told so, and one with a missing attachment is filed with a notice.
message('m3', '')
message('m4', 'see this\n[attachment: not saved]', attachments=[{'line': '[attachment: not saved]', 'saved': False}])
once()
assert not any(r['turn_id'] == 'imsg-m3' for r in requests()) and requests()[-1]['turn_id'] == 'imsg-m4'
told = [row['text'] for row in outgoing('.json').values()]
assert told == ['Only text and attachments reach Firstmate from here, so that message was not filed.'], told
assert ('m3', 'failed') not in stages(), stages()
courier_takes()
once()
assert [row['text'] for row in courier_takes()] == ['Firstmate has that message, but not everything attached to it.']
assert ('m3', 'failed') in stages() and ('m4', 'filed') in stages(), stages()
print('PASS: an empty message is told NOT_TEXT and marked failed; an unsaved attachment is filed with its notice')

# A message nobody can file - no live session - is told the failure sentence after its retries.
(home / 'state/.lock').write_text('99999999\n')
message('m5', 'anyone there?')
for _ in range(3):
    once()
    state = json.loads(state_path.read_text())
    if state['pending'] is not None:
        state['pending']['not_before'] = 0  # skip the retry wait, not the retry
        state_path.write_text(json.dumps(state))
assert json.loads(state_path.read_text())['pending'] is None
assert ('m5', 'failed') not in stages(), stages()
assert [row['text'] for row in courier_takes()] == ["I couldn't reach Firstmate."]
once()
assert [s for s in stages() if s[0] == 'm5'] == [('m5', 'failed')], stages()
(home / 'state/.lock').write_text(owner + '\n')
print('PASS: a message that cannot be filed is retried, then told the failure sentence and marked failed')

conversation('publish', {'conversation_id': 'text', 'request_id': 'imsg-req-m2', 'response_id': 'q2',
                         'sequence': 1, 'kind': 'question', 'final': False, 'question_binding': 'b2',
                         'destination': 'imessage', 'speech_text': question})
once()
[failed_poll] = courier_takes()
once()
(home / 'state/.lock').write_text('99999999\n')
record('vote', 'vote-failure', {'chosen': ['1. Ship it'], 'request_id': failed_poll['id'],
                                 'digest': 'd', 'poll_message_id': 'p3'})
for _ in range(3):
    once()
    state = json.loads(state_path.read_text())
    if state['pending'] is not None:
        state['pending']['not_before'] = 0
        state_path.write_text(json.dumps(state))
assert json.loads(state_path.read_text())['pending'] is None
assert [row['text'] for row in courier_takes()] == ["I couldn't reach Firstmate."]
once()
assert not any(target.startswith('vote-') for target, stage in stages()), stages()
(home / 'state/.lock').write_text(owner + '\n')
assert not any(r['turn_id'] == 'imsg-vote-vote-failure' for r in requests()), requests()
print('PASS: a vote that exhausts capture retries sends the failure notice without a synthetic-message reaction')

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

# Slow failure: a hung capture queues its notice, whose sent receipt precedes the
# failed stage, inside the courier's 120 s PICKUP_DEADLINE from the record's rename.
hang = open(home / 'state/voice-conversation/lock', 'a')
fcntl.flock(hang, fcntl.LOCK_EX)
running = subprocess.Popen([sys.executable, str(pickup), 'run'], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
try:
    started = time.monotonic()
    message('mslow', 'is anyone home?')
    while not outgoing('.json'):
        assert time.monotonic() - started < 120, 'no failure notice within PICKUP_DEADLINE'
        assert running.poll() is None, 'pickup exited'
        time.sleep(0.5)
    assert ('mslow', 'failed') not in stages(), stages()
    [notice] = courier_takes(receipted=False)
    assert notice['text'] == "I couldn't reach Firstmate.", notice
    courier_receipt(notice)
    while ('mslow', 'failed') not in stages():
        assert time.monotonic() - started < 120, 'no failed stage within PICKUP_DEADLINE'
        assert running.poll() is None, 'pickup exited'
        time.sleep(0.5)
    elapsed = time.monotonic() - started
    print('slow-failure stage after %.1f s' % elapsed)
    assert not outgoing('.json'), outgoing('.json')
finally:
    running.terminate()
    running.wait(timeout=30)
    fcntl.flock(hang, fcntl.LOCK_UN)
    hang.close()
courier_takes()
print('PASS: a hung capture sends one failure notice before its failed stage, all within 120 s of pickup')

# Firstmate never wrote, renamed or deleted anything in the courier's spool.
assert all(not name.startswith('.') for name in os.listdir(inbound))
assert len(os.listdir(inbound)) == seq
print('PASS: the spool is left exactly as the courier published it')
