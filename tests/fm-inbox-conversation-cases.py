"""Executable CLI cases driven by fm-inbox-conversation.test.sh, entirely offline."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

root, temp, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
home = temp / 'home'
# Remove every ambient redirect before invoking a fixture: the captain's home
# must not receive even a synthetic wake.
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env['FM_HOME'] = str(home)
cli = root / 'bin/fm-inbox.sh'


def run(command, payload, code=0, extra=None):
    result = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload),
                            text=True, capture_output=True, env=dict(env, **(extra or {})), timeout=30)
    assert code is None or result.returncode == code, (command, result.returncode, result.stderr, result.stdout)
    return json.loads(result.stdout) if code == 0 else result


catalog = {'ack': 'Certainly, sir. Let me check.',
           'question': 'Should I compare the two public options?',
           'answer': 'Option A is probably faster, but it does not support offline use.',
           'later': 'The public benchmark measured 12.5 seconds, not 1.25 seconds.'}
run('lab-init', {'speech_catalog': catalog})
(home / 'state/.lock').write_text(owner + '\n')
run('lab-init', {'speech_catalog': catalog}, code=2)
# Neither a real populated home nor a home alias can be adopted as a lab.
existing = temp / 'populated'
existing.mkdir()
(existing / 'captain.txt').write_text('never touch')
run('lab-init', {'speech_catalog': catalog}, code=2, extra={'FM_HOME': str(existing)})
assert sorted(p.name for p in existing.iterdir()) == ['captain.txt']
run('bind', {'conversation_id': 'c', 'authenticated_principal': 'captain'}, code=1,
    extra={'FM_STATE_OVERRIDE': str(existing)})


def bind(cid):
    return run('bind', {'conversation_id': cid, 'authenticated_principal': 'captain'})


connection = bind('c')
assert bind('c') == connection
other = bind('other')
run('bind', {'conversation_id': 'c', 'authenticated_principal': 'captain'}, code=6,
    extra={'FM_SUPERVISION_ACTOR': 'branch'})
run('accept', {'conversation_id': 'c'}, code=6, extra={'FM_SUPERVISION_ACTOR': 'branch'})
run('audit', {'conversation_id': 'c'}, code=6, extra={'FM_SUPERVISION_ACTOR': 'unknown'})


def wire(command, payload=None, code=0, extra=None, connection=connection):
    return run(command, dict(connection, **(payload or {})), code, extra)


def owning(command, payload=None, code=0, extra=None, cid='c'):
    return run(command, dict(conversation_id=cid, **(payload or {})), code, extra)


def capture(n, previous=None, **kwargs):
    return dict(turn_id='t' + str(n), request_id='r' + str(n),
                committed_transcript=('No, do not change option B.\nCompare option A, probably 12.5 seconds.'
                                      if n == 1 else 'Synthetic instruction ' + str(n)), revision=1,
                previous_turn_id=previous, created_at='2026-09-09T23:00:00Z', **kwargs)


def publication(n, rid, sequence=1, kind='answer', speech_key='answer', final=True, **kwargs):
    return dict(request_id='r' + str(n), response_id=rid, sequence=sequence,
                kind=kind, speech_key=speech_key, final=final, **kwargs)


# Unauthenticated input cannot impersonate a paired principal.
run('capture', dict(conversation_id='c', credential='wrong', **capture(1)), code=2)
wire('capture', dict(capture(1), authenticated_principal='administrator'), code=2)
wire('capture', dict(capture(1), provisional=True), code=2)
# Out-of-order transcript completion must not reorder dispatch.
wire('capture', capture(2, 't1'))
assert owning('accept')['dispatch'] is False
wire('capture', capture(1))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(lambda _: wire('capture', capture(1)), range(6)))
assert all(r['state'] == 'saved' for r in results)
wire('capture', dict(capture(1), committed_transcript='changed approval'), code=2)
assert len(list((home / 'state/inbox').glob('vc-*.note'))) == 2
# Ordinary inbox readers cannot accidentally dispatch conversation notes.
listed = subprocess.run([str(cli), 'list'], env=env, capture_output=True, text=True, check=True)
assert 'Synthetic instruction' not in listed.stdout
note_id = next((home / 'state/inbox').glob('vc-*.note')).stem
ack = subprocess.run([str(cli), 'drain', '--ack', note_id], env=env, capture_output=True, text=True)
assert ack.returncode != 0
assert owning('accept', cid='other')['dispatch'] is False
first_input = owning('accept')['input']
assert first_input['request_id'] == 'r1'
assert first_input['committed_transcript'] == capture(1)['committed_transcript']
assert owning('accept')['input']['request_id'] == 'r2'
assert owning('accept')['dispatch'] is False
# Work can remain busy while unrelated requests and explicit replies flow.
owning('publish', publication(1, 'a1', kind='receipt', speech_key='ack', final=False))
owning('publish', publication(2, 'q2', kind='question', speech_key='question',
                              question_binding='choice-2', final=False))
wire('capture', capture(3, 't2', question_binding='choice-2'))
assert owning('accept')['input']['question_binding'] == 'choice-2'
wire('capture', capture(4, 't3', correction_of='r1'))
assert owning('accept')['input']['correction_of'] == 'r1'
# Missing targets, repeated turn IDs and cycles cannot reach dispatch.
wire('capture', dict(capture(5, 't4'), turn_id='t1'), code=2)
owning('publish', publication(999, 'missing'), code=2)
owning('publish', dict(publication(1, 'private', sequence=2), speech_text='private raw note'), code=2)
owning('publish', publication(1, 'private', sequence=2, speech_key='private raw note'), code=2)
owning('publish', publication(1, 'final1', sequence=2))
owning('publish', publication(1, 'final1', sequence=2))
owning('publish', publication(1, 'final1', sequence=2, speech_key='later'), code=2)
owning('publish', publication(1, 'too-late', sequence=3), code=2)
owning('publish', publication(3, 'answer3'))
# A reconnecting transport discovers later owner publications by polling, not
# by scraping owner output or knowing scripted response IDs in advance.
discovered = wire('poll')
assert [r['response_id'] for r in discovered['replies']] == ['a1', 'q2', 'final1', 'answer3']
assert all('speech_text' not in r for r in discovered['replies'])
assert next(r for r in discovered['replies'] if r['response_id'] == 'q2')['question_open'] is False
assert wire('poll', connection=other)['replies'] == []
assert 'probably' not in json.dumps(discovered) and 'owner' not in discovered
# Session scope applies to replies and owner publication too.
wire('deliver', {'response_id': 'final1', 'generation': 'g1'}, code=2, connection=other)
old_lock = (home / 'state/.lock').read_text()
(home / 'state/.lock').write_text('1\n')
owning('publish', publication(3, 'wrong-owner'), code=1)
(home / 'state/.lock').write_text(old_lock)
# Independent process invocations model reconnect without fresh reasoning.
selected = next(r for r in discovered['replies'] if r['request_id'] == 'r1' and r['final'])
reply = wire('deliver', {'response_id': selected['response_id'], 'generation': 'g1'})
assert reply['speech_text'] == catalog['answer'] and reply['deliver']
assert reply['disclosure']['scope'] == 'synthetic-catalog'
assert wire('deliver', {'response_id': 'final1', 'generation': 'g1'})['deliver'] is False
assert wire('deliver', {'response_id': 'final1', 'generation': 'new'})['deliver'] is False
wire('playback', {'response_id': 'final1', 'generation': 'old', 'state': 'completed', 'position_ms': 2}, code=2)
receipt = {'response_id': 'final1', 'generation': 'g1', 'state': 'interrupted', 'position_ms': 1200}
wire('playback', receipt)
wire('playback', receipt)
wire('playback', dict(receipt, state='completed'), code=2)
wire('capture', capture(5, 't4'))
followup = owning('accept')
assert followup['input']['request_id'] == 'r5'
assert any(r['response_id'] == 'final1' and r['delivery']['state'] == 'interrupted'
           for r in followup['playback_context'])

# Kill the executable at each durable capture boundary. Restart and retry.
for n, point in ((6, 'note'), (7, 'mapping')):
    message = capture(n, 't' + str(n-1))
    wire('capture', message, code=86, extra={'FM_VOICE_FAULT': point})
    wire('capture', message)
    assert owning('accept')['input']['request_id'] == 'r' + str(n)
    assert owning('accept')['dispatch'] is False
# Lost dispatch return is accounted for but is never blindly dispatched again.
wire('capture', capture(8, 't7'))
owning('accept', code=86, extra={'FM_VOICE_FAULT': 'accept'})
assert owning('accept')['dispatch'] is False
assert any(r['request_id'] == 'r8' and r['state'] == 'accepted' for r in owning('audit')['requests'])
assert not list((home / 'state/inbox').glob('vc-*.note'))
wire('capture', capture(8, 't7'))
assert owning('accept')['dispatch'] is False
# Publication lost after commit recovers identically, including critical qualifiers.
message = publication(8, 'crash-answer', speech_key='later')
owning('publish', message, code=86, extra={'FM_VOICE_FAULT': 'publication'})
owning('publish', message)
# Audio claim committed before playback: unknown, not automatic replay.
wire('deliver', {'response_id': 'crash-answer', 'generation': 'cg'}, code=86,
     extra={'FM_VOICE_FAULT': 'delivery'})
assert wire('deliver', {'response_id': 'crash-answer', 'generation': 'cg'}) == {
    'deliver': False, 'state': 'unknown', 'response_id': 'crash-answer'}
wire('playback', {'response_id': 'crash-answer', 'generation': 'cg', 'state': 'completed', 'position_ms': 3000},
     code=86, extra={'FM_VOICE_FAULT': 'playback'})
assert wire('deliver', {'response_id': 'crash-answer', 'generation': 'cg'})['state'] == 'completed'
# Stale approval is not accepted for a second action.
wire('capture', capture(9, 't8', question_binding='choice-2'))
owning('accept', code=2)
assert owning('audit')['requests'][-1]['state'] == 'saved'
owning('reject', {'request_id': 'r9', 'reason': 'Question already answered; ask for a fresh instruction.'})
owning('reject', {'request_id': 'r9', 'reason': 'Question already answered; ask for a fresh instruction.'})
owning('reject', {'request_id': 'r8', 'reason': 'Cannot undo accepted work'}, code=2)
assert owning('audit')['requests'][-1]['state'] == 'rejected'
assert owning('accept')['dispatch'] is False
owning('publish', publication(9, 'bad-receipt', kind='receipt', speech_key='ack'), code=2)
owning('publish', publication(9, 'clarification', kind='question', speech_key='question',
                              question_binding='fresh-choice'))
# Ownership follows the session lock: a later session holding it re-binds the
# same principal idempotently, while a different principal still cannot adopt
# the conversation.
foreign = temp / 'foreign-driver.sh'
foreign.write_text('echo "$$" > "$FM_HOME/state/.lock"\n'
                   'printf \'%s\' \'{"conversation_id":"c","authenticated_principal":"captain"}\' | '
                   '"$1" conversation bind || exit 3\n'
                   'printf \'%s\' \'{"conversation_id":"c","authenticated_principal":"administrator"}\' | '
                   '"$1" conversation bind\n')
result = subprocess.run([str(temp / 'codex'), str(foreign), str(cli)], env=env,
                        capture_output=True, text=True, timeout=30)
assert result.returncode == 2 and 'already bound' in result.stderr, result
assert json.loads(result.stdout) == connection
(home / 'state/.lock').write_text(old_lock)
# ID-only transport logs: no synthetic transcript or reply in wakes.
wakes = (home / 'state/.wake-queue').read_text()
assert 'Synthetic instruction' not in wakes and 'probably' not in wakes
accounting = owning('audit')
assert len(accounting['requests']) == 9
assert len(accounting['replies']) == 6
assert len({r['request_id'] for r in accounting['requests']}) == 9
assert 'credential' not in json.dumps(accounting)
assert 'committed_transcript' not in json.dumps(accounting)
assert owning('audit', cid='other')['requests'] == []
print('PASS: 9 inputs accounted for; 8 single dispatch claims, 1 stale bound input explicitly rejected; 6 replies retained')
print('PASS: capture/accept/publication/playback crash windows, duplicate races and wrong-session refusals')

# One conversation carries every call, so turns an earlier call left saved are
# older than the live question and would be dispatched in front of it. The
# transport records the call each turn was spoken on; the owner's supersession
# step is exercised against it here.
scoped = {cid: bind(cid) for cid in ('calls', 'redial')}


def spoke(n, previous=None, code=0, cid='calls', **kwargs):
    return run('capture', dict(scoped[cid], **capture(n, previous, **kwargs)), code)


def scope_to_live_call(cid='calls', meanwhile=None):
    """Retire what earlier calls left saved, as the owner does before accepting.

    The live call is the call of the most recently captured request per audit;
    the wake row presented first is not consulted. `meanwhile` runs between the
    audit and the rejects, where a redial can land. A refused reject restarts the
    pass from a fresh audit.
    """
    while True:
        live = owning('audit', cid=cid)['requests'][-1]['call_id']
        if meanwhile:
            meanwhile, _ = None, meanwhile()
        if live is not None:
            refused = False
            for record in saved(cid):
                if record['call_id'] != live:
                    result = owning('reject', {'request_id': record['request_id'],
                                               'reason': 'prior call ended; superseded'}, cid=cid, code=None)
                    if result.returncode == 2:
                        assert 'live call ' + record['call_id'] + '; not superseded' in result.stderr, result
                        refused = True
                        break
                    assert result.returncode == 0, result
            if refused:
                continue
        return live, [r['request_id'] for r in owning('audit', cid=cid)['requests'] if r['state'] == 'rejected']


def saved(cid='calls'):
    return [r for r in owning('audit', cid=cid)['requests'] if r['state'] == 'saved']


# The 2026-09-12 shape: the captain hangs up before any turn of the first call is
# accepted, then places a second call. The drain wakes the owner for the oldest
# held row, r1, whose call is the ended one; scoping by that row would reject the
# live caller. Scoping by the newest capture answers r4 and retires r1 to r3.
for n, previous in ((1, None), (2, 't1'), (3, 't2')):
    spoke(n, previous, call_id='CA-ended')
spoke(4, 't3', call_id='CA-live')
assert [r['call_id'] for r in owning('audit', cid='calls')['requests']] == ['CA-ended'] * 3 + ['CA-live']
assert saved()[0]['call_id'] == 'CA-ended'
assert scope_to_live_call() == ('CA-live', ['r1', 'r2', 'r3'])
live = owning('accept', cid='calls')
assert live['input']['request_id'] == 'r4' and live['input']['call_id'] == 'CA-live'
assert owning('accept', cid='calls')['dispatch'] is False
assert [r['reason'] for r in owning('audit', cid='calls')['requests'][:3]] == ['prior call ended; superseded'] * 3
# A turn captured without a call_id is saved, ordered and accepted exactly as it
# was before the field existed, and the next call supersedes it on the same terms.
spoke(5, 't4')
assert owning('audit', cid='calls')['requests'][-1]['call_id'] is None
# Carrying no call is an identity of its own: the retry matches it, and naming
# the live call on the same turn does not.
spoke(5, 't4')
spoke(5, 't4', call_id='CA-third', code=2)
spoke(6, 't5', call_id='CA-third')
assert scope_to_live_call() == ('CA-third', ['r1', 'r2', 'r3', 'r5'])
assert owning('accept', cid='calls')['input']['request_id'] == 'r6'
assert owning('accept', cid='calls')['dispatch'] is False
# A later turn of the same call supersedes nothing.
spoke(7, 't6', call_id='CA-third')
assert scope_to_live_call() == ('CA-third', ['r1', 'r2', 'r3', 'r5'])
# The call a turn was spoken on is part of its immutable identity, not a label.
spoke(7, 't6', call_id='CA-third')
spoke(7, 't6', call_id='CA-relabelled', code=2)
spoke(8, 't7', call_id=5, code=2)
assert [r['request_id'] for r in saved()] == ['r7']
# When the newest capture names no call there is no live call to scope by, so a
# saved turn from a named call is left alone and handling proceeds oldest first.
spoke(9, 't7')
assert scope_to_live_call() == (None, ['r1', 'r2', 'r3', 'r5'])
assert [r['request_id'] for r in saved()] == ['r7', 'r9']
assert owning('accept', cid='calls')['input']['request_id'] == 'r7'
assert owning('accept', cid='calls')['input']['request_id'] == 'r9'
assert owning('accept', cid='calls')['dispatch'] is False
print('PASS: the newest capture names the live call; a call nobody answered is superseded, a nameless one supersedes nothing')

# The captain redials while the owner is between its audit and its rejects: the
# stale pass would retire the redial's turn as superseded. The transport refuses
# that, naming the live call, and the restarted pass retires the dropped call.
spoke(4, None, cid='redial', call_id='CA-2')
assert owning('audit', cid='redial')['requests'][-1]['call_id'] == 'CA-2'
assert scope_to_live_call('redial', meanwhile=lambda: spoke(5, 't4', cid='redial', call_id='CA-3')) == ('CA-3', ['r4'])
assert owning('accept', cid='redial')['input']['request_id'] == 'r5'
assert owning('accept', cid='redial')['dispatch'] is False
# The refusal is scoped to that one reason: the live call's turn can still be
# declined for a reason of its own, and a nameless newest turn refuses nothing.
spoke(6, 't5', cid='redial', call_id='CA-3')
assert owning('reject', {'request_id': 'r6', 'reason': 'prior call ended; superseded'}, cid='redial', code=2)
assert owning('reject', {'request_id': 'r6', 'reason': 'say that again'}, cid='redial')['state'] == 'rejected'
spoke(7, 't6', cid='redial', call_id='CA-3')
spoke(8, 't7', cid='redial')
assert owning('reject', {'request_id': 'r7', 'reason': 'prior call ended; superseded'}, cid='redial')['state'] == 'rejected'
assert owning('accept', cid='redial')['input']['request_id'] == 'r8'
print('PASS: a redial captured mid-pass is never superseded; the pass restarts and retires the dropped call')

# Live publication uses the same owner seam, never a transport-supplied author.
pilot = temp / 'pilot'
(pilot / 'state').mkdir(parents=True)
(pilot / 'state/.lock').write_text(owner + '\n')
env['FM_HOME'] = str(pilot)
run('pilot-init', {'publication_policy': 'owner-authored-elevenlabs-v1'})
run('pilot-init', {'publication_policy': 'owner-authored-elevenlabs-v1'})
connection = bind('live')
run('capture', dict(connection, **capture(1)))
assert owning('accept', cid='live')['dispatch']
live_reply = dict(conversation_id='live', request_id='r1', response_id='live-answer', sequence=1,
                  kind='answer', final=True, destination='elevenlabs',
                  speech_text='Synthetic live calculation: 12.5 is greater than 1.25; nothing was changed.')
run('publish', dict(live_reply, destination='other-provider'), code=2)
run('publish', dict(live_reply, authenticated_principal='captain'), code=2)
run('publish', dict(live_reply, speech_text='x' * 1201), code=2)
run('publish', live_reply)
assert run('publish', live_reply)['published']
run('publish', dict(live_reply, speech_text='different'), code=2)
live_journal = json.loads((pilot / 'state/voice-conversation/journal.json').read_text())
proof = next(iter(live_journal['replies'].values()))['disclosure']
assert proof['author'] == live_journal['conversations']['live']['owner']
assert proof['destination'] == 'elevenlabs' and proof['published_at'] > 0

# A long answer is published as ordered portions, which is what keeps a slow
# turn audible instead of silent: progress first, the answer last, and nothing
# after the portion that closes the stream.
run('capture', dict(connection, **capture(2, 't1')))
assert owning('accept', cid='live')['input']['request_id'] == 'r2'
portion = dict(conversation_id='live', request_id='r2', destination='elevenlabs')
run('publish', dict(portion, response_id='live-progress', sequence=1, kind='progress',
                    final=False, speech_text='Still reading the records.'))
run('publish', dict(portion, response_id='live-late', sequence=1, kind='answer',
                    final=True, speech_text='Out of order.'), code=2)
run('publish', dict(portion, response_id='live-answer-2', sequence=2, kind='answer',
                    final=True, speech_text='Both branches are green.'))
run('publish', dict(portion, response_id='live-after', sequence=3, kind='answer',
                    final=True, speech_text='After the close.'), code=2)
# The bridge speaks what is waiting, so every published portion must show up
# there unclaimed and in publication order.
waiting = [r for r in run('poll', dict(connection))['replies']
           if r['request_id'] == 'r2' and r['delivery']['state'] == 'waiting']
assert [(r['response_id'], r['kind'], r['final']) for r in waiting] == [
    ('live-progress', 'progress', False), ('live-answer-2', 'answer', True)], waiting
print('PASS: live publication is owner-authored, digest-bound, and ordered; portions surface as waiting speech')

# A routine session restart is a non-event. The next session holding this
# home's lock accepts and publishes what the previous session left saved, the
# bridge keeps polling with the credential it already holds, and re-running
# pilot-init records the new session without anyone touching policy.json.
run('capture', dict(connection, **capture(3, 't2')))
assert owning('audit', cid='live')['requests'][-1]['state'] == 'saved'
policy_path = pilot / 'state/voice-conversation/policy.json'
first_policy = json.loads(policy_path.read_text())
assert first_policy['enabled_by'] == proof['author']
successor = temp / 'successor-session.sh'
successor.write_text(
    'set -e\n'
    'echo "$$" > "$FM_HOME/state/.lock"\n'
    'printf \'%s\' \'{"conversation_id":"live"}\' | "$1" conversation accept\n'
    'printf \'%s\' \'{"conversation_id":"live","request_id":"r3","response_id":"after-restart",'
    '"sequence":1,"kind":"answer","final":true,"destination":"elevenlabs",'
    '"speech_text":"Yes, the deploy is green."}\' | "$1" conversation publish\n'
    'printf \'%s\' "$2" | "$1" conversation poll\n'
    'printf \'%s\' \'{"publication_policy":"owner-authored-elevenlabs-v1"}\' | "$1" conversation pilot-init\n')
result = subprocess.run([str(temp / 'codex'), str(successor), str(cli), json.dumps(connection)],
                        env=env, capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result
accepted, published, polled, enabled = [json.loads(line) for line in result.stdout.splitlines()]
assert accepted['input']['request_id'] == 'r3' and published['published'] and enabled['pilot']
assert any(r['response_id'] == 'after-restart' and r['delivery']['state'] == 'waiting' for r in polled['replies'])
live_journal = json.loads((pilot / 'state/voice-conversation/journal.json').read_text())
successor_owner = live_journal['conversations']['live']['owner']
assert successor_owner != proof['author']
assert next(r for r in live_journal['replies'].values()
            if r['event']['response_id'] == 'after-restart')['disclosure']['author'] == successor_owner
assert json.loads(policy_path.read_text()) == dict(first_policy, enabled_by=successor_owner)
# A caller that does not hold this home's lock is still refused, whoever it is.
(pilot / 'state/.lock').write_text('1\n')
owning('accept', cid='live', code=1)
run('pilot-init', {'publication_policy': 'owner-authored-elevenlabs-v1'}, code=1)
assert json.loads(policy_path.read_text())['enabled_by'] == successor_owner
(pilot / 'state/.lock').write_text(owner + '\n')
assert owning('audit', cid='live')['requests'][-1]['state'] == 'accepted'
print('PASS: the session holding the lock answers what its predecessor left saved; a lock-less caller is refused')

# A spoken instruction that does not say what to do, or which project to do it
# to, is answered with a question naming the missing part. The transport
# accepts that question as the only portion on the turn, reports it open,
# consumes it when the caller supplies the missing part as the next turn bound
# to it, and carries that turn's own reply. Whether the pair is read as one
# order is speaking.md's rule; the transport interprets nothing.
run('capture', dict(connection, **dict(capture(4, 't3'),
                                       committed_transcript='GPT-6 Astra Medium as a test.')))
assert owning('accept', cid='live')['input']['request_id'] == 'r4'
missing = dict(conversation_id='live', request_id='r4', destination='elevenlabs')
run('publish', dict(missing, response_id='live-missing-target', sequence=1,
                    kind='question', final=False, question_binding='r4-missing-part',
                    speech_text='I have that as a test of the new model, but not which project it is for. Which one?'))
asked = [r for r in run('poll', dict(connection))['replies'] if r['request_id'] == 'r4']
assert [(r['kind'], r['final'], r['question_open']) for r in asked] == [('question', False, True)], asked
run('capture', dict(connection, **dict(capture(5, 't4', question_binding='r4-missing-part'),
                                       committed_transcript='Astra.')))
supplied = owning('accept', cid='live')
assert supplied['input']['request_id'] == 'r5'
assert supplied['input']['question_binding'] == 'r4-missing-part'
run('publish', dict(conversation_id='live', request_id='r5', destination='elevenlabs',
                    response_id='live-pair-answered', sequence=1, kind='answer', final=True,
                    speech_text='Started that test of the new model on Astra.'))
completed = run('poll', dict(connection))['replies']
assert [(r['request_id'], r['kind'], r['final']) for r in completed if r['kind'] == 'question'
        ] == [('r4', 'question', False)], completed
assert not any(r['question_open'] for r in completed), completed
assert [(r['response_id'], r['kind'], r['final']) for r in completed if r['request_id'] == 'r5'
        ] == [('live-pair-answered', 'answer', True)], completed
# The binding is spent for the life of the conversation, so a question on a
# later turn cannot reuse the name rather than reopening the consumed one.
run('capture', dict(connection, **dict(capture(6, 't5'), committed_transcript='Is the deploy green?')))
assert owning('accept', cid='live')['input']['request_id'] == 'r6'
reused = run('publish', dict(conversation_id='live', request_id='r6', destination='elevenlabs',
                             response_id='live-reused-binding', sequence=1, kind='question', final=False,
                             question_binding='r4-missing-part', speech_text='Which one?'), code=2)
assert 'question binding already used' in reused.stderr, reused
# An ordinary clarifying question about work Firstmate proposed takes the same
# binding route, and the caller's "Yes." consumes it at acceptance exactly as a
# missing-part answer does: the transport draws no distinction, so only
# speaking.md decides that this answer settles a decision rather than becoming
# an order. What is asserted here is the shape the transport does keep - the
# answer lands on its own request and leaves no question open.
run('publish', dict(conversation_id='live', request_id='r6', destination='elevenlabs',
                    response_id='live-clarify', sequence=1, kind='question', final=False,
                    question_binding='r6-rollback',
                    speech_text='It is green, but slower than the last one. Should I roll it back?'))
run('capture', dict(connection, **dict(capture(7, 't6', question_binding='r6-rollback'),
                                       committed_transcript='Yes.')))
agreed = owning('accept', cid='live')
assert agreed['input']['request_id'] == 'r7'
assert agreed['input']['question_binding'] == 'r6-rollback'
run('publish', dict(conversation_id='live', request_id='r7', destination='elevenlabs',
                    response_id='live-decision-recorded', sequence=1, kind='answer', final=True,
                    speech_text='Noted, and the rollback is recorded as your call.'))
settled = run('poll', dict(connection))['replies']
assert not any(r['question_open'] for r in settled), settled
assert [(r['response_id'], r['kind'], r['final']) for r in settled if r['request_id'] == 'r7'
        ] == [('live-decision-recorded', 'answer', True)], settled
print('PASS: an instruction missing its target is answered with an open question the bound next turn consumes, '
      'and a clarifying question takes the same route with its own reply')

# A turn bound to a missing-part question need not supply that part: the bridge
# binds whatever comes next while the question is open, so a change of subject
# arrives on the same route and consumes the question too. The transport shows
# that turn answered on its own request, with the consumed question the only
# reply the abandoned order carries. Which of the two a bound turn is stays
# speaking.md's reading; the transport interprets nothing.
run('capture', dict(connection, **dict(capture(8, 't7'),
                                       committed_transcript='The Sonnet run, five thousand samples.')))
assert owning('accept', cid='live')['input']['request_id'] == 'r8'
run('publish', dict(conversation_id='live', request_id='r8', destination='elevenlabs',
                    response_id='live-second-missing', sequence=1, kind='question', final=False,
                    question_binding='r8-missing-part',
                    speech_text='I have five thousand samples of the Sonnet run, but not what to do with them. What would you like done?'))
run('capture', dict(connection, **dict(capture(9, 't8', question_binding='r8-missing-part'),
                                       committed_transcript='Never mind. What is on the board?')))
abandoning = owning('accept', cid='live')
assert abandoning['input']['request_id'] == 'r9'
assert abandoning['input']['question_binding'] == 'r8-missing-part'
run('publish', dict(conversation_id='live', request_id='r9', destination='elevenlabs',
                    response_id='live-board', sequence=1, kind='answer', final=True,
                    speech_text='Two branches are waiting on your review, and nothing else is blocked.'))
abandoned = run('poll', dict(connection))['replies']
assert [(r['response_id'], r['kind'], r['question_open']) for r in abandoned if r['request_id'] == 'r8'
        ] == [('live-second-missing', 'question', False)], abandoned
assert [(r['response_id'], r['kind'], r['final']) for r in abandoned if r['request_id'] == 'r9'
        ] == [('live-board', 'answer', True)], abandoned
assert not any(r['question_open'] for r in abandoned), abandoned
print('PASS: a bound turn that changes the subject is answered on its own request, '
      'and the order it abandons keeps only the question already published against it')

# A question the captain never answered stays open after his call ends, and the
# next call's opening turn is captured bound to it: the transport consumes that
# binding across calls without complaint, and the accepted request stays
# accepted, so supersession never retires it. What the record does keep is the
# call each turn was spoken on, which audit reports beside its state, so the two
# are visibly from different calls; speaking.md is what forbids pairing them.
run('capture', dict(connection, **dict(capture(10, 't9'), call_id='CA-first',
                                       committed_transcript='GPT-6 Astra Medium as a test.')))
assert owning('accept', cid='live')['input']['request_id'] == 'r10'
run('publish', dict(conversation_id='live', request_id='r10', destination='elevenlabs',
                    response_id='live-ended-call-question', sequence=1, kind='question', final=False,
                    question_binding='r10-missing-part',
                    speech_text='I have that as a test of the new model, but not which project it is for. Which one?'))
run('capture', dict(connection, **dict(capture(11, 't10', question_binding='r10-missing-part'),
                                       call_id='CA-second', committed_transcript='Astra, please.')))
redialled = owning('accept', cid='live')
assert redialled['input']['request_id'] == 'r11'
assert redialled['input']['question_binding'] == 'r10-missing-part'
spoken_on = {r['request_id']: (r['call_id'], r['state']) for r in owning('audit', cid='live')['requests']}
assert (spoken_on['r10'], spoken_on['r11']) == (('CA-first', 'accepted'), ('CA-second', 'accepted')), spoken_on
run('publish', dict(conversation_id='live', request_id='r11', destination='elevenlabs',
                    response_id='live-new-call-answer', sequence=1, kind='answer', final=True,
                    speech_text='Astra has two branches waiting on your review.'))
across = run('poll', dict(connection))['replies']
assert [(r['response_id'], r['kind'], r['question_open']) for r in across if r['request_id'] == 'r10'
        ] == [('live-ended-call-question', 'question', False)], across
assert [(r['response_id'], r['kind'], r['final']) for r in across if r['request_id'] == 'r11'
        ] == [('live-new-call-answer', 'answer', True)], across
print("PASS: a question an ended call left open binds the next call's turn all the same, "
      'and audit still reports which call each of the two was spoken on')

# The reconcile route out of a turn whose predecessor was never captured: reject
# it, then ask for it again. The turn that answers that question carries the
# whole order rather than a missing part, and the transport accepts it like any
# bound turn and keeps its reply on its own request.
run('capture', dict(connection, **dict(capture(12, 't-lost'), call_id='CA-second',
                                       committed_transcript='And the same for the other one.')))
assert owning('accept', cid='live')['dispatch'] is False
owning('reject', {'request_id': 'r12', 'reason': 'previous turn t-lost was never captured'}, cid='live')
run('publish', dict(conversation_id='live', request_id='r12', destination='elevenlabs',
                    response_id='live-say-again', sequence=1, kind='question', final=False,
                    question_binding='r12-say-again',
                    speech_text='I lost the turn before this one. Could you say it again?'))
run('capture', dict(connection, **dict(capture(13, 't11', question_binding='r12-say-again'),
                                       call_id='CA-second',
                                       committed_transcript='Run the benchmark on Astra.')))
resaid = owning('accept', cid='live')
assert resaid['input']['request_id'] == 'r13'
assert resaid['input']['question_binding'] == 'r12-say-again'
assert resaid['input']['committed_transcript'] == 'Run the benchmark on Astra.'
run('publish', dict(conversation_id='live', request_id='r13', destination='elevenlabs',
                    response_id='live-resaid-running', sequence=1, kind='answer', final=True,
                    speech_text='Running the benchmark on Astra now.'))
reissued = run('poll', dict(connection))['replies']
assert not any(r['question_open'] for r in reissued), reissued
assert [(r['response_id'], r['kind'], r['final']) for r in reissued if r['request_id'] == 'r13'
        ] == [('live-resaid-running', 'answer', True)], reissued
print('PASS: a rejected turn asked for again is answered by a bound turn carrying the whole order, '
      'on its own request')
