#!/usr/bin/env python3
"""Firstmate's side of the courier iMessage conversation, run as Firstmate.

Usage: fm-courier-pickup.py run | once
Opt in with FM_NOTIFY_COURIER=1, the same switch that routes fm-notify through
the courier; absent/off exits 3 without I/O, so the direct iMessage bridge stays
the only path until the courier is activated. Run one instance at a time with
an explicit FM_HOME; a second instance exits 1 on the state lock.

The wire is firstmate-voice's docs/courier-inbound-interface.txt, its single
owner. This program reads the courier's spool and receipts and writes only its
own outbox requests, its own state and Firstmate's conversation transport: it
never writes, renames or deletes a courier file, reads no courier credential,
state or ledger, and authenticates as nobody but this home's transport peer.

Pickup. /srv/courier/inbound is rescanned every 0.1 s (the contract's 250 ms
ceiling). A record is accepted only when its name matches
NNNNNNNNNNNN-(message|vote)-KEY.json, it opens O_NOFOLLOW as a regular
single-link 0640 file owned by the courier user, at most 128 KiB, parses as
strict UTF-8 JSON (no duplicate keys or nonstandard numbers) with exactly the
v1 fields, and its seq, type and key match its name. Records are handled one at
a time in sequence order. state/courier-pickup/state.json holds the highest
sequence handled (the cursor) and every handled Linq message id and vote key,
so a message is filed once across restarts; a malformed or duplicate record is refused, logged by
name only and passed, and the courier's own pickup deadline tells the captain.

A message record is filed exactly as the direct bridge files it: capture with
turn_id imsg-<message_id>, request_id imsg-req-<message_id>, the conversation's
last turn as previous_turn_id, created_at and the record's transcript, written
down before the first attempt so every retry is byte-identical. capture saves
the vc- note and appends its check wake, which the watcher's conversation ring
surfaces within about a second (docs/watcher-continuity.md). A capture that
fails is retried after 5 and 20 seconds, then, unless the transport shows it
landed, the captain is told dispatch FAILURE text. An empty transcript is not
filed and he is told NOT_TEXT; other_parts or an unsaved attachment adds the
direct bridge's PARTLY_FILED or PARTLY_TEXT sentence once it is filed. A vote
record is filed as his answer only when its request_id names a question poll
this program published and is still watching, with that question's binding and
the first chosen label as the transcript, at most once per question.

Replies. Every 2 s the transport's poll advances each message's stage
(accepted -> working, rejected -> failed), stops watching answered questions,
and claims each waiting reply bound for imessage with deliver. Each text to him
is one outbox ID.json for the policy captain read from /etc/courier/policy.toml
(root- or self-owned, not group/world-writable), ID derived from the claim or
the fixed sentence's key, published 0640 by fsync, rename and directory fsync.
A question whose text lists 2-10 numbered options carries them as
poll_options. Texts go one at a time in order: the next is published only after
the courier's result receipt for the previous one (or after 300 s once the
courier consumed it without one). A sent receipt records playback completed and
moves the stage the reply implies (receipt/progress working, question question,
error failed, final answer done). Stages are published as ID.stage.json in
order and never backwards: filed < working < question < done, with failed
ranked alongside done and both terminal, as the courier applies them.

FM_COURIER_ROOT (default /) relocates srv/courier and etc/courier for offline
tests; FM_COURIER_USER (default courier) names the record and receipt owner.
The binding is FM_HOME/state/imessage/binding.json, exactly
{conversation_id, credential, destination: "imessage"}. Refusals exit 1,
usage 2.
"""

import calendar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
import tomllib

NAME = re.compile(r'([0-9]{12})-(message|vote)-([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.json\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')
E164 = re.compile(r'\+[1-9][0-9]{1,14}\Z')
OPTION = re.compile(r'\A[ \t]*(\d{1,2})[.)][ \t]+(\S.*?)[ \t]*\Z')
LIMIT = 131072
COMMON = {'kind', 'version', 'seq', 'type', 'published_at'}
FIELDS = {'message': COMMON | {'message_id', 'chat_id', 'created_at', 'transcript', 'attachments', 'other_parts'},
          'vote': COMMON | {'request_id', 'digest', 'poll_message_id', 'chosen'}}
SCAN, REPLY_EVERY, RETRY_DELAYS, UNRECEIPTED, POLL_WATCH, KEEP = 0.1, 2.0, (5, 20), 300, 86400, 31 * 86400
# The direct bridge's sentences (firstmate-voice bridge.dispatch / bridge.imessage.poller).
FAILURE = "I couldn't reach Firstmate."
NOT_TEXT = 'Only text and attachments reach Firstmate from here, so that message was not filed.'
PARTLY_TEXT = 'Only the text of that message reached Firstmate; the attachment did not.'
PARTLY_FILED = 'Firstmate has that message, but not everything attached to it.'
STAGE_OF_KIND = {'receipt': 'working', 'progress': 'working', 'question': 'question', 'error': 'failed'}
RANK = {'filed': 0, 'working': 1, 'question': 2, 'done': 3, 'failed': 3}
GAVE_UP = ('denied', 'denied-limit', 'closed', 'failed', 'refused')


class Refused(Exception):
    """A record, file or configuration cannot be trusted; never retried as is."""


def log(message):
    print('fm-courier-pickup: ' + message, file=sys.stderr, flush=True)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def hashed(*parts):
    return hashlib.sha256(canonical(list(parts)).encode()).hexdigest()


def strict_json(data):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise Refused('duplicate key')
        return dict(items)

    def constant(name):
        raise Refused('nonstandard number ' + name)
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused('invalid JSON') from exc


def directory(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def read_owned(parent, name, uid, mode, limit=LIMIT):
    """One regular single-link file under ``parent`` with exact owner and mode."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != mode or info.st_size > limit):
            raise Refused('untrusted file metadata')
        with os.fdopen(os.dup(fd), 'rb') as handle:
            data = handle.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise Refused('file too large')
    return data


def options(text):
    """The numbered options a question offers, as written, or None (the direct bridge's rule)."""
    found = [(int(m.group(1)), line.strip()) for line in text.splitlines() for m in [OPTION.match(line)] if m]
    numbers = [n for n, _ in found]
    if not 2 <= len(found) <= 10 or numbers != list(range(1, len(found) + 1)):
        return None
    if any(len(option) > 150 for _, option in found):
        return None
    return [option for _, option in found]


def validate(name, record):
    """The record when it is exactly a v1 record of the type and key its name says."""
    seq, kind, key = NAME.fullmatch(name).groups()
    if not isinstance(record, dict) or set(record) != FIELDS[kind]:
        raise Refused('unexpected fields')
    if (record['kind'] != 'courier-inbound' or record['version'] != 1 or type(record['version']) is not int
            or type(record['seq']) is not int or record['seq'] != int(seq) or record['type'] != kind
            or not isinstance(record['published_at'], str) or not STAMP.fullmatch(record['published_at'])):
        raise Refused('record does not match its name')
    if kind == 'message':
        attachments = record['attachments']
        if (record['message_id'] != key or not isinstance(record['chat_id'], str) or not record['chat_id']
                or not isinstance(record['created_at'], str) or not STAMP.fullmatch(record['created_at'])
                or not isinstance(record['transcript'], str) or len(record['transcript']) > 16000
                or type(record['other_parts']) is not int or record['other_parts'] < 0
                or not isinstance(attachments, list)
                or not all(isinstance(a, dict) and set(a) == {'line', 'saved'} and isinstance(a['line'], str)
                           and type(a['saved']) is bool for a in attachments)):
            raise Refused('invalid message record')
    elif (not isinstance(record['request_id'], str) or not ID.fullmatch(record['request_id'])
          or not isinstance(record['digest'], str) or not isinstance(record['poll_message_id'], str)
          or not isinstance(record['chosen'], list) or not all(isinstance(c, str) and c for c in record['chosen'])):
        raise Refused('invalid vote record')
    try:
        epoch(record['published_at'])
        if kind == 'message':
            epoch(record['created_at'])
    except ValueError:
        raise Refused('invalid timestamp') from None
    return record


def epoch(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


class Pickup:
    def __init__(self, home, root, courier_user, now=time.time):
        self.home, self.now = home, now
        self.inbound = root / 'srv/courier/inbound'
        self.outbox = root / 'srv/courier/outbox'
        self.receipts = root / 'srv/courier/inbox'
        self.policy = root / 'etc/courier/policy.toml'
        self.courier = pwd.getpwnam(courier_user).pw_uid
        self.cli = Path(__file__).resolve().with_name('fm-inbox.sh')
        binding = json.loads((home / 'state/imessage/binding.json').read_text())
        if (not isinstance(binding, dict) or set(binding) != {'conversation_id', 'credential', 'destination'}
                or binding['destination'] != 'imessage'):
            raise Refused('state/imessage/binding.json is not an imessage binding')
        self.identity = {'conversation_id': binding['conversation_id'], 'credential': binding['credential']}
        self.dir = home / 'state/courier-pickup'
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(self.dir / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock)
            raise Refused('another pickup holds this home') from None
        self.lock = lock
        path = self.dir / 'state.json'
        self.s = json.loads(path.read_text()) if path.exists() else {
            'version': 1, 'cursor': 0, 'handled': {}, 'pending': None, 'marks': {}, 'polls': {},
            'texts': [], 'stages': 0}
        if self.s.get('version') != 1:
            raise Refused('unsupported pickup state')
        self.next_reply = 0.0
        self.quiet = {}
        # The turn this process last filed. Only this single-instance peer files
        # into the iMessage conversation, so it is the conversation's tail; a
        # restart or a failed capture forgets it and the transport is read again.
        self.tail = None

    def once(self, topic, message):
        """Log a standing condition when it changes, not on every tick it is still true."""
        if self.quiet.get(topic) != message:
            self.quiet[topic] = message
            log(message)

    def clear(self, topic):
        self.quiet.pop(topic, None)

    # ------------------------------------------------------------ plumbing

    def save(self):
        path = self.dir / 'state.json'
        tmp = path.with_name('.state.' + secrets.token_hex(8))
        with open(tmp, 'x', encoding='utf-8') as handle:
            handle.write(canonical(self.s))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        fd = directory(self.dir)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def call(self, command, payload=None):
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
        env['FM_HOME'] = str(self.home)
        result = subprocess.run([str(self.cli), 'conversation', command], env=env, text=True,
                                input=canonical(dict(payload or {}, **self.identity)),
                                capture_output=True, timeout=30, check=False)
        if result.returncode != 0:
            raise Refused(result.stderr.strip().replace(self.identity['credential'], '[redacted]')
                          or 'transport refused ' + command)
        return json.loads(result.stdout)

    def captain(self):
        info = os.stat(self.policy, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid())
                or info.st_mode & 0o022):
            raise Refused('courier policy is not root-owned and protected')
        to = tomllib.loads(self.policy.read_text(encoding='utf-8')).get('captain', {}).get('to')
        if not isinstance(to, str) or not E164.fullmatch(to):
            raise Refused('courier policy names no captain')
        return to

    def publish(self, name, row):
        """Atomically publish one Firstmate-owned 0640 request into the outbox."""
        data = canonical(row).encode()
        parent = directory(self.outbox)
        tmp = '.fm-pickup-' + secrets.token_hex(16) + '.tmp'
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=parent)
            try:
                os.write(fd, data)
                os.fchmod(fd, 0o640)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(tmp, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    # ------------------------------------------------------------- pickup

    def names(self):
        parent = directory(self.inbound)
        try:
            return sorted(n for n in os.listdir(parent) if NAME.fullmatch(n))
        finally:
            os.close(parent)

    def read(self, name):
        parent = directory(self.inbound)
        try:
            return validate(name, strict_json(read_owned(parent, name, self.courier, 0o640)))
        finally:
            os.close(parent)

    def pick(self):
        """Handle the spool in sequence order until a record must wait; whether anything moved."""
        moved = False
        while True:
            if self.s['pending'] is not None:
                if not self.file():
                    return moved
                moved = True
                continue
            later = [n for n in self.names() if int(n[:12]) > self.s['cursor']]
            if not later:
                return moved
            name = later[0]
            seq, kind, key = NAME.fullmatch(name).groups()
            moved = True
            try:
                record = self.read(name)
                # A message is deduplicated on its Linq message id, as the courier's own ledger is.
                handled = 'message-' + record['message_id'] if kind == 'message' else 'vote-' + key
                if handled in self.s['handled']:
                    raise Refused('duplicate ' + handled)
            except (Refused, OSError) as exc:
                log('refused inbound record %s: %s' % (name, exc))
                self.s['cursor'] = int(seq)
                self.save()
                continue
            self.s['handled'][handled] = epoch(record['published_at'])
            self.take(int(seq), kind, key, record)

    def take(self, seq, kind, key, record):
        self.s['cursor'] = seq
        if kind == 'message':
            identity = record['message_id']
            if not record['transcript'].strip():
                self.owe(NOT_TEXT, 'imsg-notext-' + identity, [identity, 'failed'])
                self.save()
                return
            attached = record['attachments']
            if attached and (record['other_parts'] or not all(a['saved'] for a in attached)):
                notice = PARTLY_FILED
            else:
                notice = PARTLY_TEXT if record['other_parts'] else None
            self.s['pending'] = {'message': identity, 'target': identity, 'words': record['transcript'],
                                 'created': record['created_at'], 'notice': notice}
        else:
            poll = self.s['polls'].get(record['request_id'])
            if poll is None or not record['chosen']:
                self.save()  # Not a question this program asked, or nothing chosen: never filed.
                return
            self.s['pending'] = {'message': 'vote-' + key, 'target': 'vote-' + key, 'words': record['chosen'][0],
                                 'created': record['published_at'], 'notice': None,
                                 'asked': poll['asked'], 'binding': poll['binding']}
            self.s['polls'].pop(record['request_id'])
        self.s['pending'].update(attempts=0, not_before=0, turn=None, request=None, previous=None)
        self.save()

    def file(self):
        """Try the pending capture once it is due; whether the queue moved past it."""
        entry = self.s['pending']
        if entry['not_before'] > self.now():
            return False
        try:
            if entry['turn'] is None:
                previous = self.tail
                if previous is None:
                    requests = self.call('poll').get('requests') or []
                    previous = requests[-1].get('turn_id') if requests else None
                entry.update(turn='imsg-' + entry['message'], request='imsg-req-' + entry['message'],
                             previous=previous)
                self.save()
            payload = {'turn_id': entry['turn'], 'request_id': entry['request'],
                       'committed_transcript': entry['words'], 'revision': 1,
                       'previous_turn_id': entry['previous'], 'created_at': entry['created']}
            if entry.get('binding') is not None:
                payload['question_binding'] = entry['binding']
            if self.call('capture', payload).get('request_id') != entry['request']:
                raise Refused('capture did not confirm its request_id')
        except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.tail = None
            entry['attempts'] += 1
            if entry['attempts'] <= len(RETRY_DELAYS):
                entry['not_before'] = self.now() + RETRY_DELAYS[entry['attempts'] - 1]
                log('filing %s failed (attempt %d): %s' % (entry['message'], entry['attempts'], exc))
                self.save()
                return False
            if not self.landed(entry['request']):
                self.tail = None
                log('gave up filing %s: %s' % (entry['message'], exc))
                self.s['pending'] = None
                self.owe(FAILURE, 'imsg-failed-' + entry['message'],
                         None if 'binding' in entry else [entry['target'], 'failed'])
                self.save()
                return True
        self.s['pending'] = None
        self.tail = entry['turn']
        self.answered(entry['target'], entry.get('asked'))
        self.move(entry['target'], 'filed', request=entry['request'])
        if 'binding' in entry:
            self.s['marks'][entry['target']]['silent'] = True  # A vote has no message of his to mark.
        if entry['notice']:
            self.owe(entry['notice'], 'imsg-partly-' + entry['message'])
        self.save()
        log('filed %s as request %s' % (entry['message'], entry['request']))
        return True

    def landed(self, request):
        try:
            return any(row.get('request_id') == request for row in self.call('poll').get('requests') or [])
        except (Refused, OSError, ValueError, subprocess.TimeoutExpired):
            return False

    # -------------------------------------------------------------- marks

    def move(self, target, stage, request=None):
        """Move a message's mark, and every mark following it (the direct bridge's rule)."""
        moment = self.now()
        mark = self.s['marks'].setdefault(target, {'shown': None})
        rank = RANK.get(mark.get('stage'), -1)
        if mark.get('follows') or rank == 3 or RANK[stage] < rank:
            return
        if request is not None:
            mark['request'] = request
        mark.update(stage=stage, moved=moment)
        leaders, seen = [target], {target}
        while leaders:
            leader = leaders.pop()
            for other, follower in self.s['marks'].items():
                if follower.get('follows') == leader and other not in seen:
                    rank = RANK.get(follower.get('stage'), -1)
                    if rank != 3 and RANK[stage] >= rank:
                        follower.update(stage=stage, moved=moment)
                    seen.add(other)
                    leaders.append(other)

    def answered(self, target, asked=None):
        for other, mark in self.s['marks'].items():
            if other != target and mark.get('stage') == 'question' and asked in (None, mark.get('request')):
                mark['follows'] = target

    def target(self, request):
        return next((t for t, m in self.s['marks'].items() if m.get('request') == request), None)

    def signal(self):
        """Publish each message's stage change once, in order, never backwards."""
        for target, mark in self.s['marks'].items():
            stage, shown = mark.get('stage'), mark.get('shown')
            if mark.get('silent') or stage is None or stage == shown:
                continue
            if shown is not None and (RANK[shown] == 3 or RANK[stage] < RANK[shown]):
                continue
            self.s['stages'] += 1
            name = 'st-%012d-%s' % (self.s['stages'], hashed(target, stage)[:16])
            self.save()  # The counter is durable before its name is used.
            self.publish(name + '.stage.json', {'id': name, 'message_id': target, 'stage': stage})
            mark['shown'] = stage
            self.save()

    # -------------------------------------------------------------- texts

    def owe(self, text, key, mark=None, response=None, generation=None, poll=None):
        entry = {'id': 'fm-' + hashed(key)[:48], 'text': text, 'mark': mark, 'response': response,
                 'generation': generation, 'published': None}
        if poll is not None:
            entry['poll_options'] = poll
        self.s['texts'].append(entry)

    def replies(self):
        seen = self.call('poll')
        states = {r.get('request_id'): r.get('state') for r in seen.get('requests') or []}
        for target, mark in list(self.s['marks'].items()):
            if mark.get('stage') == 'filed' and not mark.get('follows'):
                state = states.get(mark.get('request'))
                if state in ('accepted', 'rejected'):
                    self.move(target, 'working' if state == 'accepted' else 'failed')
        published = seen.get('replies') or []
        closed = {r.get('response_id') for r in published if r.get('kind') == 'question' and r.get('question_open') is False}
        self.s['polls'] = {k: p for k, p in self.s['polls'].items()
                           if p['question'] not in closed and self.now() - p['opened'] <= POLL_WATCH}
        owed = {t['response'] for t in self.s['texts']}
        for row in published:
            response = row.get('response_id')
            if (row.get('delivery') or {}).get('state') != 'waiting' or response in owed:
                continue
            generation = secrets.token_hex(16)
            claimed = self.call('deliver', {'response_id': response, 'generation': generation})
            if not claimed.get('deliver'):
                continue
            if (claimed.get('disclosure') or {}).get('destination') != 'imessage':
                log('reply %s was not published for imessage; not sent, playback left unknown' % response)
                continue
            kind, request = row.get('kind'), row.get('request_id')
            stage = 'done' if row.get('final') is True and kind == 'answer' else STAGE_OF_KIND.get(kind, 'working')
            target = self.target(request)
            offered = options(claimed['speech_text']) if kind == 'question' and row.get('question_binding') else None
            self.owe(claimed['speech_text'], 'imsg-' + generation, [target, stage] if target else None,
                     response, generation, offered)
            if offered:
                self.s['polls'][self.s['texts'][-1]['id']] = {
                    'question': response, 'asked': request, 'binding': row['question_binding'],
                    'opened': self.now()}
            self.save()

    def receipt(self, identity):
        """The courier's settled result for an outbox request, or None while it is pending."""
        parent = directory(self.receipts)
        try:
            for name in sorted(os.listdir(parent)):
                if not (name.startswith('result-' + identity + '.') and name.endswith('.json')):
                    continue
                row = strict_json(read_owned(parent, name, self.courier, 0o640))
                if isinstance(row, dict) and row.get('kind') == 'courier-result' and row.get('id') == identity:
                    if row.get('result') == 'sent' or row.get('result') in GAVE_UP:
                        return row['result']
        finally:
            os.close(parent)
        return None

    def send(self):
        """Publish owed texts one at a time, each after the previous one settled."""
        while self.s['texts']:
            entry = self.s['texts'][0]
            if entry['published'] is None:
                row = {'id': entry['id'], 'channel': 'imessage', 'to': self.captain(), 'text': entry['text'],
                       'purpose': 'reply' if entry['response'] else 'notice', 'attachments': []}
                if entry.get('poll_options'):
                    row['poll_options'] = entry['poll_options']
                self.publish(entry['id'] + '.json', row)
                entry['published'] = self.now()
                self.save()
            result = self.receipt(entry['id'])
            if result is None:
                if (self.now() - entry['published'] < UNRECEIPTED
                        or os.path.lexists(self.outbox / (entry['id'] + '.json'))):
                    return
                result = 'unreceipted'
            self.s['texts'].pop(0)
            if result == 'sent':
                if entry['response']:
                    try:
                        self.call('playback', {'response_id': entry['response'], 'generation': entry['generation'],
                                               'state': 'completed', 'position_ms': 0})
                    except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                        log('reply %s was sent but its receipt was not recorded: %s' % (entry['response'], exc))
                if entry['mark']:
                    self.move(*entry['mark'])
            else:
                log('courier did not send %s (%s)' % (entry['id'], result))
            self.save()

    # --------------------------------------------------------------- loop

    def prune(self):
        horizon = self.now() - KEEP
        self.s['handled'] = {k: v for k, v in self.s['handled'].items() if v > horizon}
        self.s['marks'] = {k: m for k, m in self.s['marks'].items() if m.get('moved', 0) > horizon}

    def tick(self, force_replies=False):
        try:
            self.send()
            self.clear('send')
        except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            self.once('send', 'courier outbox unavailable: %s' % exc)
        try:
            self.pick()
            self.clear('pick')
        except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            self.once('pick', 'inbound spool unavailable: %s' % exc)
        if force_replies or self.now() >= self.next_reply:
            self.next_reply = self.now() + REPLY_EVERY
            try:
                self.replies()
                self.clear('replies')
            except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
                self.once('replies', 'cannot read the iMessage conversation: %s' % exc)
            self.prune()
            self.save()
        try:
            self.send()
            self.signal()
            self.clear('send')
        except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            self.once('send', 'courier outbox unavailable: %s' % exc)


def main(argv):
    if len(argv) != 2 or argv[1] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0 if len(argv) == 2 else 2
    if argv[1] not in ('run', 'once'):
        print(__doc__, file=sys.stderr)
        return 2
    if os.environ.get('FM_NOTIFY_COURIER') != '1':
        return 3
    try:
        if not os.environ.get('FM_HOME'):
            raise Refused('explicit FM_HOME is required')
        os.umask(0o077)
        pickup = Pickup(Path(os.environ['FM_HOME']), Path(os.environ.get('FM_COURIER_ROOT') or '/'),
                        os.environ.get('FM_COURIER_USER') or 'courier')
        if argv[1] == 'once':
            pickup.tick(force_replies=True)
            return 0
        while True:
            pickup.tick()
            time.sleep(SCAN)
    except (Refused, OSError, KeyError, ValueError) as exc:
        log(str(exc))
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
