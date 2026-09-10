'use strict';
// Closes the one loop the voice platform leaves open: a server cannot make the
// agent speak, so when Firstmate publishes an answer this page tells the agent
// about it as an ordinary user message, and the bridge speaks it verbatim.
// This page never reads or speaks the answer itself; it carries only its identity.
//
// It also says, in words and before he presses anything, how much conversation
// the account can still pay for, and refuses to open a session that cannot reach
// the end of one exchange. The account's characters are a single pool that the
// agent's minutes draw on, so running it out mid-sentence is a real way to be cut
// off, and being cut off without warning is the thing this page exists to prevent.
// Nothing said here is a claim about the work: it is arithmetic on the account's
// own reported numbers, and every word about the work still comes from Firstmate.
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
const ABANDON_DEFAULT_MS = 15000;
// Served by the pilot rather than baked in here, so a fixture can shorten the
// wait without four abandons of dead time. FAILS CLOSED to the default: a
// missing, malformed, zero or negative value must never shorten this to nothing,
// because a slot freed instantly is the truncation race this window exists to
// close, and an unbounded one wedges every answer published afterwards.
let abandonAfterMs = ABANDON_DEFAULT_MS;
const abandonWindow = value =>
  (typeof value === 'number' && Number.isFinite(value) && value > 0) ? value : ABANDON_DEFAULT_MS;
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
// How often the allowance is re-read while he is talking. Slow, and off the
// announcing path: each reading is a live call to the account, and the voice
// path's job is to add as little as possible around Firstmate rather than to keep
// a meter warm. Slow enough that the reading is a courtesy, not a countdown.
const ALLOWANCE_INTERVAL_MS = 60000;
// Whether a session may be opened at all. Two separate facts refuse one: an
// allowance too small to finish an exchange, and an account that can bill past
// its included pool. Both are checked here so the button can never disagree with
// the sentence printed beside it.
const usable = state => !state.can_overage && state.enough;
// What is left to talk with, said in words rather than heard as a silence. The
// pilot does the arithmetic against the account's own reported numbers; this
// turns those numbers into the sentence he is owed before he starts. It asserts
// nothing about the fleet, a task, or any work in flight.
function describe(state) {
  if (state.can_overage)
    return 'This account can run into paid overage, so no session will be started until that is turned off.';
  const when = state.resets_at
    ? ' It renews on ' + new Date(state.resets_at * 1000).toLocaleDateString() + '.' : '';
  if (!state.enough)
    return 'Not enough allowance left to finish an exchange: about ' + state.seconds
      + ' seconds of conversation, where at least ' + state.minimum_seconds
      + ' are needed to ask a question and hear the answer.' + when
      + ' Connecting is refused rather than cutting you off mid-sentence.';
  return 'About ' + state.seconds + ' seconds of conversation left on this account.' + when;
}
// One reading, one sentence on the page. A failure here is reported as a failure
// to read rather than as an allowance of zero, because those are different facts
// and only one of them is about the account.
async function readAllowance() {
  const state = await api('/allowance');
  $('allowance').textContent = describe(state);
  return state;
}
// Re-read while he talks, so the pool running out mid-conversation is still a
// sentence he can read rather than a silence he sits in. A failed reading is not
// worth interrupting him over; the previous sentence simply stands.
async function watchAllowance() {
  if (!session) return;
  try {
    if (!usable(await readAllowance())) {
      log('The allowance can no longer cover a full exchange.');
    }
  } catch (_) { /* Leave the last good reading on the page and try again later. */ }
  if (session) setTimeout(watchAllowance, ALLOWANCE_INTERVAL_MS);
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
  return Date.now() - outstanding.sentAt >= abandonAfterMs;
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
  // Read again at the moment of pressing rather than trusting the reading the
  // button was enabled on: the page may have been open a while, and the account
  // is shared with everything else that speaks.
  if (!usable(await readAllowance())) {
    throw Error('The account cannot pay for a conversation right now.');
  }
  const agent = await api('/agent-config');
  if (!agent.agent_id) throw Error('No voice agent is configured for this pilot.');
  abandonAfterMs = abandonWindow(agent.abandon_after_ms);
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
    // He cut in. The platform has already cancelled the turn the bridge was
    // speaking into, so nothing here has to stop it; this page's part is to say
    // that it happened and to keep quiet until it is his turn again, which the
    // mode change above does by closing the gate the poll announces through.
    onInterruption: () => { log('You cut in; the agent stopped.'); },
    onDisconnect: () => { session = null; speaking = false; outstanding = null; status('Disconnected.'); },
    onError: message => status('Agent error: ' + message),
  });
  for (const id of ['stop']) $(id).disabled = false;
  $('connect').disabled = true;
  status('Listening');
  poll();
  watchAllowance();
}
$('connect').onclick = async () => {
  // Closed for the whole attempt, so a second press cannot open a second session
  // against the same allowance while the first is still being opened.
  $('connect').disabled = true;
  try {
    await connect();
  } catch (error) {
    status(error.message);
    // Offer the button again only when something transient stopped us. If the
    // allowance is what is short, re-enabling it would invite him to press until
    // it fails, which is the silence this page exists to replace.
    try { $('connect').disabled = !usable(await readAllowance()); } catch (_) { $('connect').disabled = false; }
  }
};
$('stop').onclick = async () => {
  const ending = session;
  session = null;
  $('stop').disabled = true;
  try { if (ending) await ending.endSession(); } catch (_) { /* already gone */ }
  status('Disconnected. Reload this page while the pairing is still valid to talk again.');
};
window.addEventListener('pagehide', () => { const ending = session; session = null; if (ending) ending.endSession(); });
// Pair and read the allowance before the button is offered, so what the account
// can afford is already on the page when he decides whether to press it. Pairing
// and reading both spend nothing; connecting the session is what uses minutes.
//
// A reload has no pairing secret left in the URL, but its session cookie is still
// good for the hour it was issued for; poll proves it, so a refresh is not a dead
// end that only a restarted pilot could get the captain out of.
(async () => {
  try {
    const paired = secret ? await api('/pair', {secret}) : await api('/poll');
    secret = null;
    cid = paired.conversation_id;
    const ready = usable(await readAllowance());
    $('connect').disabled = !ready;
    status(ready ? 'Ready. Press Connect and talk.'
                 : 'Not connecting: the account cannot pay for a conversation.');
  } catch (error) {
    // Not knowing what is left is not the same as knowing there is none, and it
    // is reported as the first rather than assumed to be the second. The button
    // stays closed either way: this page may not invite him into a conversation
    // it cannot promise will reach the end of a sentence.
    $('allowance').textContent = 'Could not read what is left to talk with: ' + error.message;
    status('Not connecting until the remaining allowance can be read.');
  }
})();
