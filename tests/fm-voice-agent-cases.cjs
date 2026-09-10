// Mechanical acceptance for the page that tells the voice agent an answer is
// ready. The vendor SDK is stubbed, so this asserts our own loop only: pairing,
// polling the real isolated transport, and carrying every published reply once.
// No account, microphone, speaker, agent minute or acoustic claim is involved.
const {chromium} = require(process.env.FM_VOICE_PLAYWRIGHT_MODULE);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.FM_VOICE_CHROMIUM, args:['--no-sandbox']});
 try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Stand in for the vendor bundle the operator installs, and for the bridge
  // behind it, which does not run in this lane: a marker is claimed through the
  // pilot exactly as the bridge's deliver would, and only THEN does the agent
  // begin to speak. That gap is real - synthesis takes a moment - and it is the
  // window in which a second marker would cut the first answer in half.
  await page.route('**/elevenlabs.js', route => route.fulfill({contentType:'text/javascript', body:`
    window.__sent = [];
    window.__attempts = 0;
    window.__carrying = false;
    window.__overlapped = false;
    // The first attempt fails, standing in for an agent that is briefly unreachable.
    window.__failNext = true;
    // The next marker after that is accepted and then dropped, standing in for a
    // bridge that declines a claim it has no time left to finish: nothing is
    // claimed, nothing is spoken, and the page is told none of it.
    window.__declineNext = true;
    window.ElevenLabsClient = {Conversation: {startSession: async opts => {
      window.__opts = {libsampleratePath: opts.libsampleratePath, connectionType: opts.connectionType,
                       conversationToken: opts.conversationToken, agentId: opts.agentId};
      window.__setMode = mode => opts.onModeChange({mode});
      // The agent is already mid-answer when this page connects, which is when
      // a marker would cut what it is saying in half.
      window.__setMode('speaking');
      const wait = ms => new Promise(done => setTimeout(done, ms));
      const carry = async id => {
        await fetch('/speech', {method:'POST', headers:{'Content-Type':'application/json'},
                                body:JSON.stringify({response_id:id, generation:'agent-lane-' + id})});
        await wait(700);
        window.__setMode('speaking');
        await wait(400);
        window.__setMode('listening');
        window.__carrying = false;
      };
      return {
        sendUserMessage: async text => {
          window.__attempts++;
          if (window.__failNext) { window.__failNext = false; throw Error('agent unreachable'); }
          if (window.__carrying) window.__overlapped = true;
          window.__carrying = true;
          window.__sent.push(text);
          if (window.__declineNext) { window.__declineNext = false; window.__carrying = false; return; }
          carry(text.slice('[firstmate-reply '.length, -1));
        },
        endSession: async () => { window.__ended = true; },
      };
    }}};
  `}));
  await page.goto(process.argv[2]);
  await page.getByRole('button', {name:'Connect', exact:true}).click();
  await page.waitForFunction(() => document.getElementById('status').textContent === 'Listening');
  if (await page.evaluate(() => location.hash)) throw Error('pairing secret remained in location');
  const opts = await page.evaluate(() => window.__opts);
  // Every session is tokened, so a failure to mint one is named rather than
  // becoming a bare agent identity and an opaque platform error.
  if (opts.conversationToken !== process.argv[3]) throw Error('the session was not opened with the minted token');
  if (opts.agentId !== undefined) throw Error('the page fell back to a bare agent identity');
  if (opts.libsampleratePath !== '/libsamplerate.worklet.js') throw Error('resampler would come from a third-party CDN');
  if (opts.connectionType !== 'webrtc') throw Error('unexpected transport: ' + opts.connectionType);

  // Nothing may be sent while the agent has the floor: a marker is an
  // interruption, and interrupting cuts an answer off mid-sentence while the
  // transport keeps the record that it was delivered.
  await page.waitForTimeout(2000);
  const duringSpeech = await page.evaluate(() => window.__attempts);
  if (duringSpeech !== 0) throw Error('an answer was announced over a speaking agent: ' + duringSpeech);
  await page.evaluate(() => window.__setMode('listening'));

  // Every published answer is carried, in order, one at a time. The declined
  // one is offered again rather than lost, which is the only way this page can
  // tell a bridge that took an answer from one that quietly did not.
  const expected = Number(process.argv[4]);
  await page.waitForFunction(n => new Set(window.__sent).size === n, expected, {timeout:60000});
  const sent = await page.evaluate(() => window.__sent);
  for (const marker of sent) {
    if (!marker.startsWith('[firstmate-reply ') || !marker.endsWith(']')) throw Error('bad marker: ' + marker);
  }
  // No marker was ever sent while the one before it was still being carried:
  // claimed is not spoken, and the gap between them is where an answer is lost.
  if (await page.evaluate(() => window.__overlapped)) {
    throw Error('a marker went out while the answer before it was still being carried');
  }
  if (sent.length !== expected + 1) {
    throw Error('the declined answer was not offered again exactly once: ' + sent.length);
  }
  const repeated = sent.find((marker, at) => sent.indexOf(marker) !== at);
  if (repeated !== sent[0]) throw Error('an answer other than the declined one was offered twice');
  const attempts = await page.evaluate(() => window.__attempts);
  if (attempts !== expected + 2) throw Error('unexpected number of attempts: ' + attempts);
  await page.waitForTimeout(2000);
  if ((await page.evaluate(() => window.__sent)).length !== sent.length) {
    throw Error('an answer already carried was announced again');
  }

  await page.getByRole('button', {name:'Disconnect', exact:true}).click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Disconnected.'));
  if (!await page.evaluate(() => window.__ended)) throw Error('the agent session was not ended');
  if (errors.length) throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({result:'PASS', browser:browser.version(), page_errors:errors,
    evidence:'pairing, transport polling, a tokened session, silence while the agent has the floor, one marker outstanding at a time across a delayed speaking start, every published answer carried once in order, a declined answer offered again, retry after an unreachable agent, and session end; stubbed vendor SDK and stand-in bridge, no account or acoustic acceptance'}, null, 2));
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
