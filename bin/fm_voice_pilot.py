#!/usr/bin/env python3
"""Private browser pilot for an explicitly bound Firstmate conversation.

Run --help for startup flags. Bind via fm-inbox.sh conversation first; --binding
is its private JSON output (conversation_id, credential). This server can only
capture/poll/deliver/receipt; it cannot accept work or publish speech. Firstmate
uses the owning-turn CLI for those operations. This is not a reasoning agent.

Listen only on 127.0.0.1; use an operator-established SSH forward with the same
port. --access-file is created exclusively, mode 0600, containing a one-use URL
fragment pairing secret. The browser exchanges it for a one-hour HttpOnly,
SameSite=Strict cookie. Exact Host and Origin checks apply; no CORS. Restarting
invalidates browser sessions but retains conversation and spend accounting.
No runtime lifecycle operations, external listener, terminal scraping or record
reader is provided. Only ElevenLabs receives committed microphone audio and
explicitly owner-published replies, under the owner's disclosure authorization.
Credentials must never be spoken or published. The browser uses no other speech
service. Every substantive utterance goes to the existing Firstmate conversation.

George warm uses eleven_flash_v2_5 and the audition settings. --ack-file must
match --ack-sha256; it is the already-approved prerecorded acknowledgement.
Optional --intro-file/--intro-sha256 supply the equally prerecorded line that
introduces an answer arriving after the captain has moved on. Both artifacts are
replayed rather than synthesized, so neither spends credits. Without an
introduction artifact the late answer is still framed in writing before it
plays.
Scribe v2 transcribes bounded mono PCM WAV utterances. No audio archive is kept.
The browser keeps unsaved final transcripts in sessionStorage for retry with
stable IDs; raw audio is memory-only and uncertain transcription is never
retried automatically. Playback claims precede synthesis and remain unknown on
failure. No speech or action replay on refresh/reconnect.

--credit-limit explicitly enables consumption of an authorized existing credit
balance. Default zero refuses metered synthesis and transcription requests; it
does not cover hosted-agent minutes, which are spent by the agent session itself
and are reserved against no ceiling here. The account-level no-overage checks
below still gate every provider call, including minting an agent session token,
so an unbudgeted agent session cannot become an overage. Supply ELEVENLABS_API_KEY only
in the launching environment, never in a file/argument. Reservations are fsynced
before network I/O, never refunded automatically, and retained across restart.
Reserve one credit per TTS character and ten per begun STT second, conservatively
above standard Flash/Scribe usage. A live subscription check before every call
requires enough included allowance AND both can_extend_character_limit and
allowed_to_extend_character_limit false. If those account protections are not
confirmed, refuse rather than risking overage during concurrent account use.
Never activate overage, buy credits or top up. All pilot processes must share
--spend-file. A flock serializes reservations and ambiguous failures latch it
closed for owner review. This is bounded usage accounting, not an invoice.


Local operator accountability owns publication; the stored author and content
digest record the exact publication, not an automatic content classifier. See
fm_inbox_conversation.py for the exact live publication contract.
"""
import argparse
import base64
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave

from fm_inbox_conversation import MAX_SPEECH_CHARS, canonical, write


class PilotError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise PilotError(message)


# How long the hosted-agent bridge holds one spoken turn open waiting for
# Firstmate. It lives here rather than in the bridge because two programs must
# mean the same number: the bridge holds its turn for exactly this long, and
# this pilot tells the announcing page how long to stand off before it may claim
# a reply the bridge is still entitled to.
HOLD_SECONDS = 7.0
# Added on top of that window before the page may claim anything. A held turn
# can still have a transport call in flight when its window closes, so the page
# waits past the hold rather than up to it.
ANNOUNCE_SETTLE_SECONDS = 3.0


class Budget:
    def __init__(self, path, ceiling):
        check(type(ceiling) is int and ceiling >= 0, 'nonnegative authorized credit limit required')
        self.path, self.ceiling = Path(path), ceiling
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.thread_lock = threading.Lock()

    def update(self, operation):
        with self.thread_lock, self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {'reserved_credits': 0, 'requests': {}}
            result = operation(data)
            write(self.path, canonical(data))
            return result

    def reserve(self, key, amount):
        def operation(data):
            check(not data.get('halted'), 'provider budget halted; owner must inspect uncertain usage')
            check(key not in data['requests'], 'provider request already reserved; do not retry uncertain speech')
            check(amount > 0 and data['reserved_credits'] + amount <= self.ceiling, 'authorized budget exhausted')
            data['reserved_credits'] += amount
            data['requests'][key] = {'reserved_credits': amount, 'state': 'unknown'}
        self.update(operation)

    def finish(self, key, receipt=None, failed=False):
        def operation(data):
            data['requests'][key]['state'] = 'failed-unknown' if failed else 'returned'
            if receipt is not None:
                data['requests'][key]['reported_character_cost'] = receipt
            if failed:
                data['halted'] = True
        self.update(operation)


class ElevenLabs:
    def __init__(self, key, budget):
        self.key, self.budget = key, budget

    def call(self, path, body, content_type, request_id, reserve):
        check(bool(self.key), 'ElevenLabs credential is unavailable')
        # The account must itself disallow paid extension, so concurrent use of
        # included credits cannot silently turn this local reservation into overage.
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    'https://api.elevenlabs.io/v1/user/subscription',
                    headers={'xi-api-key': self.key}), timeout=20) as response:
                subscription = json.load(response)
            check(subscription.get('can_extend_character_limit') is False and
                  subscription.get('allowed_to_extend_character_limit') is False,
                  'account can incur overage; owner must disable it before use')
            check(subscription['character_limit'] - subscription['character_count'] >= reserve,
                  'insufficient included allowance')
        except PilotError:
            raise
        except Exception:
            raise PilotError('cannot verify included allowance and no-overage account policy') from None
        self.budget.reserve(request_id, reserve)
        request = urllib.request.Request('https://api.elevenlabs.io' + path, data=body,
                                         headers={'xi-api-key': self.key, 'Content-Type': content_type})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(8000001)
                check(len(data) <= 8000000, 'provider response too large')
                receipt = response.headers.get('character-cost')
                cost = float(receipt) if receipt is not None else None
                check(cost is None or (math.isfinite(cost) and 0 <= cost <= reserve), 'usage exceeded reservation')
                self.budget.finish(request_id, cost)
                return data, response.headers.get('Content-Type', '')
        except Exception:
            self.budget.finish(request_id, failed=True)
            raise PilotError('provider request failed; usage may be uncertain; no automatic retry') from None

    def conversation_token(self, agent_id):
        """Short-lived WebRTC session token for the configured agent.

        Minting a token consumes no credits and reserves nothing; the agent
        minutes are spent by connecting the session, not by asking for the key.
        The overage check still runs, because a session the account cannot
        afford should be refused before the captain is invited to talk into it.

        A failure here is named rather than absorbed. Returning nothing would let
        the page connect on the bare agent identity, and the captain would then
        be shown an opaque agent error whose real cause was that minting failed.
        """
        check(bool(self.key), 'ElevenLabs credential is unavailable')
        check(isinstance(agent_id, str) and agent_id.replace('_', '').isalnum(), 'implausible agent identity')
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    'https://api.elevenlabs.io/v1/user/subscription',
                    headers={'xi-api-key': self.key}), timeout=20) as response:
                subscription = json.load(response)
            check(subscription.get('can_extend_character_limit') is False and
                  subscription.get('allowed_to_extend_character_limit') is False,
                  'account can incur overage; owner must disable it before use')
        except PilotError:
            raise
        except Exception:
            raise PilotError('cannot verify the no-overage account policy') from None
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    'https://api.elevenlabs.io/v1/convai/conversation/token?agent_id=' + agent_id,
                    headers={'xi-api-key': self.key}), timeout=20) as response:
                token = json.load(response).get('token')
        except Exception:
            raise PilotError('could not mint a voice agent session token; '
                             'no agent session was started') from None
        check(isinstance(token, str) and token, 'provider returned no session token')
        return token

    def speech(self, text, request_id):
        check(isinstance(text, str) and 0 < len(text) <= MAX_SPEECH_CHARS,
              'speech portion must contain 1-%d characters' % MAX_SPEECH_CHARS)
        check(not self.key or self.key not in text, 'credential must never be published')
        body = {'text': text, 'model_id': 'eleven_flash_v2_5',
                'voice_settings': {'stability': .40, 'similarity_boost': .75, 'style': .15,
                                   'speed': 1.02, 'use_speaker_boost': True}}
        data, kind = self.call('/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb?output_format=mp3_44100_128',
                               canonical(body).encode(), 'application/json', request_id, len(text))
        if not kind.startswith('audio/') or len(data) < 1000:
            self.budget.finish(request_id, failed=True)
            raise PilotError('provider returned no usable speech; budget halted')
        return data

    def transcribe(self, audio, request_id):
        try:
            with wave.open(io.BytesIO(audio), 'rb') as source:
                check(source.getnchannels() == 1 and source.getsampwidth() == 2 and
                      source.getframerate() == 16000 and source.getcomptype() == 'NONE', 'expected mono 16kHz PCM WAV')
                count = source.getnframes()
                check(1600 <= count <= 480000, 'utterance must be 0.1-30 seconds')
                check(len(source.readframes(count)) == count * 2, 'truncated WAV')
        except (wave.Error, EOFError):
            raise PilotError('invalid WAV') from None
        boundary = secrets.token_hex(24)
        body = bytearray()
        for key, value in {'model_id': 'scribe_v2', 'diarize': 'false', 'tag_audio_events': 'false'}.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="utterance.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
        body.extend(audio)
        body.extend(f'\r\n--{boundary}--\r\n'.encode())
        data, _ = self.call('/v1/speech-to-text', bytes(body), 'multipart/form-data; boundary=' + boundary,
                            request_id, math.ceil(count / 16000) * 10)
        try:
            text = json.loads(data)['text']
            check(isinstance(text, str) and len(text) <= 16000, 'invalid committed transcription')
            return text
        except (ValueError, KeyError, TypeError, PilotError):
            self.budget.finish(request_id, failed=True)
            raise PilotError('provider returned no usable transcription') from None


class Bridge:
    def __init__(self, home, binding):
        self.home, self.binding = str(home), binding

    def call(self, command, payload=None, timeout=35):
        check(command in ('capture', 'poll', 'deliver', 'playback'), 'transport operation refused')
        check(timeout > 0, 'no time is left for the conversation transport')
        payload = payload or {}
        check(not ({'credential', 'conversation_id', 'authenticated_principal'} & payload.keys()), 'identity comes from pairing')
        env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE' and
               k not in ('ELEVENLABS_API_KEY', 'ELEVEN_LABS_API_KEY', 'XI_API_KEY', 'ELEVEN_API_KEY')}
        env['FM_HOME'] = self.home
        try:
            result = subprocess.run([str(Path(__file__).with_name('fm-inbox.sh')), 'conversation', command],
                                    input=canonical(dict(payload, **self.binding)), text=True,
                                    capture_output=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise PilotError('the conversation transport did not answer in time') from None
        check(result.returncode == 0, 'conversation refused the request; owner can inspect the durable journal')
        return json.loads(result.stdout)


class Pilot(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, bridge, provider, ack, intro=None, agent_id=None, agent_sdk=None,
                 agent_worklet=None):
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)
        self.bridge, self.provider, self.ack, self.intro = bridge, provider, ack, intro
        # The hosted-agent page and its vendor SDK are served only when the
        # operator has deliberately supplied both; no vendor bytes live in this repo.
        self.agent_id, self.agent_sdk, self.agent_worklet = agent_id, agent_sdk, agent_worklet
        self.pair_secret = secrets.token_urlsafe(32)
        self.cookie, self.expires = None, 0
        self.auth_lock = threading.Lock()
        self.assets = Path(__file__).with_name('voice-pilot')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # No paths, credentials, transcripts, provider errors or payload logs.

    def send(self, value, status=200, kind='application/json', cookie=None):
        data = canonical(value).encode() if kind == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', self.policy())
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    AGENT_PATHS = ('/agent', '/agent.js', '/elevenlabs.js', '/libsamplerate.worklet.js',
                   '/agent-config', '/agent-token')

    def policy(self):
        """Same-origin everywhere; the agent page also needs its worklet sources.

        The vendor bundle builds audio worklets from blob: and data: URLs and
        speaks WebRTC to the platform, so those are widened for that page alone.
        The browser pilot page keeps the stricter policy it already had.
        """
        if self.path in self.AGENT_PATHS:
            return ("default-src 'self'; script-src 'self' blob: data:; style-src 'self'; "
                    "media-src 'self' blob:; worker-src 'self' blob:; "
                    # The vendor's own hosts, observed rather than guessed: the realtime
                    # transport lives on a regional subdomain, not the name its SDK is
                    # built from, and naming the wrong one fails only at Connect.
                    "connect-src 'self' https://*.elevenlabs.io wss://*.elevenlabs.io; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return ("default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

    def authorized(self):
        check(self.headers.get('Host') == self.server.origin.removeprefix('http://'), 'wrong host')
        check(self.headers.get('Origin') == self.server.origin, 'wrong origin')
        cookie = self.headers.get('Cookie', '')
        fields = dict(item.strip().split('=', 1) for item in cookie.split(';') if '=' in item)
        with self.server.auth_lock:
            check(self.server.cookie is not None and time.time() < self.server.expires and
                  secrets.compare_digest(fields.get('fm_voice', ''), self.server.cookie), 'pairing expired or missing')

    def do_GET(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            self.send({'error': 'wrong host'}, 403)
            return
        vendor = {'/elevenlabs.js': self.server.agent_sdk, '/libsamplerate.worklet.js': self.server.agent_worklet}
        if self.path in vendor:
            if not vendor[self.path]:
                self.send({'error': 'that voice agent asset is not installed for this pilot'}, 404)
                return
            self.send(Path(vendor[self.path]).read_bytes(), kind='text/javascript')
            return
        names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                 '/style.css': ('style.css', 'text/css'), '/agent': ('agent.html', 'text/html'),
                 '/agent.js': ('agent.js', 'text/javascript')}
        if self.path not in names:
            self.send({'error': 'not found'}, 404)
            return
        name, kind = names[self.path]
        self.send((self.server.assets / name).read_bytes(), kind=kind)

    def do_POST(self):
        try:
            check(self.headers.get('Host') == self.server.origin.removeprefix('http://') and
                  self.headers.get('Origin') == self.server.origin, 'wrong origin or host')
            check(self.headers.get('Content-Type') == 'application/json', 'expected JSON')
            size = int(self.headers.get('Content-Length', '0'))
            check(0 < size <= 1400000, 'invalid request size')
            data = json.loads(self.rfile.read(size))
            check(isinstance(data, dict), 'expected object')
            if self.path == '/pair':
                with self.server.auth_lock:
                    check(self.server.pair_secret is not None and
                          secrets.compare_digest(str(data.get('secret', '')), self.server.pair_secret), 'pairing refused')
                    self.server.pair_secret = None
                    self.server.cookie = secrets.token_urlsafe(32)
                    self.server.expires = time.time() + 3600
                    self.send({'conversation_id': self.server.bridge.binding['conversation_id']},
                              cookie=f'fm_voice={self.server.cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
                return
            self.authorized()
            if self.path == '/disconnect':
                with self.server.auth_lock:
                    self.server.cookie = None
                self.send({'disconnected': True}, cookie='fm_voice=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            elif self.path == '/ack':
                self.send(self.server.ack, kind='audio/mpeg')
            elif self.path == '/intro':
                # Absent artifact is normal: the written framing still names the earlier question.
                check(self.server.intro is not None, 'no approved introduction artifact is configured')
                self.send(self.server.intro, kind='audio/mpeg')
            elif self.path == '/agent-config':
                # The page never restates the bridge's hold; it is told it here,
                # so exactly one side is ever entitled to claim a given reply.
                self.send({'agent_id': self.server.agent_id,
                           'announce_after_ms': round((HOLD_SECONDS + ANNOUNCE_SETTLE_SECONDS) * 1000)})
            elif self.path == '/agent-token':
                # A private agent needs a short-lived session token. Minting one
                # spends nothing; connecting the session is what uses minutes.
                check(self.server.agent_id is not None, 'no voice agent is configured')
                self.send({'token': self.server.provider.conversation_token(self.server.agent_id)})
            elif self.path == '/poll':
                self.send(self.server.bridge.call('poll'))
            elif self.path in ('/capture', '/playback'):
                self.send(self.server.bridge.call(self.path[1:], data))
            elif self.path == '/speech':
                reply = self.server.bridge.call('deliver', data)
                if not reply['deliver']:
                    self.send(reply)
                else:
                    audio = self.server.provider.speech(reply['speech_text'],
                        'tts:' + self.server.bridge.binding['conversation_id'] + ':' + reply['response_id'])
                    self.send({'audio': base64.b64encode(audio).decode('ascii'),
                               'speech_text': reply['speech_text']})
            elif self.path == '/transcribe':
                rid = data.get('request_id')
                check(isinstance(rid, str) and 0 < len(rid) <= 200, 'request identity required')
                audio = base64.b64decode(data['audio'], validate=True)
                text = self.server.provider.transcribe(audio, 'stt:' + self.server.bridge.binding['conversation_id'] + ':' + rid)
                self.send({'text': text})
            else:
                self.send({'error': 'not found'}, 404)
        except (PilotError, ValueError, KeyError, TypeError, OSError) as exc:
            try:
                # Only locally authored PilotError strings may be returned.
                self.send({'error': str(exc) if isinstance(exc, PilotError) else 'request failed; no automatic provider retry'}, 400)
            except OSError:
                pass  # The browser closed this connection; nothing is left to tell it.


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--access-file', type=Path, required=True)
    parser.add_argument('--spend-file', type=Path, required=True)
    parser.add_argument('--credit-limit', type=int, default=0)
    parser.add_argument('--ack-file', type=Path, required=True)
    parser.add_argument('--ack-sha256', required=True)
    parser.add_argument('--intro-file', type=Path)
    parser.add_argument('--intro-sha256')
    parser.add_argument('--agent-id', help='hosted voice agent to talk to on the /agent page')
    parser.add_argument('--agent-sdk', type=Path,
                        help='operator-supplied vendor SDK bundle served to that page')
    parser.add_argument('--agent-worklet', type=Path,
                        help='operator-supplied resampler worklet, served so no CDN is used')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    os.umask(0o077)
    binding = json.loads(args.binding.read_text())
    check(set(binding) == {'conversation_id', 'credential'}, 'invalid binding')
    check(args.binding.stat().st_mode & 0o077 == 0, 'binding must be private (0600)')
    ack = args.ack_file.read_bytes()
    check(hashlib.sha256(ack).hexdigest() == args.ack_sha256, 'acknowledgement differs from approved artifact')
    check(bool(args.intro_file) == bool(args.intro_sha256), 'an introduction artifact requires its digest')
    intro = args.intro_file.read_bytes() if args.intro_file else None
    if intro is not None:
        check(hashlib.sha256(intro).hexdigest() == args.intro_sha256, 'introduction differs from approved artifact')
    keys = {os.environ[n] for n in ('ELEVENLABS_API_KEY', 'ELEVEN_LABS_API_KEY', 'XI_API_KEY', 'ELEVEN_API_KEY')
            if os.environ.get(n)}
    check(len(keys) <= 1, 'conflicting ElevenLabs credentials')
    server = Pilot(args.port, Bridge(args.home, binding),
                   ElevenLabs(next(iter(keys), None), Budget(args.spend_file, args.credit_limit)), ack, intro,
                   args.agent_id, args.agent_sdk, args.agent_worklet)
    # Verify bound owner before creating browser access, without any provider call.
    server.bridge.call('poll')
    # Point the pairing URL at the page this pilot was actually started for.
    # A configured agent means the hosted-agent page; writing the root URL and
    # expecting the operator to edit the route in is how the captain came to
    # spend a whole session reviewing the page we were replacing.
    page = '/agent#' if args.agent_id else '/#'
    with args.access_file.open('x') as handle:
        handle.write(server.origin + page + server.pair_secret + '\n')
    print('Private pairing URL written to ' + str(args.access_file), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    try:
        main()
    except FileExistsError as exc:
        # Exclusive creation protects a live pairing record; say so plainly,
        # because "startup failed" sends the operator hunting the wrong flag.
        raise SystemExit('voice pilot startup failed: the access file %s already exists; '
                         'a pairing record is never overwritten, so name a new one' % exc.filename) from None
    except OSError as exc:
        raise SystemExit('voice pilot startup failed: %s' % exc) from None
    except (PilotError, ValueError) as exc:
        raise SystemExit('voice pilot startup failed: %s'
                         % (exc if isinstance(exc, PilotError) else 'binding is not readable JSON')) from None
