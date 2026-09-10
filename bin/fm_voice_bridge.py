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

The bridge never invents an answer, and never says anything of its own. On a
substantive turn it files the captain's committed transcript and speaks nothing:
the agent platform generates its own line while it waits, in the context of what
he actually said, which is what this used to manufacture badly. The only words
the bridge ever produces are text Firstmate has actually published. It holds no
fleet data, calls no project tool, and files his transcript rather than any
summary of it, so nothing it says is an action or evidence of one.
A classifier that lets conversational turns be answered without waking Firstmate
is a separate metered dependency and is deliberately absent until authorized.
"""

import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fm_inbox_conversation import canonical
from fm_voice_pilot import Bridge, PilotError, check

# The browser sends this as an ordinary user message once Firstmate has published
# a reply, because the platform offers a server no way to make the agent speak.
ANSWER_MARKER = '[firstmate-reply '

MAX_BODY_BYTES = 400000

# The agent's own cascade timeout. A turn still open when it expires does not
# time out, it ends the captain's conversation with an LLM cascade error and
# drops him mid-call, so everything one turn does - filing his words, or claiming
# and speaking a published answer - finishes inside the budget this leaves.
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
# The least budget a claim may be started on. The transport writes and fsyncs
# the delivery claim before its answer gets back here, so a call killed after
# that leaves the reply claimed, unspoken, and no longer waiting for the page to
# announce - the captain never hears that answer and nothing reports it. Skipping
# a claim costs him the answer one stand-off later, which he can sit through, so
# the floor errs well above what the call is measured to cost.
DELIVER_FLOOR = 2.0


def turn_budget(cascade):
    """How long one turn may run, given the agent's own cascade timeout.

    Checked before the socket exists rather than left to whoever moves the
    console setting next, because the two numbers live in different places and
    only ever meet in a failure the captain feels.
    """
    check(cascade > CASCADE_MARGIN + DELIVER_FLOOR,
          'a cascade timeout of %gs leaves no room to file a turn or speak an answer inside it'
          % cascade)
    return cascade - CASCADE_MARGIN


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

    def __init__(self, bridge, budget):
        self.bridge, self.budget = bridge, budget
        self.sessions = {}
        self.lock = threading.Lock()

    def for_key(self, key):
        with self.lock:
            if key not in self.sessions:
                if len(self.sessions) >= self.LIMIT:
                    self.sessions.pop(next(iter(self.sessions)))
                self.sessions[key] = Session(self.bridge, self.budget)
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

    def __init__(self, bridge, budget):
        self.bridge, self.budget = bridge, budget
        self.previous_turn = None
        self.handled_turns = 0
        self.lock = threading.Lock()

    def speak(self, messages, extra):
        """Yield exactly what the agent should say for this turn."""
        spoken = user_turns(messages)
        said = last_user_turn(messages)
        # Everything this turn does finishes inside one budget, which is what
        # the cascade timeout leaves after its margin. A transport call left on
        # its own longer allowance would carry the turn past that timeout, and
        # that ends the captain's conversation rather than the turn.
        deadline = time.time() + self.budget
        # The platform re-invokes this endpoint for its own generated pause line
        # and on retries, resending a transcript that has not grown. Every turn
        # is handled exactly once, or one spoken instruction is filed twice.
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

    def record(self, said, extra, deadline):
        """File the captain's own words, and say nothing.

        Nothing truthful can be said before an answer exists, and the platform
        speaks its own line, in the context of what he actually said, while
        Firstmate reads them. A line manufactured here would only be a worse
        version of that one, spoken over it.
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
        return []

    def deliver(self, response_id, deadline):
        """Speak Firstmate's published words verbatim, and nothing besides."""
        check(response_id and len(response_id) <= 200, 'a published reply identity is required')
        state = self.within(deadline, 'poll')
        check(any(r['response_id'] == response_id for r in state['replies']),
              'no such published reply in this conversation')
        if deadline - time.time() < DELIVER_FLOOR:
            # No time left to finish a claim, and a claim that cannot be
            # finished is the one loss nothing recovers. It stays waiting.
            return []
        result = self.within(deadline, 'deliver', {'response_id': response_id,
                                                   'generation': str(uuid.uuid4())})
        if not result.get('deliver'):
            # Already spoken once; never say the same answer twice.
            return []
        return [result['speech_text']]


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
        """Write each portion as it is produced, so the first words leave at once."""
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
                # Saying nothing is how a turn with nothing to say ends: the
                # platform's own line covers the wait, and inventing one here is
                # what that replaced.
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
                        help="the agent's own cascade timeout, which every turn must finish inside")
    args = parser.parse_args()
    try:
        budget = turn_budget(args.cascade_seconds)
        secret = args.secret_file.read_text().strip()
        binding = json.loads(args.binding.read_text())
        check(isinstance(binding, dict) and binding.get('conversation_id'), 'binding is not a conversation')
        session = Sessions(Bridge(args.home, binding), budget)
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
