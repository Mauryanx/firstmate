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

# Spoken while Firstmate reads and thinks. None of these claims a result, reports
# progress, or promises a time; the platform's own fillers cover later silence.
ACKNOWLEDGEMENTS = (
    'On it.',
    'Let me check.',
    'Give me a moment, I will find out.',
    'Putting that to the fleet now.',
    'Right, let me look into that.',
    'Checking that for you.',
    'One moment while I ask.',
    'Let me take that to Firstmate.',
)

MAX_BODY_BYTES = 400000


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

    def __init__(self, bridge, bag):
        self.bridge, self.bag = bridge, bag
        self.sessions = {}
        self.lock = threading.Lock()

    def for_key(self, key):
        with self.lock:
            if key not in self.sessions:
                if len(self.sessions) >= self.LIMIT:
                    self.sessions.pop(next(iter(self.sessions)))
                self.sessions[key] = Session(self.bridge, self.bag)
            return self.sessions[key]

    def speak(self, messages, extra):
        key = extra.get('session_id')
        key = key if isinstance(key, str) and 0 < len(key) <= 200 else 'unkeyed'
        return self.for_key(key).speak(messages, extra)


class Session:
    """Maps one platform conversation onto the bound Firstmate conversation."""

    def __init__(self, bridge, bag):
        self.bridge, self.bag = bridge, bag
        self.previous_turn = None
        self.handled_turns = 0
        self.lock = threading.Lock()

    def speak(self, messages, extra):
        """Yield exactly what the agent should say for this turn."""
        spoken = user_turns(messages)
        said = last_user_turn(messages)
        # The platform re-invokes this endpoint for its own filler generation and
        # on retries, resending a transcript that has not grown. Every turn is
        # handled exactly once, or one spoken instruction is dispatched twice.
        with self.lock:
            if len(spoken) <= self.handled_turns:
                return []
        if said.startswith(ANSWER_MARKER) and said.endswith(']'):
            portions = self.deliver(said[len(ANSWER_MARKER):-1].strip())
        else:
            portions = self.record(said, extra)
        # Advanced only after the turn is handled, so a refused call may be retried.
        with self.lock:
            self.handled_turns = max(self.handled_turns, len(spoken))
        return portions

    def tail(self):
        """The turn this conversation currently ends on.

        Read from the transport rather than remembered, so a bridge that starts
        mid-conversation continues the existing order instead of branching it.
        """
        with self.lock:
            if self.previous_turn is not None:
                return self.previous_turn
        requests = self.bridge.call('poll')['requests']
        return requests[-1]['turn_id'] if requests else None

    def record(self, said, extra):
        """File the captain's own words, then acknowledge without claiming a result."""
        request_id = str(extra.get('request_id') or uuid.uuid4())
        turn_id = str(uuid.uuid4())
        previous = self.tail()
        self.bridge.call('capture', {'turn_id': turn_id, 'request_id': request_id,
                                     'committed_transcript': said, 'revision': 1,
                                     'previous_turn_id': previous,
                                     'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        with self.lock:
            self.previous_turn = turn_id
        return [self.bag.draw()]

    def deliver(self, response_id):
        """Speak Firstmate's published words verbatim, framed if the captain moved on."""
        check(response_id and len(response_id) <= 200, 'a published reply identity is required')
        state = self.bridge.call('poll')
        reply = next((r for r in state['replies'] if r['response_id'] == response_id), None)
        check(reply is not None, 'no such published reply in this conversation')
        result = self.bridge.call('deliver', {'response_id': response_id,
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
            check(self.path in ('/v1/chat/completions', '/chat/completions'), 'unsupported endpoint')
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
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        identity = 'chatcmpl-' + secrets.token_hex(12)
        try:
            for portion in portions:
                self.event(dict(chunk(portion), id=identity))
            if not portions:
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
    args = parser.parse_args()
    try:
        secret = args.secret_file.read_text().strip()
        binding = json.loads(args.binding.read_text())
        check(isinstance(binding, dict) and binding.get('conversation_id'), 'binding is not a conversation')
        session = Sessions(Bridge(args.home, binding), ShuffleBag(ACKNOWLEDGEMENTS))
        endpoint = Endpoint(args.port, session, secret)
    except (PilotError, ValueError, KeyError, TypeError, OSError):
        raise SystemExit('voice bridge startup failed; verify the binding, the shared secret and the port') from None
    print('bridge listening on 127.0.0.1:%d; expose it with an outbound tunnel, never an inbound port'
          % endpoint.server_port, file=sys.stderr)
    endpoint.serve_forever()


if __name__ == '__main__':
    os.umask(0o077)
    main()
