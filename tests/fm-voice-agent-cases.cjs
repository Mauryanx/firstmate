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
      window.__agentId = opts.agentId;
      window.__opts = {libsampleratePath: opts.libsampleratePath, connectionType: opts.connectionType};
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
  if (await page.evaluate(() => window.__agentId) !== process.argv[3]) throw Error('page opened the wrong agent');
  const opts = await page.evaluate(() => window.__opts);
  if (opts.libsampleratePath !== '/libsamplerate.worklet.js') throw Error('resampler would come from a third-party CDN');
  if (opts.connectionType !== 'webrtc') throw Error('unexpected transport: ' + opts.connectionType);

  // The answer published before this page connected is announced. The first
  // attempt fails, so it must be retried by a later poll rather than lost, and
  // once it lands it must never be announced a second time.
  await page.waitForFunction(() => window.__sent.length === 1, null, {timeout:15000});
  const attempts = await page.evaluate(() => window.__attempts);
  if (attempts !== 2) throw Error('an unreachable agent did not cost exactly one retry: ' + attempts);
  const marker = (await page.evaluate(() => window.__sent))[0];
  if (!marker.startsWith('[firstmate-reply ') || !marker.endsWith(']')) throw Error('bad marker: ' + marker);
  await page.waitForTimeout(2000);
  if ((await page.evaluate(() => window.__sent)).length !== 1) throw Error('an answer was announced more than once');

  await page.getByRole('button', {name:'Disconnect', exact:true}).click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Disconnected.'));
  if (!await page.evaluate(() => window.__ended)) throw Error('the agent session was not ended');
  if (errors.length) throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({result:'PASS', browser:browser.version(), page_errors:errors,
    evidence:'pairing, transport polling, one announcement per published answer, retry after an unreachable agent, and session end; stubbed vendor SDK, no account or acoustic acceptance'}, null, 2));
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
