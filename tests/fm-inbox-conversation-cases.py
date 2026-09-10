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
    assert result.returncode == code, (command, result.returncode, result.stderr, result.stdout)
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
# A new owner identity cannot adopt or publish into a previous conversation.
# Exercise identity mismatch independently of ancestry rejection above.
foreign = temp / 'foreign-driver.sh'
foreign.write_text('echo "$$" > "$FM_HOME/state/.lock"\n'
                   'printf \'%s\' \'{"conversation_id":"c","authenticated_principal":"captain"}\' | '
                   '"$1" conversation bind\n')
result = subprocess.run([str(temp / 'codex'), str(foreign), str(cli)], env=env,
                        capture_output=True, text=True, timeout=30)
assert result.returncode == 2 and 'already bound' in result.stderr
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

# Real loopback HTTP handlers, scoped pairing and provider substitution only.
sys.path.insert(0, str(root / 'bin'))
from fm_voice_pilot import Bridge, Budget, Pilot, PilotError, ElevenLabs
import threading
import urllib.request
import urllib.error
import hashlib
import io
import wave
import base64
from unittest.mock import patch

class SpeechFixture:
    def __init__(self):
        self.calls = []
    def speech(self, text, request_id):
        self.calls.append((text, request_id))
        return b'fixture-audio'
    def conversation_token(self, agent_id):
        return None  # Stands in for a public agent, which is named rather than tokened.

    def transcribe(self, audio, request_id):
        return 'A synthetic spoken follow-up'

provider = SpeechFixture()
server = Pilot(0, Bridge(pilot, connection), provider, b'approved-ack')
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
cookie = None

def http(path, data, expected=200, origin=None):
    headers = {'Origin': origin or server.origin, 'Content-Type': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    request = urllib.request.Request(server.origin + path, json.dumps(data).encode(), headers)
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    assert response.status == expected, (path, response.status, response.read())
    body = response.read()
    return (json.loads(body) if response.headers.get('Content-Type') == 'application/json' else body,
            response.headers)

try:
    http('/poll', {}, 400)
    http('/pair', {'secret': server.pair_secret}, 400, origin='https://attacker.invalid')
    token = server.pair_secret
    paired, headers = http('/pair', {'secret': token})
    assert paired['conversation_id'] == 'live'
    cookie = headers['Set-Cookie'].split(';')[0]
    http('/pair', {'secret': token}, 400)
    http('/capture', dict(capture(2, 't1'), conversation_id='other'), 400)
    http('/capture', capture(2, 't1'))
    http('/capture', capture(2, 't1'))
    assert owning('accept', cid='live')['input']['request_id'] == 'r2'
    assert owning('accept', cid='live')['dispatch'] is False
    assert http('/ack', {})[0] == b'approved-ack'
    assert len(http('/poll', {})[0]['replies']) == 1
    spoken = http('/speech', {'response_id':'live-answer', 'generation':'g'})[0]
    assert base64.b64decode(spoken['audio']) == b'fixture-audio'
    assert spoken['speech_text'] == live_reply['speech_text']
    assert http('/speech', {'response_id':'live-answer', 'generation':'g'})[0]['deliver'] is False
    assert provider.calls == [(live_reply['speech_text'], 'tts:live:live-answer')]
    http('/playback', {'response_id':'live-answer','generation':'g','state':'interrupted','position_ms':12})
    http('/playback', {'response_id':'live-answer','generation':'old','state':'completed','position_ms':100}, 400)
    # Identity remains checked after browser authentication.
    old_lock = (pilot / 'state/.lock').read_text()
    (pilot / 'state/.lock').write_text('999999999\n')
    http('/poll', {}, 400)
    (pilot / 'state/.lock').write_text(old_lock)
    http('/disconnect', {})
    http('/poll', {}, 400)
finally:
    server.shutdown()
    server.server_close()
    thread.join()

# A replacement browser server reuses durable delivery claims but never cookies.
server = Pilot(0, Bridge(pilot, connection), provider, b'approved-ack')
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    http('/poll', {}, 400)  # Old browser cookie is not a new server credential.
    _, headers = http('/pair', {'secret': server.pair_secret})
    cookie = headers['Set-Cookie'].split(';')[0]
    assert http('/speech', {'response_id':'live-answer', 'generation':'replacement'})[0]['deliver'] is False
    assert len(provider.calls) == 1
    # A third question the owner answers out of order later in the browser lane.
    http('/capture', capture(3, 't2'))
    server.expires = 0
    http('/capture', capture(4, 't3'), 400)
    assert len(owning('audit', cid='live')['requests']) == 3
finally:
    server.shutdown()
    server.server_close()
    thread.join()

budget = Budget(temp / 'credits.json', 100)
budget.reserve('one', 60)
for key, amount in [('one', 1), ('two', 41)]:
    try:
        Budget(temp / 'credits.json', 100).reserve(key, amount)
        raise AssertionError('budget accepted duplicate or excess')
    except PilotError:
        pass
budget.finish('one', failed=True)
try:
    budget.reserve('three', 1)
    raise AssertionError('uncertain request did not halt paid calls')
except PilotError:
    pass

def competing_reservation(key):
    try:
        Budget(temp / 'race-credits.json', 100).reserve(key, 60)
        return True
    except PilotError:
        return False
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    assert sum(pool.map(competing_reservation, ['a', 'b'])) == 1
assert json.loads((temp / 'race-credits.json').read_text())['reserved_credits'] == 60

# Exercise the real adapter serialization and no-overage gate, never a provider.
class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

account = {'can_extend_character_limit': False, 'allowed_to_extend_character_limit': False,
           'character_limit': 10000, 'character_count': 573}
requests = []
def network(request, timeout):
    requests.append(request)
    if request.full_url.endswith('/subscription'):
        return Response(json.dumps(account).encode())
    if request.full_url.endswith('/speech-to-text'):
        return Response(json.dumps({'text':'No, do not change option B.'}).encode())
    return Response(b'x' * 1200, {'Content-Type':'audio/mpeg', 'character-cost':'4'})

adapter = ElevenLabs('test-only-secret', Budget(temp / 'adapter-credits.json', 100))
with patch('urllib.request.urlopen', network):
    assert len(adapter.speech('Probably.', 'sample')) == 1200
    payload = json.loads(requests[-1].data)
    assert payload['text'] == 'Probably.' and payload['voice_settings']['stability'] == .40
    assert 'JBFqnCBsd6RMkjVDRZzb' in requests[-1].full_url
    account['can_extend_character_limit'] = True
    try:
        adapter.speech('No overages.', 'refused')
        raise AssertionError('account overage was not refused')
    except PilotError:
        pass
    assert len(requests) == 3  # metadata only on the refused call
    account['can_extend_character_limit'] = False
    wav = io.BytesIO()
    with wave.open(wav, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0' * 16000)
    assert adapter.transcribe(wav.getvalue(), 'stt-contract') == 'No, do not change option B.'
    assert requests[-1].full_url.endswith('/speech-to-text')
    assert b'RIFF' in requests[-1].data and b'scribe_v2' in requests[-1].data
print('pilot: exact owner publication, HTTP isolation, single delivery, credit and overage guards passed')

# Opt-in actual browser mechanics share the real isolated transport and current
# owner fixture above. No microphone input or acoustic output is synthesized.
if os.environ.get('FM_VOICE_PLAYWRIGHT_MODULE'):
    # r2's answer is provably late: the captain asked r3 before the browser
    # ever connects, so the reply must name the question it belongs to.
    assert owning('accept', cid='live')['input']['request_id'] == 'r3'
    run('publish', dict(live_reply, request_id='r2', response_id='late-answer',
                        speech_text='Synthetic late answer to the earlier question.'))
    browser_server = Pilot(0, Bridge(pilot, connection), provider, b'non-acoustic-test-ack')
    browser_thread = threading.Thread(target=browser_server.serve_forever, daemon=True)
    browser_thread.start()
    try:
        subprocess.run(['node', str(root / 'tests/fm-voice-browser-cases.cjs'),
                        browser_server.origin + '/#' + browser_server.pair_secret],
                       check=True, timeout=90)
        accounting = owning('audit', cid='live')
        assert len(accounting['requests']) == 5
        assert [r['state'] for r in accounting['requests']] == [
            'accepted', 'accepted', 'accepted', 'saved', 'saved']
        # The fixture provider stands in for ElevenLabs; the late answer is the
        # only speech the browser lane ever requests.
        assert len(provider.calls) == 2
        assert provider.calls[1][0] == 'Synthetic late answer to the earlier question.'
    finally:
        browser_server.shutdown()
        browser_server.server_close()
        browser_thread.join()

# The public custom-LLM bridge: a hosted voice agent's reasoning endpoint, put in
# front of the same durable transport. Reachable from the internet through the
# operator's Funnel, so authentication is proven before any useful behaviour.
import random
import time

from fm_voice_bridge import (ANSWER_MARKER, CASCADE_MARGIN, Endpoint, HOLD_SECONDS,
                             Sessions, ShuffleBag, require_safe_hold)

# A turn still open when the agent's cascade timeout expires ends the captain's
# conversation rather than the turn, so an unsafe hold must never reach a socket.
require_safe_hold(HOLD_SECONDS, 15.0)
require_safe_hold(15.0 - CASCADE_MARGIN, 15.0)
for unsafe in (15.0 - CASCADE_MARGIN + 0.1, 10.0, 12.0, 20.0, -1.0):
    try:
        require_safe_hold(unsafe, 15.0)
        raise AssertionError('a hold of %g was allowed against a 15s cascade timeout' % unsafe)
    except PilotError:
        pass

secret = 'x' * 32
bag_order = random.Random(7)
bridge_session = Sessions(Bridge(pilot, connection), ShuffleBag(['first ack.', 'second ack.'], bag_order),
                          topics=ShuffleBag(['Looking into {topic}.', 'On {topic} now.'], random.Random(11)),
                          hold=0.6)
try:
    Endpoint(0, bridge_session, 'too short')
    raise AssertionError('a weak shared secret must refuse to listen')
except PilotError:
    pass

endpoint = Endpoint(0, bridge_session, secret)
endpoint_origin = 'http://127.0.0.1:' + str(endpoint.server_port)
endpoint_thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
endpoint_thread.start()


heard_so_far = []


def ask(said, expected=200, token=secret, path='/v1/chat/completions', extra=None,
        grow=True, messages=None):
    # The platform resends the whole transcript every turn, one longer each time.
    if messages is None:
        if grow:
            heard_so_far.append({'role': 'user', 'content': said})
        messages = [{'role': 'system', 'content': 'ignored'}] + list(heard_so_far)
    body = {'model': 'x', 'stream': True, 'messages': messages}
    body['elevenlabs_extra_body'] = dict(extra or {})
    body['elevenlabs_extra_body'].setdefault('session_id', 'session-one')
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(endpoint_origin + path, json.dumps(body).encode(), headers)
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    payload = response.read().decode()
    assert response.status == expected, (expected, response.status, payload)
    if response.status != 200:
        return payload
    assert payload.endswith('data: [DONE]\n\n'), payload
    spoken = []
    for line in payload.splitlines():
        if line.startswith('data: ') and line != 'data: [DONE]':
            delta = json.loads(line[6:])['choices'][0]['delta']
            spoken.append(delta.get('content', ''))
    return ''.join(spoken)


try:
    # Authentication comes first: nothing useful happens without the shared secret,
    # and a refusal describes nothing about what lies behind it.
    before = len(owning('audit', cid='live')['requests'])
    for token in (None, '', 'wrong', secret[:-1] + 'y', secret + 'z'):
        assert json.loads(ask('Unauthenticated instruction.', 401, token=token)) == {
            'error': {'message': 'unauthorized'}}
    # An unauthenticated caller changes nothing behind the gate.
    assert len(owning('audit', cid='live')['requests']) == before
    assert ask('Unauthenticated instruction.', 401, token=None, path='/v1/models') == \
        '{"error":{"message":"unauthorized"}}'

    # Authenticated but malformed still refuses without touching the transport.
    ask('', 400)
    assert len(owning('audit', cid='live')['requests']) == before

    # A substantive turn files the captain's own words and acknowledges, varied.
    # The opening words are about what he actually asked, drawn from his own
    # words, and they never assert a finding, a status or a result.
    said = 'Tell me what the research found about option B.'
    first = ask(said, extra={'request_id': 'bridge-1'}).strip()
    assert first in ('Looking into option B.', 'On option B now.'), first
    second = ask('And what about option A?', extra={'request_id': 'bridge-2'})
    assert 'option A' in second, second
    filed = owning('audit', cid='live')['requests']
    assert len(filed) == before + 2
    assert [r['request_id'] for r in filed[-2:]] == ['bridge-1', 'bridge-2']
    assert filed[-1]['previous_turn_id'] == filed[-2]['turn_id']

    # The captain's exact words are filed, never the bridge's paraphrase of them.
    # Older saved turns are accepted first, in the order they were spoken.
    accepted = owning('accept', cid='live')
    while accepted['input']['request_id'] != 'bridge-1':
        accepted = owning('accept', cid='live')
    assert accepted['input']['committed_transcript'] == said
    assert owning('accept', cid='live')['input']['request_id'] == 'bridge-2'

    # Firstmate publishes; the bridge speaks that text verbatim and only once.
    answer = 'Option B is slower by 12.5 seconds, and nothing has been changed yet.'
    run('publish', dict(live_reply, request_id='bridge-1', response_id='bridge-answer',
                        speech_text=answer))
    marker = ANSWER_MARKER + 'bridge-answer]'
    heard = ask(marker)
    # bridge-2 was asked after bridge-1, so this answer is late and says so.
    assert heard.endswith(answer) and heard != answer, heard
    assert 'earlier question' in heard, heard
    assert ask(marker) == '', 'a published answer was spoken twice'
    # A marker naming no published reply is refused, and refusing it consumes
    # nothing, so the platform may retry the same turn.
    unknown = [{'role': 'system', 'content': 'ignored'}] + heard_so_far + [
        {'role': 'user', 'content': ANSWER_MARKER + 'no-such-answer]'}]
    ask('', 400, grow=False, messages=unknown)
    ask('', 400, grow=False, messages=unknown)

    # The bridge never invents: every word it has spoken is either a fixed
    # acknowledgement or text Firstmate actually published.
    assert answer in heard and heard.replace(answer, '').strip() != answer

    # The platform re-invokes the endpoint for its own filler generation and on
    # retries, resending a transcript that has not grown. Nothing may be filed
    # again, or one spoken instruction becomes two dispatched requests.
    settled = len(owning('audit', cid='live')['requests'])
    for _ in range(3):
        assert ask('And what about option A?', grow=False) == ''
    assert len(owning('audit', cid='live')['requests']) == settled

    # A genuine repeat of the same words is a new turn, because the transcript grew.
    assert ask('And what about option A?') != ''
    assert len(owning('audit', cid='live')['requests']) == settled + 1

    # A question whose subject carries a claim gets a neutral opener instead: the
    # bridge must not repeat "the build is broken" back as though it knew.
    neutral = ask('Tell me about why the build is broken.').strip()
    assert neutral in ('first ack.', 'second ack.'), neutral
    assert 'broken' not in neutral and 'build' not in neutral
    again = ask('Why is the deploy failing?').strip()
    assert again in ('first ack.', 'second ack.') and again != neutral, (neutral, again)

    # A transcript carrying no captain turn at all is refused, not guessed at.
    ask('', 400, messages=[{'role': 'system', 'content': 'only a system prompt'}])
    ask('', 400, messages=[])

    # A NEW conversation starts its transcript at one turn again. Turn
    # bookkeeping is per conversation, so that must be heard, not read as a
    # repeat of the previous conversation and answered with silence.
    opening = [{'role': 'system', 'content': 'ignored'},
               {'role': 'user', 'content': 'Tell me what the research found about option B.'}]
    before_new = len(owning('audit', cid='live')['requests'])
    assert ask('', messages=opening, extra={'session_id': 'session-two'}) != ''
    assert len(owning('audit', cid='live')['requests']) == before_new + 1
    # ...and that new conversation keeps its own re-invocation guard.
    assert ask('', messages=opening, extra={'session_id': 'session-two'}) == ''
    assert len(owning('audit', cid='live')['requests']) == before_new + 1

    # A refusal answers before the body is read, so the connection must close
    # rather than leave that body to be parsed as the next request.
    refused = urllib.request.Request(endpoint_origin + '/v1/chat/completions',
                                     json.dumps({'messages': [{'role': 'user', 'content': 'x' * 5000}]}).encode(),
                                     {'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(refused, timeout=10)
        raise AssertionError('an unauthenticated call was answered')
    except urllib.error.HTTPError as error:
        assert error.status == 401
        assert error.headers.get('Connection') == 'close', dict(error.headers)
finally:
    endpoint.shutdown()
    endpoint.server_close()
    endpoint_thread.join()

# The turn is held open, so an answer published while the captain waits continues
# the SAME utterance rather than arriving later as a separate announcement.
holding = Sessions(Bridge(pilot, connection), ShuffleBag(['neutral one.', 'neutral two.'], random.Random(3)),
                   topics=ShuffleBag(['Looking into {topic}.', 'On {topic} now.'], random.Random(5)),
                   hold=25.0)
held = Endpoint(0, holding, secret)
held_origin = 'http://127.0.0.1:' + str(held.server_port)
held_thread = threading.Thread(target=held.serve_forever, daemon=True)
held_thread.start()
spoken_turn = {}


def hold_ask(said, request_id):
    body = {'model': 'x', 'stream': True, 'elevenlabs_extra_body': {'session_id': 'held', 'request_id': request_id},
            'messages': [{'role': 'user', 'content': said}]}
    request = urllib.request.Request(held_origin + '/v1/chat/completions', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + secret})
    payload = urllib.request.urlopen(request, timeout=30).read().decode()
    said_parts = [json.loads(line[6:])['choices'][0]['delta'].get('content', '')
                  for line in payload.splitlines() if line.startswith('data: ') and line != 'data: [DONE]']
    spoken_turn['text'] = ''.join(said_parts)


try:
    answer = 'Option B took twelve and a half seconds, and nothing was changed.'
    turn = threading.Thread(target=hold_ask, args=('Tell me what we found about option B.', 'held-1'))
    turn.start()
    # Firstmate answers while the captain is still on that turn.
    for _ in range(40):
        time.sleep(0.25)
        if any(r['request_id'] == 'held-1' for r in owning('audit', cid='live')['requests']):
            break
    owning('accept', cid='live')
    while True:
        accepted = owning('audit', cid='live')['requests']
        if next(r['state'] for r in accepted if r['request_id'] == 'held-1') == 'accepted':
            break
        owning('accept', cid='live')
    run('publish', dict(live_reply, request_id='held-1', response_id='held-answer', speech_text=answer))
    turn.join(timeout=30)
    assert not turn.is_alive(), 'the held turn never finished'
    heard_turn = spoken_turn['text']
    # One continuous thought: the opener about his words, then the answer verbatim.
    assert heard_turn.startswith(('Looking into option B.', 'On option B now.')), heard_turn
    assert heard_turn.endswith(answer), heard_turn
    # The answer is spoken once; it is no longer waiting for the announcing page.
    delivered = next(r for r in owning('audit', cid='live')['replies'] if r['response_id'] == 'held-answer')
    assert delivered['delivery']['state'] != 'waiting', delivered

    # If the platform hangs up while the turn is held, the turn must end without
    # consuming anything: the answer stays available for the announcing page
    # rather than being marked spoken to a listener who had already gone.
    body = {'model': 'x', 'stream': True,
            'elevenlabs_extra_body': {'session_id': 'held', 'request_id': 'held-2'},
            'messages': [{'role': 'user', 'content': 'x'}, {'role': 'user', 'content': 'And about option C?'}]}
    cut = urllib.request.Request(held_origin + '/v1/chat/completions', json.dumps(body).encode(),
                                 {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + secret})
    while any(r['state'] == 'saved' for r in owning('audit', cid='live')['requests']):
        owning('accept', cid='live')
    stream = urllib.request.urlopen(cut, timeout=30)
    stream.read(1)          # Take the opener, then hang up while the turn is held.
    stream.close()
    while True:
        rows = owning('audit', cid='live')['requests']
        if next((r['state'] for r in rows if r['request_id'] == 'held-2'), None) == 'accepted':
            break
        owning('accept', cid='live')
    run('publish', dict(live_reply, request_id='held-2', response_id='held-unheard',
                        speech_text='An answer nobody was left to hear.'))
    time.sleep(3)
    record = next(r for r in owning('audit', cid='live')['replies'] if r['response_id'] == 'held-unheard')
    assert record['delivery']['state'] == 'waiting', record
finally:
    held.shutdown()
    held.server_close()
    held_thread.join()

print('bridge: unauthenticated refusal before any effect, verbatim publication, varied acknowledgement')
print('bridge: opener drawn from the captain words, answer continuing the same held turn')

# The page that tells the agent an answer is ready, against the real transport
# with the vendor SDK stubbed. No account, agent minute or acoustic claim.
if os.environ.get('FM_VOICE_PLAYWRIGHT_MODULE'):
    run('publish', dict(live_reply, request_id='bridge-2', response_id='agent-answer',
                        speech_text='Synthetic answer for the announcing page.'))
    worklet = temp / 'libsamplerate.worklet.js'
    worklet.write_text('// stand-in for the operator-supplied resampler worklet\n')
    agent_server = Pilot(0, Bridge(pilot, connection), provider, b'agent-lane-ack',
                         agent_id='agent-fixture', agent_worklet=worklet)
    agent_thread = threading.Thread(target=agent_server.serve_forever, daemon=True)
    agent_thread.start()
    try:
        waiting = sum(1 for r in owning('audit', cid='live')['replies']
                      if r['delivery']['state'] == 'waiting')
        subprocess.run(['node', str(root / 'tests/fm-voice-agent-cases.cjs'),
                        agent_server.origin + '/agent#' + agent_server.pair_secret,
                        'agent-fixture', str(waiting)], check=True, timeout=90)
        delivery = next(r for r in owning('audit', cid='live')['replies']
                        if r['response_id'] == 'agent-answer')
        # The page only announces; the bridge is what actually delivers speech.
        assert delivery['delivery']['state'] == 'waiting', delivery
    finally:
        agent_server.shutdown()
        agent_server.server_close()
        agent_thread.join()
    print('agent page: one announcement per published answer, retried when the agent is unreachable')
