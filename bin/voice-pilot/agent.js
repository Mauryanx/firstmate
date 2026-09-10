'use strict';
// Closes the one loop the voice platform leaves open: a server cannot make the
// agent speak, so when Firstmate publishes an answer this page tells the agent
// about it as an ordinary user message, and the bridge speaks it verbatim.
// This page never reads or speaks the answer itself; it carries only its identity.
const $ = id => document.getElementById(id);
const MARKER = id => '[firstmate-reply ' + id + ']';
let secret = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let session = null, cid = null, announced = new Set(), polling = false;
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
function pending(replies) {
  return replies.filter(reply => reply.delivery.state === 'waiting' && !announced.has(reply.response_id));
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
    for (const reply of pending(state.replies)) await announce(reply);
  } catch (error) {
    status(error.message);
  } finally {
    polling = false;
    if (session) setTimeout(poll, 600);
  }
}
async function connect() {
  const paired = await api('/pair', {secret});
  secret = null;
  cid = paired.conversation_id;
  const agent = await api('/agent-config');
  if (!agent.agent_id) throw Error('No voice agent is configured for this pilot.');
  if (!window.ElevenLabsClient) throw Error('The voice agent SDK is not installed for this pilot.');
  // The platform SDK owns the microphone, turn-taking, interruption and speech.
  const {Conversation} = window.ElevenLabsClient;
  // A private agent needs a minted session token; a public one is named directly.
  const minted = await api('/agent-token').catch(() => ({token:null}));
  status('Connecting');
  // The bridge keeps per-conversation turn state under this id, so a fresh
  // session is never mistaken for a repeat of the previous one.
  const sessionId = crypto.randomUUID();
  session = await Conversation.startSession({
    ...(minted.token ? {conversationToken: minted.token} : {agentId: agent.agent_id}),
    connectionType: 'webrtc',
    customLlmExtraBody: {session_id: sessionId},
    // Served from this pilot so the page never reaches a third-party CDN.
    libsampleratePath: '/libsamplerate.worklet.js',
    onStatusChange: state => status('Agent ' + (state.status || state)),
    onDisconnect: () => { session = null; status('Disconnected.'); },
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
  status('Disconnected. A new private pairing is needed to reconnect.');
};
window.addEventListener('pagehide', () => { const ending = session; session = null; if (ending) ending.endSession(); });
