"""Hand driver: exercises bin/fm-inbox.sh conversation live in an isolated lab home.

argv: <repo root holding bin/> <tmp dir> <owner pid> <mode fixed|base> <transcript path>
Every command below is a real subprocess call to the public CLI; the transcript
records the payload sent, the exit code, stdout and stderr exactly as returned.
"""
import json, os, subprocess, sys
from pathlib import Path

root, temp, owner, mode, out = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
home = temp / 'home'
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env['FM_HOME'] = str(home)
cli = root / 'bin/fm-inbox.sh'
log = out.open('w')


def say(text=''):
    print(text); log.write(text + '\n'); log.flush()


def run(command, payload):
    r = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, timeout=30)
    say('$ fm-inbox.sh conversation %s <<< %s' % (command, json.dumps(payload)))
    say('  exit=%d' % r.returncode)
    if r.stdout.strip():
        say('  stdout: ' + r.stdout.strip())
    if r.stderr.strip():
        say('  stderr: ' + r.stderr.strip())
    say()
    return r


def ok(command, payload):
    r = run(command, payload)
    assert r.returncode == 0, (command, r)
    return json.loads(r.stdout)


def check(cond, label):
    say(('CHECK PASS: ' if cond else 'CHECK FAIL: ') + label); say()
    assert cond, label


catalog = {'answer': 'Option A is probably faster, but it does not support offline use.'}
ok('lab-init', {'speech_catalog': catalog})
(home / 'state/.lock').write_text(owner + '\n')


def bind(cid):
    return ok('bind', {'conversation_id': cid, 'authenticated_principal': 'captain'})


def turn(n, previous=None, cid='captain-voice-2026-09-10', **kw):
    return dict(conn[cid], turn_id='t%d' % n, request_id='r%d' % n, revision=1, previous_turn_id=previous,
                created_at='2026-09-12T05:%02d:00Z' % n, committed_transcript='Spoken turn %d' % n, **kw)


def audit(cid):
    return ok('audit', {'conversation_id': cid})['requests']


def table(cid):
    say('  audit of %s (capture order):' % cid)
    for r in audit(cid):
        say('    %-3s call_id=%-10s state=%-8s reason=%s' % (r['request_id'], r.get('call_id'), r['state'], r.get('reason')))
    say()


def supersede_pass(cid):
    """SKILL.md step 1: newest captured call_id is live; reject other saved rows; restart on refusal."""
    while True:
        live = audit(cid)[-1].get('call_id')
        say('  step 1: newest captured request names live call_id=%s' % live)
        if live is None:
            say('  step 1: newest carries no call_id; nothing superseded'); say()
            return live
        refused = False
        yield_hook()
        for r in audit(cid):
            if r['state'] == 'saved' and r.get('call_id') != live:
                res = run('reject', {'conversation_id': cid, 'request_id': r['request_id'],
                                     'reason': 'prior call ended; superseded'})
                if res.returncode != 0:
                    check(res.returncode == 2 and 'not superseded' in res.stderr, 'reject refused as not superseded')
                    refused = True
                    break
        if not refused:
            return live


hook = [None]


def yield_hook():
    if hook[0]:
        f, hook[0] = hook[0], None
        f()


conn = {}
say('=== mode: %s  (bin from %s) ===' % (mode, root)); say()

say('### S1: the 2026-09-12 shape - three turns of an ended call sit in front of the live question')
cid = 'captain-voice-2026-09-10'
conn[cid] = bind(cid)
if mode == 'base':
    r = run('capture', turn(1, call_id='CA-ended'))
    check(r.returncode == 2 and 'unsupported input fields' in r.stderr, 'BASE: the bridge cannot even record call_id')
    for n, prev in ((1, None), (2, 't1'), (3, 't2'), (4, 't3')):
        ok('capture', turn(n, prev))
    table(cid)
    first = ok('accept', {'conversation_id': cid})
    check(first['input']['request_id'] == 'r1', 'BASE: first accept returns r1, a turn from the ended call, ahead of the live r4 (the reported failure)')
    sys.exit(0)

for n, prev in ((1, None), (2, 't1'), (3, 't2')):
    ok('capture', turn(n, prev, call_id='CA-ended'))
ok('capture', turn(4, 't3', call_id='CA-live'))
table(cid)
oldest = [r for r in audit(cid) if r['state'] == 'saved'][0]
check(oldest['request_id'] == 'r1' and oldest['call_id'] == 'CA-ended', 'the oldest saved row (what the drain wakes on) belongs to the ended call')
live = supersede_pass(cid)
check(live == 'CA-live', 'live call resolved from the newest capture')
table(cid)
first = ok('accept', {'conversation_id': cid})
check(first['dispatch'] is True and first['input']['request_id'] == 'r4' and first['input']['call_id'] == 'CA-live',
      'first accept returns r4 of the live call, not the stale r1')
second = ok('accept', {'conversation_id': cid})
check(second['dispatch'] is False, 'nothing else waits behind the live turn')
check([r['reason'] for r in audit(cid)[:3]] == ['prior call ended; superseded'] * 3, 'r1-r3 carry the superseded reason on record')
say('  persisted note for r1 (state/inbox/handled):')
key = 'vc-' + __import__('hashlib').sha256(json.dumps([cid, 'r1']).encode()).hexdigest()[:16]
notes = sorted((home / 'state/inbox/handled').glob('vc-*.note'))
say('    %d handled notes: %s' % (len(notes), ', '.join(p.name for p in notes))); say()

say('### S2: the captain redials while the owner is between audit and its rejects')
cid = 'redial'
conn[cid] = bind(cid)
ok('capture', turn(4, None, cid=cid, call_id='CA-2'))
check(audit(cid)[-1]['call_id'] == 'CA-2', 'audit says the newest call is CA-2 before the pass starts')
# Stale pass: the owner computed live=CA-2, then CA-3 lands, then the owner tries to reject everything not CA-2.
stale_live = audit(cid)[-1]['call_id']
say('  owner snapshot: live=%s' % stale_live)
ok('capture', turn(5, 't4', cid=cid, call_id='CA-3'))
say('  ...bridge captured r5 on CA-3 after the audit...'); say()
res = run('reject', {'conversation_id': cid, 'request_id': 'r5', 'reason': 'prior call ended; superseded'})
check(res.returncode == 2 and 'live call CA-3; not superseded' in res.stderr, 'rejecting the redial turn as superseded is refused and names CA-3')
check(audit(cid)[-1]['state'] == 'saved', 'r5 is still saved after the refusal')
live = supersede_pass(cid)
check(live == 'CA-3', 'restarted pass resolves CA-3 as live')
table(cid)
check([(r['request_id'], r['state']) for r in audit(cid)] == [('r4', 'rejected'), ('r5', 'saved')], 'r4 superseded, r5 untouched')
first = ok('accept', {'conversation_id': cid})
check(first['input']['request_id'] == 'r5' and first['input']['call_id'] == 'CA-3', 'accept returns the redial turn r5')
check(ok('accept', {'conversation_id': cid})['dispatch'] is False, 'nothing else waits')

say('### S2b: the redial lands after the live call was computed and before the saved rows are listed for rejection; the pass restarts by itself')
say('  (a redial landing AFTER the last reject and before accept is the accept-window tradeoff declined in review round 3 and is not exercised here)'); say()
cid = 'redial2'
conn[cid] = bind(cid)
ok('capture', turn(1, None, cid=cid, call_id='CA-a'))
ok('capture', turn(2, 't1', cid=cid, call_id='CA-b'))
hook[0] = lambda: ok('capture', turn(3, 't2', cid=cid, call_id='CA-c'))
live = supersede_pass(cid)
check(live == 'CA-c', 'pass restarted and resolved CA-c')
table(cid)
check([(r['request_id'], r['state']) for r in audit(cid)] == [('r1', 'rejected'), ('r2', 'rejected'), ('r3', 'saved')], 'both earlier calls superseded; the redial stays saved')
check(ok('accept', {'conversation_id': cid})['input']['request_id'] == 'r3', 'accept returns r3 of CA-c')

say('### S3: back-compat - the newest capture carries no call_id, so nothing is superseded and order is oldest-first')
cid = 'legacy'
conn[cid] = bind(cid)
ok('capture', turn(1, None, cid=cid, call_id='CA-named'))
ok('capture', turn(2, 't1', cid=cid))
table(cid)
path = home / 'state/inbox'
bodies = {p.name: json.loads(p.read_text().split('\n--\n', 1)[1]) for p in path.glob('vc-*.note')}
r2 = [b for b in bodies.values() if b['request_id'] == 'r2'][0]
check('call_id' not in r2, 'persisted note body of a nameless capture has no call_id key at all (identity unchanged from pre-field release)')
check(audit(cid)[-1]['call_id'] is None, 'audit reports call_id null for the nameless newest request')
live = supersede_pass(cid)
check(live is None, 'no live call to scope by')
check([r['state'] for r in audit(cid)] == ['saved', 'saved'], 'nothing rejected')
check(ok('accept', {'conversation_id': cid})['input']['request_id'] == 'r1', 'accept still returns oldest first (r1)')
check(ok('accept', {'conversation_id': cid})['input']['request_id'] == 'r2', 'then r2')
say('  the nameless newest turn: reject with the superseded reason refuses nothing (no live call), so it is simply rejected')
ok('capture', turn(3, 't2', cid=cid, call_id='CA-x'))
ok('capture', turn(4, 't3', cid=cid))
res = run('reject', {'conversation_id': cid, 'request_id': 'r4', 'reason': 'prior call ended; superseded'})
check(res.returncode == 0 and json.loads(res.stdout)['state'] == 'rejected', 'nameless newest turn is not protected by the refusal')

say('### S4: adversarial edges of the refusal and of call_id identity')
cid = 'edges'
conn[cid] = bind(cid)
ok('capture', turn(1, None, cid=cid, call_id='CA-1'))
ok('capture', turn(2, 't1', cid=cid, call_id='CA-2'))
res = run('reject', {'conversation_id': cid, 'request_id': 'r2', 'reason': 'prior call ended; superseded'})
check(res.returncode == 2, 'the live call turn cannot be retired as superseded')
res = run('reject', {'conversation_id': cid, 'request_id': 'r2', 'reason': 'prior call ended; superseded '})
check(res.returncode == 0, 'REGRESSION PROBE: a reason differing by trailing whitespace is NOT refused (exact-string match)') if res.returncode == 0 else check(res.returncode == 2, 'whitespace variant refused')
table(cid)
res = run('reject', {'conversation_id': cid, 'request_id': 'r1', 'reason': 'prior call ended; superseded'})
check(res.returncode == 0, 'the earlier call turn is superseded normally')
ok('capture', turn(3, 't2', cid=cid, call_id='CA-2'))
res = run('reject', {'conversation_id': cid, 'request_id': 'r3', 'reason': 'say that again'})
check(res.returncode == 0 and json.loads(res.stdout)['state'] == 'rejected', 'a live-call turn can still be rejected for its own reason')
ok('capture', turn(4, 't3', cid=cid, call_id='CA-2'))
res = run('capture', turn(4, 't3', cid=cid, call_id='CA-2'))
check(res.returncode == 0, 'exact retry of a capture is idempotent')
res = run('capture', turn(4, 't3', cid=cid, call_id='CA-relabelled'))
check(res.returncode == 2, 'retry with a different call_id is refused: call_id is immutable identity')
res = run('capture', turn(5, 't4', cid=cid, call_id=5))
check(res.returncode == 2 and 'invalid call_id' in res.stderr, 'non-identifier call_id refused')
res = run('capture', turn(5, 't4', cid=cid, call_id=''))
check(res.returncode == 2, 'empty call_id refused')
res = run('capture', dict(turn(5, 't4', cid=cid), call_id=None))
check(res.returncode == 0 and 'call_id' not in json.loads(res.stdout).get('input', {}), 'explicit null call_id is treated as absent')
table(cid)
say('=== ALL CHECKS PASSED ===')
