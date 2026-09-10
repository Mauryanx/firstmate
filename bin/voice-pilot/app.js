'use strict';
const $ = id => document.getElementById(id);
let secret = location.hash.slice(1);
history.replaceState(null, '', '/');
let connected = false, cid = null, previous = null, pending = null;
let player = null, playing = null, playerURL = null, generation = 0, busy = false, speakingInput = false;
let micStream = null, context = null, processor = null, micEpoch = 0, audioChain = Promise.resolve();
let ackTimer = null, ackBlob = null, seenReplies = new Set(), inputBusy = false, queuedAudio = 0;
const status = text => { $('status').textContent = text; };
function log(text) { const li = document.createElement('li'); li.textContent = text; $('transcript').append(li); }
async function api(path, data = {}, binary = false) {
  const result = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  if (!result.ok) { const error = await result.json(); throw Error(error.error); }
  if (binary && result.headers.get('Content-Type').startsWith('audio/')) return result.blob();
  return result.json();
}
function remember() { sessionStorage.setItem('fm-voice:' + cid, JSON.stringify({previous, pending})); }
async function silence(state = 'interrupted') {
  generation++;
  clearTimeout(ackTimer);
  const old = playing;
  const position = player ? Math.round(player.currentTime * 1000) : 0;
  if (player) { player.pause(); player.removeAttribute('src'); player.load(); }
  if (playerURL) URL.revokeObjectURL(playerURL);
  playerURL = null; player = null; playing = null;
  if (old) {
    try { await api('/playback', {...old, state, position_ms:position}); }
    catch (_) { log('Playback receipt is uncertain; the answer will not replay automatically.'); }
  }
}
async function play(blob, identity, expectedGeneration) {
  if (!connected || speakingInput || generation !== expectedGeneration) return;
  const url = URL.createObjectURL(blob), sound = new Audio(url);
  player = sound; playerURL = url; playing = identity;
  sound.onended = async () => {
    URL.revokeObjectURL(url);
    if (player !== sound) return;
    const receipt = playing;
    player = null; playerURL = null; playing = null;
    if (receipt) {
      try { await api('/playback', {...receipt, state:'completed', position_ms:Math.round(sound.currentTime * 1000)}); }
      catch (_) { log('Playback completion is uncertain.'); }
    }
    status(micStream ? 'Listening' : 'Connected · microphone off');
  };
  sound.onerror = () => { URL.revokeObjectURL(url); silence('unknown'); status('Could not play this answer.'); };
  try { await sound.play(); status(identity ? 'Speaking' : 'Waiting for Firstmate'); }
  catch (_) { URL.revokeObjectURL(url); await silence('unknown'); status('Playback was blocked. Use Connect again to enable audio.'); }
}
function acknowledge() {
  clearTimeout(ackTimer);
  ackTimer = setTimeout(() => {
    if (!player && !busy && !speakingInput && !queuedAudio && ackBlob) play(ackBlob, null, generation);
  }, 650);
}
async function savePending() {
  if (!pending) return;
  if (inputBusy) throw Error('A message is being saved.');
  inputBusy = true;
  const item = pending;
  try {
  const result = await api('/capture', item);
  previous = item.turn_id; pending = null; remember();
  $('correction').value = ''; $('question').value = '';
  $('retry').disabled = true;
  log('You: ' + item.committed_transcript);
  $('pending').textContent = 'Saved for Firstmate · ' + result.state;
  acknowledge();
  } finally { inputBusy = false; }
}
async function submit(text, requestId = crypto.randomUUID()) {
  if (!text.trim()) return;
  if (pending) throw Error('Retry the saved message before sending another.');
  // Silence locally before yielding, then record identity before any request.
  const stopped = silence();
  pending = {turn_id:crypto.randomUUID(), request_id:requestId, committed_transcript:text,
    revision:1, previous_turn_id:previous, created_at:new Date().toISOString(),
    correction_of:$('correction').value || null, question_binding:$('question').value || null};
  remember(); $('retry').disabled = false;
  await stopped;
  await savePending();
}
function options(id, entries) {
  const select = $(id), selected = select.value;
  while (select.options.length > 1) select.remove(1);
  for (const [value, label] of entries) { const option = new Option(label, value); select.add(option); }
  select.value = entries.some(([v]) => v === selected) ? selected : '';
}
async function poll() {
  if (!connected) return;
  try {
    const data = await api('/poll');
    if (data.conversation_id !== cid) throw Error('Conversation identity changed.');
    options('correction', data.requests.filter(r => r.state === 'accepted').map(r => [r.request_id, r.request_id]));
    options('question', data.replies.filter(r => r.question_open).map(r => [r.question_binding, r.question_binding]));
    const waiting = data.requests.filter(r => r.state === 'saved').length;
    $('pending').textContent = waiting ? `${waiting} saved message(s) waiting for Firstmate` : 'Saved messages accounted for';
    const reply = data.replies.find(r => r.delivery.state === 'waiting' && !seenReplies.has(r.response_id));
    if (reply && !busy && !player && !speakingInput && !queuedAudio && !pending) {
      busy = true; clearTimeout(ackTimer);
      seenReplies.add(reply.response_id);
      const epoch = generation, identity = {response_id:reply.response_id, generation:crypto.randomUUID()};
      try {
        const audio = await api('/speech', identity, true);
        if (audio instanceof Blob) {
          if (generation !== epoch || speakingInput || !connected) {
            await api('/playback', {...identity, state:'interrupted', position_ms:0});
          } else { await play(audio, identity, epoch); }
        }
      } finally { busy = false; }
    }
  } catch (error) { status(error.message); }
  if (connected) setTimeout(poll, 600);
}
function wav(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
  const str = (at, value) => { for (let i=0;i<value.length;i++) view.setUint8(at+i, value.charCodeAt(i)); };
  str(0,'RIFF'); view.setUint32(4,36+samples.length*2,true); str(8,'WAVE'); str(12,'fmt ');
  view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
  view.setUint32(24,16000,true); view.setUint32(28,32000,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
  str(36,'data'); view.setUint32(40,samples.length*2,true);
  samples.forEach((v,i) => view.setInt16(44+i*2, Math.max(-1,Math.min(1,v))*32767,true));
  let text=''; const bytes=new Uint8Array(buffer);
  for (let i=0;i<bytes.length;i+=8192) text+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return btoa(text);
}
async function microphoneOff() {
  micEpoch++; speakingInput=false;
  if (micStream) micStream.getTracks().forEach(track => track.stop());
  micStream=null;
  if (processor) { processor.disconnect(); processor.onaudioprocess=null; processor=null; }
  if (context) { await context.close(); context=null; }
  $('mic').textContent='Enable microphone';
}
async function microphoneOn() {
  const epoch=++micEpoch;
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
  if (!connected || epoch!==micEpoch) { stream.getTracks().forEach(track=>track.stop()); return; }
  micStream=stream;
  context=new AudioContext({sampleRate:16000});
  if (context.sampleRate!==16000) { await microphoneOff(); throw Error('This pilot requires 16 kHz microphone capture.'); }
  processor=context.createScriptProcessor(2048,1,1);
  const source=context.createMediaStreamSource(stream), silent=context.createGain(); silent.gain.value=0;
  source.connect(processor); processor.connect(silent); silent.connect(context.destination);
  let frames=[], count=0, quiet=0, pre=[];
  processor.onaudioprocess=event=>{
    if (!connected || epoch!==micEpoch) return;
    const frame=Float32Array.from(event.inputBuffer.getChannelData(0));
    const rms=Math.sqrt(frame.reduce((sum,x)=>sum+x*x,0)/frame.length);
    if (rms>.022) {
      if (!speakingInput) { speakingInput=true; silence(); frames=pre.slice(); count=frames.reduce((n,f)=>n+f.length,0); }
      quiet=0;
    } else { quiet+=frame.length; }
    pre.push(frame); if(pre.length>2)pre.shift();
    if (!speakingInput) return;
    frames.push(frame); count+=frame.length;
    if (quiet>11200 || count>=470000) {
      const joined=new Float32Array(count); let offset=0; for(const f of frames){joined.set(f,offset);offset+=f.length;}
      frames=[];count=0;quiet=0;pre=[];speakingInput=false;
      const requestId=crypto.randomUUID();
      queuedAudio++;
      audioChain=audioChain.then(async()=>{
        if (!connected || epoch!==micEpoch) return;
        status('Transcribing');
        const result=await api('/transcribe',{request_id:requestId,audio:wav(Array.from(joined))});
        // Mute/disconnect invalidates any in-flight recognition before dispatch.
        if (!connected || epoch!==micEpoch) return;
        $('text').value=result.text;
        await submit(result.text,requestId);
      }).catch(error=>{status(error.message);log('This utterance was not confirmed saved. Inspect the transcript before repeating it.');}).finally(()=>{queuedAudio--;});
    }
  };
  $('mic').textContent='Mute microphone';status('Listening');
}
$('connect').onclick=async()=>{
  try {
    let result;
    if(secret){result=await api('/pair',{secret});secret=null;}
    else {result=await api('/poll');}
    cid=result.conversation_id;
    const stored=JSON.parse(sessionStorage.getItem('fm-voice:'+cid)||'{}');
    previous=stored.previous||null;pending=stored.pending||null;
    // Poll supplies the durable predecessor when a tab has no local history.
    const state=await api('/poll');
    if (!pending && state.requests.length) previous=state.requests[state.requests.length-1].turn_id;
    ackBlob=await api('/ack',{},true);
    // Prime playback within the Connect gesture where the browser permits it.
    connected=true;
    for(const id of ['mic','stop','disconnect','text','send'])$(id).disabled=false;
    $('retry').disabled=!pending;$('connect').disabled=true;
    status('Connected · microphone off');poll();
  } catch(error){status(error.message);}
};
$('send').onclick=async()=>{try{await submit($('text').value);$('text').value='';}catch(error){status(error.message);}};
$('retry').onclick=async()=>{try{await savePending();}catch(error){status(error.message);}};
$('stop').onclick=()=>silence();
$('mic').onclick=async()=>{try{if(micStream){await microphoneOff();status('Connected · microphone off');}else{await microphoneOn();}}catch(error){status(error.message);}};
$('disconnect').onclick=async()=>{
  connected=false;await microphoneOff();await silence();
  try{await api('/disconnect');}catch(_){}
  for(const id of ['mic','stop','disconnect','send','retry','text'])$(id).disabled=true;
  status('Disconnected. A new private pairing is needed to reconnect.');
};
window.addEventListener('pagehide',()=>{connected=false;microphoneOff();silence('unknown');});
