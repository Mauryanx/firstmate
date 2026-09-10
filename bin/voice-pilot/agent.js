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
// When the agent last stopped talking. The page cannot be told which answer a
// stretch of speech belonged to, so it reads that from the order of events: the
// bridge claims a reply before its words can be synthesised, so speech that had
// already ended when the claim landed cannot have been that reply's.
let stoppedAt = 0;
// The one marker in flight, if any: {id, sentAt, claimedAt}. At most one is
// ever outstanding. A marker is an interruption, and the agent starts speaking
// a moment AFTER the bridge claims the answer, so a second marker sent inside
// that moment cuts the first answer off mid-sentence while the transport has
// already recorded it as delivered.
let outstanding = null;
// How long a marker may stay outstanding before the slot is given up, whatever
// became of it. Two things end that way. A reply the transport still shows
// waiting was declined - the bridge refuses a claim it has no time left to
// finish, and nothing tells this page so - and it is offered again rather than
// lost. A reply that was claimed and never spoken is already lost, and holding
// the slot for it would lose every answer published afterwards too, which is
// worse. Longer than a whole bridge turn can be, so an offer never races a
// marker still being worked on: that turn is bounded by the agent's cascade
// timeout, whose documented maximum is fifteen seconds, less the bridge's margin.
const ABANDON_AFTER_MS = 15000;
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
//
// The slot frees when this reply has been claimed AND the agent has spoken and
// stopped since that claim landed. Both halves matter. Without the claim, an
// answer taken but never voiced holds the slot for good and silently swallows
// every answer after it. Without ordering the speech against the claim, the
// platform's own pause line - it speaks one on any turn the bridge answers with
// silence - would settle a marker whose answer has not been said yet.
function settled() {
  if (!outstanding) return true;
  if (outstanding.claimedAt && !speaking && stoppedAt > outstanding.claimedAt) return true;
  return Date.now() - outstanding.sentAt >= ABANDON_AFTER_MS;
}
async function announce(reply) {
  outstanding = {id: reply.response_id, sentAt: Date.now(), claimedAt: 0};
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
    if (outstanding && !outstanding.claimedAt &&
        !owed.some(reply => reply.response_id === outstanding.id)) {
      outstanding.claimedAt = Date.now();
    }
    if (settled()) outstanding = null;
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
      const talking = (state.mode || state) === 'speaking';
      if (speaking && !talking) stoppedAt = Date.now();
      speaking = talking;
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
