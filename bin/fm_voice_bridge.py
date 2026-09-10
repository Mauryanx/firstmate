#!/usr/bin/env python3
"""Custom reasoning endpoint that puts a hosted voice agent in front of Firstmate.

Run --help for startup flags. The voice platform owns hearing, turn-taking,
interruption and speaking; this bridge owns what is said and what is recorded.
Firstmate remains the brain: it reads the captain's own words from the durable
conversation transport and publishes the answers this bridge speaks verbatim.

This bridge listens only on loopback and is published to the public internet by
an operator-established Tailscale Funnel, which connects outward, terminates TLS
and needs no inbound firewall rule. Funnel makes the address genuinely public,
so the shared secret is the only thing standing in front of it: authentication
runs before request parsing, routing, transport access and every other useful
behaviour, and an unauthenticated caller learns only that something refused it.
The tunnel is transport, never permission. Enabling Funnel is the operator's
network policy decision, not this program's. Never open an inbound port to reach
this; if a change would require one, stop and report it instead.

The bridge never invents an answer. It may speak an acknowledgement drawn from a
fixed set, and it may speak text Firstmate has actually published. It holds no
fleet data, calls no project tool, and files the captain's committed transcript
rather than any summary of it, so nothing it says is an action or evidence of one.
A classifier that lets conversational turns be answered without waking Firstmate
is a separate metered dependency and is deliberately absent until authorized.
"""

import argparse
import hmac
import json
import os
from pathlib import Path
import random
import re
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fm_inbox_conversation import canonical
from fm_voice_pilot import HOLD_SECONDS, Bridge, PilotError, check

# The browser sends this as an ordinary user message once Firstmate has published
# a reply, because the platform offers a server no way to make the agent speak.
ANSWER_MARKER = '[firstmate-reply '

# Spoken while Firstmate reads and thinks. None of these claims a result, reports
# progress, or promises a time, and nothing fills the silence that follows: the
# platform's generated fillers are left off, because the bridge cannot hold text
# it never sees to that rule.
#
# Each is spoken before the filing has succeeded, so each must be true whatever
# happens next. A line naming where the request went, or saying it got there, is
# a claim, and it is the only thing the captain hears when the filing then fails.
ACKNOWLEDGEMENTS = (
    'On it.',
    'Let me check.',
    'Give me a moment, I will find out.',
    'Right, let me look into that.',
    'Checking that for you.',
    'One moment while I ask.',
)

MAX_BODY_BYTES = 400000

# One spoken turn stays open for HOLD_SECONDS waiting for Firstmate, and the
# bridge looks every LOOK_INTERVAL. Holding the turn open is what lets the answer
# continue the opener as one utterance instead of arriving later as a separate
# announcement. That length is defined in fm_voice_pilot, not here, because the
# announcing page must stand off for the same window: a held turn is entitled to
# the reply it is waiting for, and no reply may ever have two claimants.
#
# It MUST stay below the agent's own cascade timeout. Measured against the live
# platform: a turn still open when that timeout expires ends the whole
# conversation with an LLM cascade error, which drops the captain mid-call, so a
# generous hold is worse than a short one. Holding does not delay speech: the
# opener is synthesised and heard about two seconds in either way, and only the
# platform's own bookkeeping waits for the stream to finish.

# The agent's own cascade timeout, which the hold must stay clear of. Exceeding it
# does not time the turn out, it ends the captain's conversation, so the margin is
# enforced at startup rather than left to whoever moves one of them next.
#
# PROVISIONAL: the six second margin is a judgement, not a measurement.
#
# It derives from a sibling agent on the vendor's Speech Engine product, which was
# killed fourteen seconds in on a turn that had already finalised around eleven.
# That agent's own configuration could not be read from here. The ceiling on THIS
# path, the custom reasoning endpoint, has never been measured: our own turns were
# never killed at a hold of ten, across roughly twenty conversations.
#
# So this margin very likely refuses settings that are perfectly safe here. It was
# chosen because the two ways of being wrong are not symmetric. Too loose ends the
# captain's conversation while he is waiting for an answer. Too strict means his
# answers arrive later, by a slower path. The second is recoverable and the first
# is not, so the error was taken deliberately in that direction.
#
# Revisit it the moment there is allowance to measure this path's real ceiling.
# Until then, treat six as a placeholder someone chose under a constraint, not as
# a number anyone established.
CASCADE_SECONDS = 15.0
CASCADE_MARGIN = 6.0
LOOK_INTERVAL = 0.5
# The least budget a claim may be started on. The transport writes and fsyncs
# the delivery claim before its answer gets back here, so a call killed after
# that leaves the reply claimed, unspoken, and no longer waiting for the page to
# announce - the captain never hears that answer and nothing reports it. Skipping
# a claim costs him the answer one stand-off later, which he can sit through, so
# the floor errs well above what the call is measured to cost.
DELIVER_FLOOR = 2.0

# An opener may reflect what the captain asked. It may never assert a finding, a
# status or a result, because nothing has been answered when it is spoken.
TOPIC_OPENERS = (
    'Let me look into {topic}.',
    'Give me a moment on {topic}.',
    'Let me find out about {topic}.',
    'Checking on {topic} now.',
    'Right, {topic}. Let me find out.',
)

# TEMPORARY. Drawing the subject out of the captain's words belongs to the
# watcher tap, which will let Firstmate's own first sentence be spoken instead of
# a line assembled here, and this whole block goes when that lands. It is live
# until then, in the captain's ear on every turn, which is why it was still worth
# making it refuse by default rather than leaving it to be replaced eventually.

# Phrases that introduce what the captain is asking about, anywhere in what he
# said. Each one names the subject explicitly. A bare on or with is not among
# them: those introduce when or how as readily as what, so a subject reachable
# only through a plain preposition is not recognised at all.
TOPIC_LEADS = (' about ', ' regarding ', ' with regard to ', ' on the subject of ')
TOPIC_IMPERATIVES = ('tell me about', 'find out about', 'look into', 'look up', 'look at',
                     'pull up', 'check on', 'check', 'review')
# An imperative behind a negation is an instruction NOT to do the thing, so an
# opener about its object would announce the opposite of what he said.
NEGATORS = frozenset("""no not never none nothing don't dont doesn't doesnt didn't didnt
    won't wont cannot can't cant avoid""".split())
# Words that open a clause, which is where an assertion lives. "the deploy THAT
# crashed" is a claim about the deploy; "the deploy" is a subject.
CLAUSE_WORDS = frozenset("""that which who whom whose what when where why how if whether
    because since while until unless though although than so but after before""".split())
# A pronoun is the other way a clause hides inside a phrase: "the deploy WE lost"
# has a subject and a verb, and no word list is needed to see the pronoun.
PRONOUNS = frozenset("""i we us our ours you your yours he him his she her hers
    it its they them their theirs""".split())
# A word wearing a verb's inflection is a verb until something proves otherwise,
# and nothing here can prove otherwise, so it is refused.
VERB_ENDINGS = ('ed', 'ing', 'en')
# Copulas and bare irregular verbs, which wear no ending to recognise them by.
# This list is closed. Irregular past forms it cannot name - found, lost, sent,
# built, took, made, kept - are refused by the length cap and the pronoun rule
# instead, because chasing spellings here would never finish.
CLAIM_WORDS = frozenset("""is are was were be am do does did done has have had gone
    will would shall should can could may might must broke break breaks failed fails
    fail works went goes ran run said says say think thinks seems seem looks look""".split())
TOPIC_DETERMINERS = frozenset('the a an this that these those my our your their'.split())
PLAIN_WORD = re.compile(r"^[a-z0-9][a-z0-9'.&/-]*$")
# Beyond an optional determiner. Two tokens name a thing; more room than that is
# room for a subject and a verb, which is a claim.
MAX_TOPIC_TOKENS = 2


def plain_noun_phrase(words):
    """True only for a phrase short and plain enough to assert nothing.

    A determiner and at most two further tokens, each an ordinary word wearing
    no verb's inflection, opening no clause and standing for no one. Anything
    longer has room for a subject and a verb, and a phrase with room for those
    can say something happened. Plenty of harmless phrases fail this, and that
    costs the captain a neutral opener. What it exists to prevent costs him the
    bridge asserting, in its own voice, that his deploy crashed, before anything
    has been looked at. Those two are not the same size.
    """
    plain = [word.strip('?.!,;:"()').lower() for word in words]
    if not all(plain) or not all(PLAIN_WORD.match(word) for word in plain):
        return False
    body = plain[1:] if plain[0] in TOPIC_DETERMINERS else plain
    if not body or len(body) > MAX_TOPIC_TOKENS:
        return False
    for word in plain:
        if word in PRONOUNS or word in CLAUSE_WORDS or word in CLAIM_WORDS:
            return False
        if word.endswith(VERB_ENDINGS):
            return False
    return True


def topic_of(said):
    """The subject the captain named, or None when nothing can be said truthfully.

    Conservative by design: it returns a noun phrase drawn from his own words,
    and nothing at all when that phrase cannot be shown to carry no claim. The
    subject may sit anywhere in a question, so it is looked for anywhere; which
    marker owns it is decided here rather than by which loop happens to run first.
    """
    lowered = said.lower().replace('\u2019', "'")
    found = []
    for lead in TOPIC_LEADS:
        at = lowered.find(lead)
        if at != -1:
            found.append((at, -len(lead), at + len(lead)))
    for verb in TOPIC_IMPERATIVES:
        at = lowered.find(verb + ' ')
        if at != -1 and (at == 0 or not lowered[at - 1].isalnum()):
            found.append((at, -len(verb), at + len(verb) + 1))
    if not found:
        return None
    # Whichever marker comes first owns the subject, and the longest of those
    # starting together. A trailing "about the new config" must not take the
    # sentence away from the "look into the deploy" it was hung on.
    _, _, start = min(found)
    # "Don't check the prod database" names a subject the same way "check the
    # prod database" does, and announcing it would say the opposite of what he
    # said, so a negation anywhere ahead of the marker refuses the whole thing.
    if any(word.strip('?.!,;:"()') in NEGATORS for word in lowered[:start].split()):
        return None
    candidate = said[start:].strip().strip('?.!,;:').strip()
    words = candidate.split()
    if not words:
        return None
    if not plain_noun_phrase(words):
        return None
    return ' '.join(words)


class ShuffleBag:
    """Draws every phrase before repeating one, and never twice in a row."""

    def __init__(self, phrases, rng=None):
        check(len(phrases) >= 2, 'at least two acknowledgements are required to vary')
        self.phrases, self.rng = list(phrases), rng or random.Random()
        self.remaining, self.last = [], None
        self.lock = threading.Lock()

    def draw(self):
        with self.lock:
            if not self.remaining:
                self.remaining = list(self.phrases)
                self.rng.shuffle(self.remaining)
                if len(self.remaining) > 1 and self.remaining[0] == self.last:
                    self.remaining.append(self.remaining.pop(0))
            self.last = self.remaining.pop(0)
            return self.last


def require_safe_hold(hold, cascade):
    """Refuse a hold that is not clear of the cascade timeout.

    A turn still open when that timeout expires ends the captain's conversation
    rather than merely ending the turn, so this is checked before the socket
    exists instead of being left to whoever moves one of them next.
    """
    check(hold >= 0, 'hold cannot be negative')
    check(hold <= cascade - CASCADE_MARGIN,
          'hold of %gs is not clear of the %gs cascade timeout; a turn still open when that '
          'expires ends the conversation, so keep at least %gs between them'
          % (hold, cascade, CASCADE_MARGIN))


def chunk(text, finish=None):
    """One OpenAI-compatible streaming delta, the shape the platform expects."""
    delta = {'content': text} if text is not None else {}
    return {'object': 'chat.completion.chunk', 'created': int(time.time()),
            'model': 'firstmate-bridge',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


def user_turns(messages):
    """The captain's turns in order, so a transcript that has not grown is known."""
    check(isinstance(messages, list) and messages, 'a conversation transcript is required')
    check(all(isinstance(message, dict) for message in messages), 'malformed transcript entry')
    return [message for message in messages if message.get('role') == 'user']


def last_user_turn(messages):
    """What the captain most recently said, exactly as the platform committed it."""
    spoken = user_turns(messages)
    check(spoken, 'no captain turn in this transcript')
    content = spoken[-1].get('content')
    # Some clients send content as typed parts rather than a bare string.
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content
                          if isinstance(part, dict) and part.get('type') == 'text')
    check(isinstance(content, str) and content.strip(), 'the captain said nothing to record')
    return content.strip()


class Sessions:
    """One Session per platform conversation, keyed by the page's session id.

    Turn bookkeeping cannot be global: a new conversation starts its transcript
    at one turn again, and a shared counter would read that as a re-invocation
    and answer the captain with silence.
    """

    LIMIT = 64

    def __init__(self, bridge, bag, topics=None, hold=HOLD_SECONDS):
        self.bridge, self.bag = bridge, bag
        self.topics = topics or ShuffleBag(TOPIC_OPENERS)
        self.hold = hold
        self.sessions = {}
        self.lock = threading.Lock()

    def for_key(self, key):
        with self.lock:
            if key not in self.sessions:
                if len(self.sessions) >= self.LIMIT:
                    self.sessions.pop(next(iter(self.sessions)))
                self.sessions[key] = Session(self.bridge, self.bag, self.topics, self.hold)
            return self.sessions[key]

    def speak(self, messages, extra):
        key = extra.get('session_id')
        # A turn carrying no conversation identity is refused, loudly, rather
        # than folded onto a shared counter: that counter reads a fresh
        # conversation's first turn as a repeat and answers the captain with
        # silence, with no error anywhere for anyone to find.
        check(isinstance(key, str) and 0 < len(key) <= 200,
              'a session identity is required; allow the agent to send session_id')
        return self.for_key(key).speak(messages, extra)


class Session:
    """Maps one platform conversation onto the bound Firstmate conversation."""

    def __init__(self, bridge, bag, topics=None, hold=HOLD_SECONDS):
        self.bridge, self.bag = bridge, bag
        self.topics = topics or ShuffleBag(TOPIC_OPENERS)
        self.hold = hold
        self.previous_turn = None
        self.handled_turns = 0
        self.lock = threading.Lock()

    def speak(self, messages, extra):
        """Yield exactly what the agent should say for this turn."""
        spoken = user_turns(messages)
        said = last_user_turn(messages)
        # One budget covers the whole turn: filing his words, the opener, and
        # the wait for an answer. It is the hold, which require_safe_hold has
        # proven CASCADE_MARGIN clear of the cascade timeout; that margin is
        # headroom the guard reserves, not an allowance to spend. Nothing inside
        # the turn may run on its own longer allowance, because a transport call
        # that outlasts this ends the captain's conversation rather than the
        # turn, and nothing recovers that.
        deadline = time.time() + self.hold
        # The platform re-invokes this endpoint for its own filler generation and
        # on retries, resending a transcript that has not grown. Every turn is
        # handled exactly once, or one spoken instruction is dispatched twice.
        with self.lock:
            if len(spoken) <= self.handled_turns:
                return []
        if said.startswith(ANSWER_MARKER) and said.endswith(']'):
            portions = self.deliver(said[len(ANSWER_MARKER):-1].strip(), deadline)
        else:
            portions = self.record(said, extra, deadline)
        # Advanced only after the turn is handled, so a refused call may be retried.
        with self.lock:
            self.handled_turns = max(self.handled_turns, len(spoken))
        return portions

    def within(self, deadline, command, payload=None):
        """One transport call, allowed only what is left of the turn."""
        return self.bridge.call(command, payload, timeout=deadline - time.time())

    def tail(self, deadline):
        """The turn this conversation currently ends on.

        Read from the transport rather than remembered, so a bridge that starts
        mid-conversation continues the existing order instead of branching it.
        """
        with self.lock:
            if self.previous_turn is not None:
                return self.previous_turn
        requests = self.within(deadline, 'poll')['requests']
        return requests[-1]['turn_id'] if requests else None

    def opener(self, said):
        """The first words of the turn, about what he asked wherever that is safe."""
        topic = topic_of(said)
        if topic is None:
            # Nothing specific can be said truthfully from his words alone.
            return self.bag.draw()
        return self.topics.draw().format(topic=topic)

    def record(self, said, extra, deadline):
        """File the captain's own words, then speak while Firstmate reads them.

        The turn is held open afterwards so Firstmate's answer continues this
        same utterance rather than arriving later as a separate announcement.
        Capture happens here, eagerly, not inside the generator.
        """
        request_id = str(extra.get('request_id') or uuid.uuid4())
        turn_id = str(uuid.uuid4())
        previous = self.tail(deadline)
        self.within(deadline, 'capture',
                    {'turn_id': turn_id, 'request_id': request_id,
                     'committed_transcript': said, 'revision': 1,
                     'previous_turn_id': previous,
                     'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        with self.lock:
            self.previous_turn = turn_id
        return self.utterance(self.opener(said), request_id, deadline)

    def utterance(self, opening, request_id, deadline):
        """Speak the opener now, then hold this turn open for Firstmate's answer."""
        yield opening + ' '
        # The wait is what is left of the turn, so filing his words slowly
        # shortens it rather than pushing the turn past the cascade timeout.
        while True:
            time.sleep(LOOK_INTERVAL)
            try:
                state = self.within(deadline, 'poll')
            except PilotError:
                return  # The answer is still durable; the page will announce it.
            # This turn's claim ends with its hold window. Past the deadline the
            # announcing page is the only claimant, so a look that came back late
            # must not take an answer the page has become entitled to carry.
            if time.time() >= deadline:
                return
            reply = next((r for r in state['replies'] if r['request_id'] == request_id and
                          r['delivery']['state'] == 'waiting'), None)
            if reply is not None:
                if deadline - time.time() < DELIVER_FLOOR:
                    return  # No time to finish a claim; the page announces it instead.
                try:
                    result = self.within(deadline, 'deliver',
                                         {'response_id': reply['response_id'],
                                          'generation': str(uuid.uuid4())})
                except PilotError:
                    return  # Unclaimed and still durable; the page will announce it.
                if result.get('deliver'):
                    yield result['speech_text']
                return
            # Speaks nothing; keeps the stream alive while Firstmate thinks.
            yield ''

    def deliver(self, response_id, deadline):
        """Speak Firstmate's published words verbatim, framed if the captain moved on."""
        check(response_id and len(response_id) <= 200, 'a published reply identity is required')
        state = self.within(deadline, 'poll')
        reply = next((r for r in state['replies'] if r['response_id'] == response_id), None)
        check(reply is not None, 'no such published reply in this conversation')
        result = self.within(deadline, 'deliver', {'response_id': response_id,
                                                   'generation': str(uuid.uuid4())})
        if not result.get('deliver'):
            # Already spoken once; never say the same answer twice.
            return []
        asked = next((n for n, r in enumerate(state['requests'])
                      if r['request_id'] == reply['request_id']), -1)
        late = 0 <= asked < len(state['requests']) - 1
        spoken = result['speech_text']
        if late:
            return ['Coming back to your earlier question. ', spoken]
        return [spoken]


class Endpoint(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, session, secret):
        # Validated and installed before the socket exists, so the endpoint is
        # never listening for even an instant without its guard in place.
        check(isinstance(secret, str) and len(secret) >= 32, 'shared secret must be at least 32 characters')
        self.session, self.secret = session, secret
        super().__init__(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass  # No transcripts, secrets, identities or paths in logs.

    def authenticated(self):
        """The only gate in front of a public address; nothing precedes it."""
        offered = self.headers.get('Authorization', '')
        prefix = 'Bearer '
        if not offered.startswith(prefix):
            return False
        return hmac.compare_digest(offered[len(prefix):], self.server.secret)

    def refuse(self, status, message):
        # A refusal answers before the request body is read, so the unread body
        # would be parsed as the next request on a reused connection. Closing is
        # the safe end: draining first would let an unauthenticated caller decide
        # how much this process reads.
        body = canonical({'error': {'message': message}}).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Authentication precedes parsing, routing and every useful behaviour, so
        # an unauthenticated caller learns only that something refused it.
        if not self.authenticated():
            self.refuse(401, 'unauthorized')
            return
        try:
            check(self.path == '/v1/chat/completions', 'unsupported endpoint')
            size = int(self.headers.get('Content-Length', '0'))
            check(0 < size <= MAX_BODY_BYTES, 'invalid request size')
            body = json.loads(self.rfile.read(size))
            check(isinstance(body, dict), 'expected an object')
            extra = body.get('elevenlabs_extra_body') or {}
            check(isinstance(extra, dict), 'malformed session parameters')
            spoken = self.server.session.speak(body.get('messages'), extra)
        except (PilotError, ValueError, KeyError, TypeError, OSError) as exc:
            # Only locally authored refusals are returned; nothing else is described.
            self.refuse(400, str(exc) if isinstance(exc, PilotError) else 'request refused')
            return
        self.stream(spoken)

    def do_GET(self):
        if not self.authenticated():
            self.refuse(401, 'unauthorized')
            return
        self.refuse(404, 'unsupported endpoint')

    def stream(self, portions):
        """Write each portion as it is produced, so the first words leave at once.

        A turn may stay open while Firstmate thinks, so nothing is collected
        first; the platform starts speaking the opener while the rest is pending.
        """
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()
        identity = 'chatcmpl-' + secrets.token_hex(12)
        spoke = False
        try:
            for portion in portions:
                self.event(dict(chunk(portion), id=identity))
                spoke = True
            if not spoke:
                # Nothing to say is said by saying nothing, not by inventing filler.
                self.event(dict(chunk(''), id=identity))
            self.event(dict(chunk(None, finish='stop'), id=identity))
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except OSError:
            pass  # The platform hung up; there is nothing left to say to it.

    def event(self, payload):
        self.wfile.write(b'data: ' + canonical(payload).encode() + b'\n\n')
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--binding', required=True, type=Path,
                        help='private JSON from fm-inbox.sh conversation bind')
    parser.add_argument('--secret-file', required=True, type=Path,
                        help='file holding the shared secret; never passed as an argument')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--cascade-seconds', type=float, default=CASCADE_SECONDS,
                        help="the agent's own cascade timeout, which the hold must stay clear of")
    args = parser.parse_args()
    try:
        # The hold is not a flag: the announcing page stands off for exactly this
        # window, so a hold this process could change on its own would put two
        # claimants on the same reply. Only the operator's cascade timeout, which
        # is configured in the agent console rather than here, is told to us.
        require_safe_hold(HOLD_SECONDS, args.cascade_seconds)
        secret = args.secret_file.read_text().strip()
        binding = json.loads(args.binding.read_text())
        check(isinstance(binding, dict) and binding.get('conversation_id'), 'binding is not a conversation')
        session = Sessions(Bridge(args.home, binding), ShuffleBag(ACKNOWLEDGEMENTS))
        endpoint = Endpoint(args.port, session, secret)
    except PilotError as exc:
        # Name the cause. "Startup failed" once sent an operator hunting the
        # wrong flag for an hour, and this refusal is one an operator will hit.
        raise SystemExit('voice bridge startup failed: %s' % exc) from None
    except OSError as exc:
        raise SystemExit('voice bridge startup failed: %s' % exc) from None
    except (ValueError, KeyError, TypeError):
        raise SystemExit('voice bridge startup failed: the binding is not readable JSON') from None
    print('bridge listening on 127.0.0.1:%d; expose it with an outbound tunnel, never an inbound port'
          % endpoint.server_port, file=sys.stderr)
    endpoint.serve_forever()


if __name__ == '__main__':
    os.umask(0o077)
    main()
