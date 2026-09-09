#!/usr/bin/env python3
"""Offline conversation contract, composed by fm-inbox.sh conversation.

This first-stage lab makes no network/model/media call. lab-init requires a new
absolute FM_HOME and JSON {"speech_catalog": {"name": "synthetic speech"}}.
It creates that directory exclusively, never seeds or adopts an operational home.
The catalog is the entire publication scope: publication selects a catalog key,
never arbitrary reply text or private records. This is NOT a live disclosure
policy. A production session adapter and owner-approved disclosure design must
precede live use. All commands take one JSON object on stdin and return JSON.
Use the shell entry point; owner commands require its existing session-lock seam
and main-actor role partition (a Pi supervision branch shares main's process).
The local OS owner is trusted; no CLI or owner credential is a model tool.

bind (owner): {conversation_id, authenticated_principal}. Returns a random
    transport credential bound to this owner process identity and conversation.
    Repeat bind is idempotent; another owner cannot adopt the conversation.
    This models pairing; it does not implement browser authentication.
All remaining commands require conversation_id. Transport commands also require
    credential. Owner commands accept only the currently bound session owner.
capture (transport): {turn_id, request_id, committed_transcript, revision,
    previous_turn_id, created_at, correction_of?, question_binding?}.
    Principal comes from pairing, not input. revision is a positive integer.
    A committed turn is immutable; corrections get new turn/request IDs and an
    explicit correction_of. Out-of-order completions wait for their predecessor.
    Only committed input enters this interface; provisional events are refused.
accept (owner): returns the oldest eligible input ONCE, with the prior playback
    context. Acceptance is committed BEFORE returning dispatch:true; a crash at
    this boundary leaves an accepted request with uncertain work state, never an
    automatic second dispatch. Recovery requires owner inspection (audit).
reject (owner): {request_id, reason}. Declines an unaccepted input explicitly;
    retains its transcript and reason without running it or cancelling work.
publish (owner): {request_id, response_id, sequence, kind, speech_key, final,
    question_binding?}. kind is receipt/progress/question/answer/error; sequence
    starts at 1 per request. IDs are immutable and retries must match exactly.
    Questions require a unique binding. A bound answer consumes that question at
    acceptance. Missing, ambiguous or stale bindings never become approvals.
    Explicit work outcome is separate from playback: final closes this request's
    reply stream, not its action state. No model interprets or dispatches actions.
    A rejected input may receive an error or clarification question, never a
    receipt/progress/answer that could imply its work was accepted.
poll (transport): returns this conversation's request states and published reply
    headers, with delivery state and question bindings. No raw inputs, owner
    identity or record reader is exposed. Polling discovers asynchronous results;
    it never claims playback, reaccepts input or blocks waiting for work to finish.
deliver (transport): {response_id, generation}. Claims one playback generation
    before returning speech. A repeated claim yields unknown, never more audio.
    Replaying uncertain/interrupted/completed speech is not automatic.
playback (transport): {response_id, generation, position_ms,
    state: interrupted|completed|unknown}. Positions approximate playback, not
    proof of words heard. Terminal records are immutable; stale generations fail.
audit (owner): returns durable input/reply/delivery accounting, no credentials.

state/inbox/vc-<sha256>.note is the sole request record, using the existing inbox
header and a JSON body; handled/ retains accepted notes. The serialized transport
journal state/voice-conversation/journal.json holds identity hashes, acceptance,
reply publication and playback receipts, not a task backlog. A durable note
precedes its mapping; every command recovers orphan notes before proceeding.
Journal and note publication use fsync + rename + directory fsync under one flock.
Acceptance precedes moving a note to handled; recovery completes that move.
Wake delivery is at least once and contains IDs only. Wake retries cannot repeat
acceptance. No raw session, source, backlog, note text or audio archive is read.
An unresolved work outcome or playback receipt stays visible rather than replaying.

FM_VOICE_FAULT is a lab-only process-exit injection after note, mapping, accept,
publication, delivery or playback commit, used by tests/fm-inbox-conversation.test.sh.
This module owns the lab schema/state machine; docs/voice-relay.md routes to it.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


class ContractError(Exception):
    """An input cannot safely advance the conversation."""


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def string(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 16000


def identifier(value):
    return string(value) and len(value) <= 200 and not any(ord(c) < 32 for c in value)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, value):
    tmp = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    try:
        with tmp.open('x', encoding='utf-8') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def fault(point):
    if os.environ.get('FM_VOICE_FAULT') == point:
        os._exit(86)


def init(home, payload):
    catalog = payload.get('speech_catalog')
    require(isinstance(catalog, dict) and catalog and
            all(identifier(k) and string(v) for k, v in catalog.items()),
            'lab-init requires an explicit synthetic speech catalog')
    require(home.is_absolute() and not home.exists(), 'lab-init requires a new absolute home')
    home.mkdir(mode=0o700)
    sync_dir(home.parent)
    for name in ('state', 'state/inbox', 'state/inbox/handled', 'state/voice-conversation'):
        (home / name).mkdir(mode=0o700)
        sync_dir((home / name).parent)
    write(home / 'state/voice-conversation/catalog.json', canonical(catalog))
    write(home / '.voice-conversation-lab', 'synthetic-only-v1\n')
    return {'lab': True, 'network': False}


class Conversation:
    def __init__(self, home, command, payload):
        self.home, self.command, self.p = home, command, payload
        self.state = home / 'state'
        self.root = self.state / 'voice-conversation'
        self.inbox = self.state / 'inbox'
        require((home / '.voice-conversation-lab').read_text() == 'synthetic-only-v1\n',
                'only an initialized synthetic lab is supported')
        self.catalog = json.loads((self.root / 'catalog.json').read_text())
        self.path = self.root / 'journal.json'
        self.lock = (self.root / 'lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.j = json.loads(self.path.read_text()) if self.path.exists() else {
            'version': 1, 'conversations': {}, 'requests': {}, 'replies': {}}
        require(self.j.get('version') == 1, 'unsupported journal version')
        self.cid = payload.get('conversation_id')
        require(identifier(self.cid), 'conversation_id is required')
        self.c = self.j['conversations'].get(self.cid)
        if command != 'bind':
            require(self.c is not None, 'unknown conversation')
            if command in ('accept', 'reject', 'publish', 'audit'):
                require(self.c['owner'] == os.environ.get('FM_VOICE_OWNER'), 'wrong owning session')
            else:
                require(secrets.compare_digest(str(payload.get('credential', '')), self.c['credential']),
                        'wrong transport credential')
        self.recover()

    def save(self):
        write(self.path, canonical(self.j))

    def note_path(self, key):
        pending = self.inbox / (key + '.note')
        return pending if pending.exists() else self.inbox / 'handled' / (key + '.note')

    def event(self, key):
        return json.loads(self.note_path(key).read_text().split('\n--\n', 1)[1])

    def recover(self):
        changed = False
        for folder in (self.inbox, self.inbox / 'handled'):
            for path in sorted(folder.glob('vc-*.note')):
                event = json.loads(path.read_text().split('\n--\n', 1)[1])
                key = path.stem
                require(key == self.key(event['conversation_id'], event['request_id']), 'note identity mismatch')
                require(event['conversation_id'] in self.j['conversations'], 'orphan conversation')
                row = self.j['requests'].get(key)
                if row is None:
                    require(folder == self.inbox, 'handled note has no acceptance journal')
                    self.j['requests'][key] = {'hash': digest(event), 'conversation_id': event['conversation_id'],
                                              'state': 'saved', 'order': len(self.j['requests']) + 1}
                    changed = True
                else:
                    require(row['hash'] == digest(event), 'request identity conflict')
        if changed:
            self.save()
        for key, row in self.j['requests'].items():
            require(self.note_path(key).is_file(), 'journal request has no durable inbox note')
            if row['state'] in ('accepted', 'rejected'):
                pending = self.inbox / (key + '.note')
                if pending.exists():
                    os.replace(pending, self.inbox / 'handled' / pending.name)
                    sync_dir(self.inbox / 'handled')
                    sync_dir(self.inbox)

    @staticmethod
    def key(cid, rid):
        return 'vc-' + digest([cid, rid])

    def rows(self):
        return [(key, row, self.event(key)) for key, row in
                sorted(self.j['requests'].items(), key=lambda pair: pair[1]['order'])
                if row['conversation_id'] == self.cid]

    def bind(self):
        owner = os.environ.get('FM_VOICE_OWNER')
        principal = self.p.get('authenticated_principal')
        require(string(owner) and identifier(principal), 'owner and authenticated principal are required')
        if self.c:
            require(self.c['owner'] == owner and self.c['principal'] == principal, 'conversation already bound')
        else:
            self.c = {'owner': owner, 'principal': principal, 'credential': secrets.token_urlsafe(32)}
            self.j['conversations'][self.cid] = self.c
            self.save()
        return {'conversation_id': self.cid, 'credential': self.c['credential']}

    def wake(self, key):
        env = dict(os.environ, FM_HOME=str(self.home))
        lib = Path(__file__).with_name('fm-wake-lib.sh')
        result = subprocess.run(['bash', '-c',
                                 '. "$1"; fm_wake_append check "inbox:$2" '
                                 '"check: conversation inbox note $2; use fm-inbox.sh conversation accept"',
                                 'voice-wake', str(lib), key], env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
        require(result.returncode == 0, 'input saved; wake failed; retry capture to announce it')

    def capture(self):
        fields = ('turn_id', 'request_id', 'committed_transcript', 'revision', 'previous_turn_id',
                  'created_at', 'correction_of', 'question_binding')
        require(not (set(self.p) - set(fields) - {'credential', 'conversation_id'}), 'unsupported input fields')
        event = {k: self.p.get(k) for k in fields}
        for field in ('turn_id', 'request_id', 'created_at'):
            require(identifier(event[field]), field + ' is required')
        require(string(event['committed_transcript']), 'committed transcript is required')
        require(integer(event['revision'], 1), 'revision must be a positive integer')
        for field in ('previous_turn_id', 'correction_of', 'question_binding'):
            require(event[field] is None or identifier(event[field]), 'invalid ' + field)
        require(event['previous_turn_id'] != event['turn_id'], 'turn cannot follow itself')
        event.update(conversation_id=self.cid, authenticated_principal=self.c['principal'])
        key = self.key(self.cid, event['request_id'])
        old = self.j['requests'].get(key)
        if old:
            require(old['hash'] == digest(event), 'request ID reused with different input')
        else:
            rows = self.rows()
            require(not any(e['turn_id'] == event['turn_id'] for _, _, e in rows),
                    'turn already committed; send an explicit correction as a new turn')
            require(not any(e['previous_turn_id'] == event['previous_turn_id'] for _, _, e in rows),
                    'input order branches; reconcile transcript order')
            # A missing predecessor is allowed; cycles are not.
            predecessors = {e['turn_id']: e['previous_turn_id'] for _, _, e in rows}
            previous = event['previous_turn_id']
            while previous in predecessors:
                previous = predecessors[previous]
                require(previous != event['turn_id'], 'input order cycle')
            note = 'id={}\nsource=voice-conversation\n--\n{}\n'.format(key, canonical(event))
            write(self.inbox / (key + '.note'), note)
            fault('note')
            self.recover()
            fault('mapping')
        if self.j['requests'][key]['state'] == 'saved':
            self.wake(key)
        return {'request_id': event['request_id'], 'state': self.j['requests'][key]['state']}

    def accept(self):
        rows = self.rows()
        accepted = {e['turn_id'] for _, r, e in rows if r['state'] in ('accepted', 'rejected')}
        for key, row, event in sorted(rows, key=lambda triple: triple[1]['order']):
            if row['state'] != 'saved':
                continue
            previous = event['previous_turn_id']
            if previous is not None and previous not in accepted:
                continue
            correction = event['correction_of']
            if correction is not None:
                target = self.j['requests'].get(self.key(self.cid, correction))
                require(target is not None and target['state'] == 'accepted', 'correction target not accepted here')
            binding = event['question_binding']
            if binding is not None:
                questions = [r for r in self.j['replies'].values()
                             if r['conversation_id'] == self.cid and r['event']['kind'] == 'question'
                             and r['event']['question_binding'] == binding]
                require(len(questions) == 1 and not questions[0].get('consumed_by'), 'stale or unknown question binding')
                questions[0]['consumed_by'] = event['request_id']
            row['state'] = 'accepted'
            self.save()
            fault('accept')
            self.recover()
            context = [{'response_id': r['event']['response_id'], 'delivery': r.get('delivery')}
                       for r in self.j['replies'].values() if r['conversation_id'] == self.cid]
            return {'dispatch': True, 'input': event, 'playback_context': context}
        return {'dispatch': False}

    def reject(self):
        require(identifier(self.p.get('request_id')) and string(self.p.get('reason')),
                'request_id and rejection reason are required')
        row = self.j['requests'].get(self.key(self.cid, self.p['request_id']))
        require(row is not None and row['state'] != 'accepted', 'cannot reject unknown or accepted input')
        require(row['state'] != 'rejected' or row['reason'] == self.p['reason'], 'conflicting rejection')
        row.update(state='rejected', reason=self.p['reason'])
        self.save()
        self.recover()
        return {'request_id': self.p['request_id'], 'state': 'rejected'}

    def publish(self):
        fields = ('request_id', 'response_id', 'sequence', 'kind', 'speech_key', 'final', 'question_binding')
        require(not (set(self.p) - set(fields) - {'conversation_id'}), 'unsupported publication fields')
        event = {k: self.p.get(k) for k in fields}
        require(identifier(event['request_id']) and identifier(event['response_id']), 'reply identity is required')
        require(integer(event['sequence'], 1) and type(event['final']) is bool, 'invalid sequence or final flag')
        require(event['kind'] in ('receipt', 'progress', 'question', 'answer', 'error'), 'invalid reply kind')
        require(event['speech_key'] in self.catalog, 'speech must select the synthetic catalog; live disclosure is gated')
        binding = event['question_binding']
        require(identifier(binding) if event['kind'] == 'question' else binding is None, 'invalid question binding')
        request = self.j['requests'].get(self.key(self.cid, event['request_id']))
        require(request is not None and (request['state'] == 'accepted' or
                (request['state'] == 'rejected' and event['kind'] in ('error', 'question'))),
                'request has not been accepted or explicitly rejected here')
        key = self.key(self.cid, event['response_id'])
        old = self.j['replies'].get(key)
        if old:
            require(old['event'] == event, 'response ID reused with different publication')
        else:
            stream = [r['event'] for r in self.j['replies'].values()
                      if r['conversation_id'] == self.cid and r['event']['request_id'] == event['request_id']]
            require(event['sequence'] == len(stream) + 1 and not any(r['final'] for r in stream),
                    'reply stream closed or sequence is not next')
            if binding:
                require(not any(r['conversation_id'] == self.cid and r['event']['question_binding'] == binding
                                for r in self.j['replies'].values()), 'question binding already used')
            self.j['replies'][key] = {'conversation_id': self.cid, 'event': event,
                                      'order': len(self.j['replies']) + 1,
                                      'speech_text': self.catalog[event['speech_key']],
                                      'disclosure': {'scope': 'synthetic-catalog', 'digest': digest(self.catalog[event['speech_key']])}}
            self.save()
            fault('publication')
        return {'published': True, 'response_id': event['response_id']}

    def poll(self):
        requests = [{'request_id': e['request_id'], 'turn_id': e['turn_id'],
                     'previous_turn_id': e['previous_turn_id'], 'state': r['state']}
                    for _, r, e in self.rows()]
        replies = []
        for row in sorted(self.j['replies'].values(), key=lambda value: value['order']):
            if row['conversation_id'] != self.cid:
                continue
            event = row['event']
            replies.append({k: event[k] for k in ('request_id', 'response_id', 'sequence',
                                                 'kind', 'final', 'question_binding')})
            replies[-1]['delivery'] = row.get('delivery', {'state': 'waiting'})
            replies[-1]['question_open'] = event['kind'] == 'question' and not row.get('consumed_by')
        return {'conversation_id': self.cid, 'requests': requests, 'replies': replies}

    def reply(self):
        rid = self.p.get('response_id')
        require(identifier(rid), 'response_id is required')
        row = self.j['replies'].get(self.key(self.cid, rid))
        require(row is not None, 'reply is not published in this conversation')
        require(identifier(self.p.get('generation')), 'generation is required')
        return row

    def deliver(self):
        row = self.reply()
        delivery = row.get('delivery')
        if delivery:
            return {'deliver': False, 'state': delivery['state'], 'response_id': self.p['response_id']}
        row['delivery'] = {'generation': self.p['generation'], 'state': 'unknown', 'position_ms': 0}
        self.save()
        fault('delivery')
        return {'deliver': True, 'response_id': self.p['response_id'], 'generation': self.p['generation'],
                'speech_text': row['speech_text'], 'disclosure': row['disclosure']}

    def playback(self):
        row = self.reply()
        old = row.get('delivery')
        require(old is not None and old['generation'] == self.p['generation'], 'stale or unclaimed audio generation')
        value = {k: self.p.get(k) for k in ('generation', 'state', 'position_ms')}
        require(value['state'] in ('interrupted', 'completed', 'unknown') and integer(value['position_ms']),
                'invalid playback receipt')
        require(value == old or (old['state'] == 'unknown' and value['position_ms'] >= old['position_ms']),
                'playback receipt conflicts with durable state')
        row['delivery'] = value
        self.save()
        fault('playback')
        return value

    def audit(self):
        requests = [{'request_id': e['request_id'], 'turn_id': e['turn_id'], 'state': r['state'],
                     'note_id': key, 'reason': r.get('reason'),
                     'previous_turn_id': e['previous_turn_id'], 'correction_of': e['correction_of'],
                     'question_binding': e['question_binding']} for key, r, e in self.rows()]
        replies = [{'request_id': r['event']['request_id'], 'response_id': r['event']['response_id'],
                    'final': r['event']['final'], 'delivery': r.get('delivery', {'state': 'waiting'})}
                   for r in self.j['replies'].values() if r['conversation_id'] == self.cid]
        return {'conversation_id': self.cid, 'requests': requests, 'replies': replies,
                'work_outcome': 'Firstmate-owned; acceptance and playback do not prove action completion'}


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ('--help', '-h', 'help'):
        print(__doc__)
        return 0
    command = sys.argv[1]
    try:
        require(command in ('lab-init', 'bind', 'capture', 'accept', 'reject', 'publish', 'poll',
                            'deliver', 'playback', 'audit'),
                'unknown conversation command')
        payload = json.load(sys.stdin)
        require(isinstance(payload, dict), 'expected a JSON object')
        require(string(os.environ.get('FM_HOME')), 'explicit FM_HOME is required')
        home = Path(os.environ['FM_HOME'])
        os.umask(0o077)
        if command == 'lab-init':
            result = init(home, payload)
        else:
            controller = Conversation(home, command, payload)
            try:
                result = getattr(controller, command)()
            finally:
                controller.lock.close()
        print(canonical(result))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print('fm-inbox conversation: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
