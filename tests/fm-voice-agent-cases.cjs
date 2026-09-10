// Mechanical acceptance for the page that tells the voice agent an answer is
// ready. The vendor SDK is stubbed, so this asserts our own loop only: pairing,
// polling the real isolated transport, and announcing each published reply once.
// No account, microphone, speaker, agent minute or acoustic claim is involved.
const {chromium} = require(process.env.FM_VOICE_PLAYWRIGHT_MODULE);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.FM_VOICE_CHROMIUM, args:['--no-sandbox']});
 try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Stand in for the vendor bundle the operator installs, recording what the
  // page would say to the agent instead of opening a real session.
  await page.route('**/elevenlabs.js', route => route.fulfill({contentType:'text/javascript', body:`
    window.__sent = [];
    window.__attempts = 0;
    // The first attempt fails, standing in for an agent that is briefly unreachable.
    window.__failNext = true;
    window.ElevenLabsClient = {Conversation: {startSession: async opts => {
      window.__opts = {libsampleratePath: opts.libsampleratePath, connectionType: opts.connectionType,
                       conversationToken: opts.conversationToken, agentId: opts.agentId};
      return {
        sendUserMessage: async text => {
          window.__attempts++;
          if (window.__failNext) { window.__failNext = false; throw Error('agent unreachable'); }
          window.__sent.push(text);
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

  // The bridge's held turn is entitled to a reply before this page is, so the
  // page must stand off for that whole window. Ask the pilot how long that is
  // rather than restating it, then prove what does and does not wait it out:
  // inside the window only continuation portions of an already-claimed answer
  // may go, because no held turn is waiting on those, and nothing else may.
  const standoff = await page.evaluate(async () => (await (await fetch('/agent-config',
    {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).json()).announce_after_ms);
  if (!(standoff > 0)) throw Error('the pilot reported no stand-off window: ' + standoff);
  const continuing = Number(process.argv[5]);
  await page.waitForTimeout(standoff / 2);
  const early = await page.evaluate(() => window.__sent);
  if (early.length !== continuing) {
    throw Error('inside the hold window ' + early.length + ' answers were announced, not the '
                + continuing + ' continuation portion(s) no held turn was waiting on');
  }

  // Every answer already waiting is then announced. The first attempt fails, so
  // it must be retried by a later poll rather than lost, and no answer may ever
  // be announced twice however many polls run.
  const expected = Number(process.argv[4]);
  await page.waitForFunction(n => window.__sent.length === n, expected, {timeout:standoff + 20000});
  const attempts = await page.evaluate(() => window.__attempts);
  if (attempts !== expected + 1) throw Error('an unreachable agent did not cost exactly one retry: ' + attempts);
  const sent = await page.evaluate(() => window.__sent);
  for (const marker of sent) {
    if (!marker.startsWith('[firstmate-reply ') || !marker.endsWith(']')) throw Error('bad marker: ' + marker);
  }
  await page.waitForTimeout(2000);
  const after = await page.evaluate(() => window.__sent);
  if (after.length !== expected) throw Error('an answer was announced more than once');
  if (new Set(after).size !== after.length) throw Error('the same answer was announced twice');

  await page.getByRole('button', {name:'Disconnect', exact:true}).click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Disconnected.'));
  if (!await page.evaluate(() => window.__ended)) throw Error('the agent session was not ended');
  if (errors.length) throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({result:'PASS', browser:browser.version(), page_errors:errors,
    evidence:'pairing, transport polling, a tokened session, the stand-off that keeps the held turn the only claimant inside its hold while continuation portions of an already-claimed answer go out at once, one announcement per published answer, retry after an unreachable agent, and session end; stubbed vendor SDK, no account or acoustic acceptance'}, null, 2));
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
