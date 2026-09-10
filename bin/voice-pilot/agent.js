'use strict';
// Closes the one loop the voice platform leaves open: a server cannot make the
// agent speak, so when Firstmate publishes an answer this page tells the agent
// about it as an ordinary user message, and the bridge speaks it verbatim.
// This page never reads or speaks the answer itself; it carries only its identity.
const $ = id => document.getElementById(id);
const MARKER = id => '[firstmate-reply ' + id + ']';
let secret = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let session = null, cid = null, standoff = null, polling = false, speaking = false;
const announced = new Set(), firstSeen = new Map();
const status = text => { $('status').textContent = text; };
function log(text) {
  const li = document.createElement('li');
  li.textContent = text;
  $('activity').append(li);
}
async function api(path, data = {}) {
  const result = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify(data)});
  if (!result.ok) throw Error((await result.json()).error);
  return result.json();
}
// One place decides what is worth announcing, so a reply is announced once even
// if a poll overlaps, the page is slow, or the agent takes a moment to accept it.
//
// Exactly one side may claim a given reply. The bridge's held turn claims it
// first, because delivering inside that turn is what makes the answer continue
// the opener as one thought instead of a stall and then a reply; this page is
// only the fallback for what the hold did not catch. So a reply is left alone
// until the bridge's whole hold window has passed. The pilot reports that window
// as announce_after_ms; the page never restates it. Do not announce sooner: a
// marker sent while the turn is still held either steals the answer or
// interrupts it mid-sentence, and the transport then reads it as already spoken.
//
// That window applies only to a reply a held turn could still be entitled to.
// An answer may be published as ordered portions, and a held turn speaks one
// portion and ends, so once a portion is recorded finished its stream is waiting
// on nothing. Those continuations go out at once: waiting there would put ten
// seconds of silence in the middle of one answer. Finished, not merely claimed -
// a claim is taken before a word is spoken, and sending the next portion on top
// of one still being said cuts the answer off and records it as delivered.
function pending(replies, now) {
  const finished = new Set(replies.filter(reply => reply.delivery.state === 'completed')
                                  .map(reply => reply.request_id));
  const ready = [];
  for (const reply of replies) {
    if (reply.delivery.state !== 'waiting') { firstSeen.delete(reply.response_id); continue; }
    if (announced.has(reply.response_id)) continue;
    if (!firstSeen.has(reply.response_id)) firstSeen.set(reply.response_id, now);
    if (finished.has(reply.request_id) || now - firstSeen.get(reply.response_id) >= standoff) {
      ready.push(reply);
    }
  }
  return ready;
}
async function announce(reply) {
  announced.add(reply.response_id);
  try {
    await session.sendUserMessage(MARKER(reply.response_id));
    log('Firstmate answered; asked the agent to say it.');
  } catch (error) {
    // Let a later poll try again rather than losing the answer silently.
    announced.delete(reply.response_id);
    status('Could not reach the agent: ' + error.message);
  }
}
async function poll() {
  if (!session || polling) return;
  polling = true;
  try {
    const state = await api('/poll');
    if (state.conversation_id !== cid) throw Error('Conversation identity changed.');
    const waiting = state.requests.filter(r => r.state === 'saved').length;
    $('waiting').textContent = waiting
      ? waiting + ' message(s) with Firstmate' : 'Nothing waiting with Firstmate';
    const ready = pending(state.replies, Date.now());
    // A marker is an interruption while the agent has the floor. The stand-off
    // clock keeps running; only the sending waits until it has stopped talking.
    if (!speaking) for (const reply of ready) await announce(reply);
  } catch (error) {
    status(error.message);
  } finally {
    polling = false;
    if (session) setTimeout(poll, 600);
  }
}
async function connect() {
  // A reload has no pairing secret left in the URL, but its session cookie is
  // still good for the hour it was issued for; poll proves it, so a refresh is
  // not a dead end that only a restarted pilot could get the captain out of.
  const paired = secret ? await api('/pair', {secret}) : await api('/poll');
  secret = null;
  cid = paired.conversation_id;
  const agent = await api('/agent-config');
  if (!agent.agent_id) throw Error('No voice agent is configured for this pilot.');
  standoff = agent.announce_after_ms;
  if (!(standoff >= 0)) throw Error('The pilot did not say how long the bridge holds a turn.');
  if (!window.ElevenLabsClient) throw Error('The voice agent SDK is not installed for this pilot.');
  // The platform SDK owns the microphone, turn-taking, interruption and speech.
  const {Conversation} = window.ElevenLabsClient;
  // The session is always tokened. A minting failure is raised by name here
  // rather than becoming a bare agent identity and an opaque platform error.
  const minted = await api('/agent-token');
  status('Connecting');
  // The bridge keeps per-conversation turn state under this id, so a fresh
  // session is never mistaken for a repeat of the previous one.
  const sessionId = crypto.randomUUID();
  session = await Conversation.startSession({
    conversationToken: minted.token,
    connectionType: 'webrtc',
    customLlmExtraBody: {session_id: sessionId},
    // Served from this pilot so the page never reaches a third-party CDN.
    libsampleratePath: '/libsamplerate.worklet.js',
    onStatusChange: state => status('Agent ' + (state.status || state)),
    onModeChange: state => { speaking = (state.mode || state) === 'speaking'; },
    onDisconnect: () => { session = null; speaking = false; status('Disconnected.'); },
    onError: message => status('Agent error: ' + message),
  });
  for (const id of ['stop']) $(id).disabled = false;
  $('connect').disabled = true;
  status('Listening');
  poll();
}
$('connect').onclick = async () => {
  try { await connect(); } catch (error) { status(error.message); }
};
$('stop').onclick = async () => {
  const ending = session;
  session = null;
  $('stop').disabled = true;
  try { if (ending) await ending.endSession(); } catch (_) { /* already gone */ }
  status('Disconnected. Reload this page while the pairing is still valid to talk again.');
};
window.addEventListener('pagehide', () => { const ending = session; session = null; if (ending) ending.endSession(); });
