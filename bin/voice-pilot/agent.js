'use strict';
// Closes the one loop the voice platform leaves open: a server cannot make the
// agent speak, so when Firstmate publishes an answer this page tells the agent
// about it as an ordinary user message, and the bridge speaks it verbatim.
// This page never reads or speaks the answer itself; it carries only its identity.
const $ = id => document.getElementById(id);
const MARKER = id => '[firstmate-reply ' + id + ']';
let secret = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let session = null, cid = null, polling = false, speaking = false;
// The one marker in flight, if any: {id, sentAt, heard}. At most one is ever
// outstanding. A marker is an interruption, and the agent starts speaking a
// moment AFTER it accepts one, so a second marker sent inside that moment cuts
// the first answer off mid-sentence while the transport has already recorded it
// as delivered. Nothing else goes out until this one has been spoken and the
// agent has stopped, or until it is plainly not going to be.
let outstanding = null;
// How long to wait before offering the same reply again. The bridge can decline
// a marker - it refuses a claim it has no time left to finish - and nothing
// tells this page that happened, so a reply the transport still shows waiting
// after a whole bridge turn could have run is offered once more rather than
// lost. Longer than that turn can be: it is bounded by the agent's cascade
// timeout, whose documented maximum is fifteen seconds, less the bridge's own
// margin. A retry can therefore never race a marker still being worked on.
const RETRY_AFTER_MS = 15000;
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
// The transport is the only memory of what has been carried: a reply the bridge
// has claimed is no longer waiting, so it cannot be announced twice, and one it
// has not is still owed to the captain. This page is the only thing that claims
// a published reply, so a reply it leaves unannounced is an answer he never
// hears - which is why a marker that produced no speech is offered again.
function settled(waiting) {
  if (!outstanding) return true;
  if (outstanding.heard) return !speaking;
  return Date.now() - outstanding.sentAt >= RETRY_AFTER_MS &&
         waiting.some(reply => reply.response_id === outstanding.id);
}
async function announce(reply) {
  outstanding = {id: reply.response_id, sentAt: Date.now(), heard: false};
  try {
    await session.sendUserMessage(MARKER(reply.response_id));
    log('Firstmate answered; asked the agent to say it.');
  } catch (error) {
    // Nothing was carried, so a later poll may offer this same reply again.
    outstanding = null;
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
    const owed = state.replies.filter(reply => reply.delivery.state === 'waiting');
    if (settled(owed)) outstanding = null;
    // Replies go out one at a time, in the order Firstmate published them, so
    // the portions of one answer stay in order and never overlap each other.
    if (!speaking && !outstanding && owed.length) await announce(owed[0]);
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
    onModeChange: state => {
      speaking = (state.mode || state) === 'speaking';
      // The agent has begun saying what the outstanding marker asked for, which
      // is the only proof this page gets that the bridge accepted it.
      if (speaking && outstanding) outstanding.heard = true;
    },
    onDisconnect: () => { session = null; speaking = false; outstanding = null; status('Disconnected.'); },
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
