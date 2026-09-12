"""Drive the real voice-conversation transport the way the answering agent does.

Run as:  <fake-harness-shell> -c 'python3 drive-incomplete-spoken-order.py <repo-root> <tmp> $$'

No mocks: every line below is a real `bin/fm-inbox.sh conversation ...` process
against a real FM_HOME, in the live ElevenLabs publication pilot (speech_text,
not the synthetic lab catalog), following the procedure the branch documents in
.agents/skills/answer-voice-turn/{SKILL.md,references/speaking.md,references/publication.md}.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

root, temp, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
home = temp / 'home'
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env['FM_HOME'] = str(home)
cli = str(root / 'bin/fm-inbox.sh')
CALL_A, CALL_B = 'CA-1001', 'CA-2002'
credential = None


def cmd(command, payload, code=0):
    r = subprocess.run([cli, 'conversation', command], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=30)
    assert r.returncode == code, (command, r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout) if code == 0 else r.stderr.strip()


def say(who, text):
    print('\n%-9s %s' % (who, text))


def step(label, detail=''):
    print('   $ fm-inbox.sh conversation %s' % label)
    if detail:
        print('     -> %s' % detail)


def banner(text):
    print('\n' + '=' * 78 + '\n' + text + '\n' + '=' * 78)


def capture(turn, rid, transcript, previous, call_id, binding=None):
    payload = dict(conversation_id='voice', credential=credential, turn_id=turn, request_id=rid,
                   committed_transcript=transcript, revision=1, previous_turn_id=previous,
                   created_at='2026-09-12T18:00:00Z', call_id=call_id)
    if binding:
        payload['question_binding'] = binding
    out = cmd('capture', payload)
    say('CAPTAIN', '"%s"' % transcript)
    step('capture', 'turn %s taken as request %s, state %s%s'
         % (turn, rid, out['state'], ', bound to question %s' % binding if binding else ''))
    return out


def accept(code=0):
    return cmd('accept', {'conversation_id': 'voice'}, code=code)


def publish(rid, response_id, kind, text, final, sequence=1, binding=None, code=0):
    payload = dict(conversation_id='voice', request_id=rid, response_id=response_id,
                   sequence=sequence, kind=kind, final=final, destination='elevenlabs',
                   speech_text=text)
    if binding:
        payload['question_binding'] = binding
    out = cmd('publish', payload, code=code)
    if code == 0:
        say('FIRSTMATE', '"%s"' % text)
        step('publish', 'kind=%s final=%s%s' % (kind, str(final).lower(),
                                                ' question_binding=%s' % binding if binding else ''))
    else:
        step('publish', 'REFUSED: %s' % out)
    return out


def poll():
    return cmd('poll', {'conversation_id': 'voice', 'credential': credential})


def open_questions():
    return [(r['request_id'], r['question_binding']) for r in poll()['replies'] if r['question_open']]


def portions(rid):
    return [(r['kind'], r['final']) for r in poll()['replies'] if r['request_id'] == rid]


def states():
    return [(r['request_id'], r['state']) for r in cmd('audit', {'conversation_id': 'voice'})['requests']]


# ---------------------------------------------------------------- setup
(home / 'state').mkdir(parents=True)
(home / 'state/.lock').write_text(owner + '\n')
cmd('pilot-init', {'publication_policy': 'owner-authored-elevenlabs-v1'})
credential = cmd('bind', {'conversation_id': 'voice',
                          'authenticated_principal': 'captain'})['credential']
print('home: %s   live ElevenLabs publication pilot enabled, conversation "voice" bound' % home)

banner('S1/S2  The 2026-09-12 fragment: asked about, not guessed at   [call %s]' % CALL_A)
capture('t1', 'r1', 'GPT-6 Astra Medium as a test.', None, CALL_A)
got = accept()
step('accept', 'dispatch=%s request=%s' % (got['dispatch'], got['input']['request_id']))
publish('r1', 'v-r1-missing-part', 'question', final=False, binding='r1-missing-part',
        text='I have that as a test of the new model, but not which project it is for. Which one?')
assert open_questions() == [('r1', 'r1-missing-part')], open_questions()
assert portions('r1') == [('question', False)], portions('r1')
step('poll', 'r1 replies=%s  question_open=%s   (no receipt, progress or answer on r1)'
     % (portions('r1'), [q[0] for q in open_questions()]))

capture('t2', 'r2', 'Astra.', 't1', CALL_A, binding='r1-missing-part')
got = accept()
step('accept', 'dispatch=%s request=%s question_binding=%s'
     % (got['dispatch'], got['input']['request_id'], got['input']['question_binding']))
assert got['input']['question_binding'] == 'r1-missing-part'
assert open_questions() == [], open_questions()
step('poll', 'r1 question_open=false  (the bound next turn consumed it)')
publish('r2', 'v-r2-answer', 'answer', final=True,
        text='Started that test of the new model on Astra.')
assert portions('r2') == [('answer', True)] and portions('r1') == [('question', False)]
step('poll', 'the completed order is answered on r2, its own request: %s' % portions('r2'))

banner('S3  Adversarial: a topic-named binding collides the second time the same part is missing')
capture('t3', 'r3', 'Deploy it.', 't2', CALL_A)
accept()
publish('r3', 'v-r3-question', 'question', final=False, binding='missing-project',
        text='Deploy which project?')
capture('t4', 'r4', 'Astra.', 't3', CALL_A, binding='missing-project')
accept()
publish('r4', 'v-r4-answer', 'answer', final=True, text='Deploying Astra now.')
capture('t5', 'r5', 'And run it.', 't4', CALL_A)
accept()
publish('r5', 'v-r5-collides', 'question', final=False, binding='missing-project',
        text='Run it on which project?', code=2)
publish('r5', 'v-r5-question', 'question', final=False, binding='r5-missing-part',
        text='Run what, and on which project?')
assert open_questions() == [('r5', 'r5-missing-part')], open_questions()
step('poll', 'the request_id-derived binding is open: %s' % open_questions())

banner('S4  Adversarial: the captain answers the same question twice, then the way out')
capture('t6', 'r6', 'The benchmark, on Astra.', 't5', CALL_A, binding='r5-missing-part')
accept()
publish('r6', 'v-r6-answer', 'answer', final=True, text='Running the benchmark on Astra.')
capture('t7', 'r7', 'On Astra, I said.', 't6', CALL_A, binding='r5-missing-part')
capture('t8', 'r8', 'Is the deploy green?', 't7', CALL_A)
step('accept', 'REFUSED: %s' % accept(code=2))
assert states()[-2:] == [('r7', 'saved'), ('r8', 'saved')], states()
step('audit', 'the jam holds the later turn behind it too: %s' % states()[-2:])
cmd('reject', {'conversation_id': 'voice', 'request_id': 'r7',
               'reason': 'question r5-missing-part was already answered by turn t6'})
step('reject', 'r7 declined explicitly, transcript and reason retained')
publish('r7', 'v-r7-say-again', 'question', final=False, binding='r7-say-again',
        text='I lost that one. Could you say it again?')
got = accept()
step('accept', 'queue drains: dispatch=%s request=%s' % (got['dispatch'], got['input']['request_id']))
assert got['input']['request_id'] == 'r8'
publish('r8', 'v-r8-answer', 'answer', final=True, text='The deploy is green.')
capture('t9', 'r9', 'Run the benchmark on Astra.', 't8', CALL_A, binding='r7-say-again')
got = accept()
assert got['input']['question_binding'] == 'r7-say-again'
step('accept', 'the whole order said again arrives on its own request %s' % got['input']['request_id'])
publish('r9', 'v-r9-answer', 'answer', final=True, text='Running the benchmark on Astra now.')
assert open_questions() == [], open_questions()

banner('S5  A question the captain never answered, and the redial that lands on it   [call %s]' % CALL_B)
capture('t10', 'r10', 'The usual, as a test.', 't9', CALL_A)
accept()
publish('r10', 'v-r10-missing-part', 'question', final=False, binding='r10-missing-part',
        text='Which project, and what should I run there?')
print('\n   ... the captain hangs up without answering. He redials.')
capture('t11', 'r11', 'Astra.', 't10', CALL_B, binding='r10-missing-part')
got = accept()
spoken_on = {r['request_id']: r['call_id'] for r in cmd('audit', {'conversation_id': 'voice'})['requests']}
assert (spoken_on['r10'], spoken_on['r11']) == (CALL_A, CALL_B), spoken_on
step('accept', 'request=%s question_binding=%s' % (got['input']['request_id'], got['input']['question_binding']))
step('audit', 'r10 was spoken on %s, r11 on %s: the question is stale, so r11 is read on its own merits'
     % (spoken_on['r10'], spoken_on['r11']))
publish('r11', 'v-r11-missing-part', 'question', final=False, binding='r11-missing-part',
        text='I have Astra, but not what to run there. What should I run?')
assert open_questions() == [('r11', 'r11-missing-part')], open_questions()
assert not any(k == 'answer' for k, _ in portions('r10') + portions('r11'))
step('poll', 'still a question, never a dispatch: %s' % open_questions())

banner('Every turn accounted for, and nothing was dispatched on a fragment')
for rid, state in states():
    print('   %-4s %-9s %s' % (rid, state, portions(rid) or '[no reply]'))
print('\nALL SCENARIOS PASSED against the real transport CLI.')
