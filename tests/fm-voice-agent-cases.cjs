// Mechanical acceptance for the page that tells the voice agent an answer is
// ready. The vendor SDK is stubbed, so this asserts our own loop only: pairing,
// polling the real isolated transport, and carrying every published reply once.
// No account, microphone, speaker, agent minute or acoustic claim is involved.
const {chromium} = require(process.env.FM_VOICE_PLAYWRIGHT_MODULE);
// What the pilot reports about the account, stubbed so the refusals can be
// driven without spending an account down or arming overage to prove them. The
// arithmetic that produces these numbers belongs to the pilot; what is asserted
// here is what the page does with them, which is the half the captain meets: a
// sentence before he presses anything, and a refusal rather than a silence
// whenever the account cannot pay for a whole exchange.
const HEALTHY = {characters:34000, seconds:1000, enough:true, minimum_seconds:20,
                 resets_at:1760000000, can_overage:false};
const SPENT = {characters:61, seconds:1, enough:false, minimum_seconds:20,
               resets_at:1760000000, can_overage:false};
// Room in the pool, but the account may bill past it. Refusing this one is about
// his money rather than his sentence, and the button must agree with the page.
const OVERAGE = {characters:34000, seconds:1000, enough:true, minimum_seconds:20,
                 resets_at:1760000000, can_overage:true};
const allowance = state => route => route.fulfill({contentType:'application/json', body:JSON.stringify(state)});
// The page reads the allowance once, at load, so each reading gets its own load.
// A reload has no pairing secret left in the URL and rides the session cookie,
// which is also what a refresh mid-conversation does.
const reread = async (page, state) => {
  await page.unroute('**/allowance');
  await page.route('**/allowance', allowance(state));
  await page.reload();
};
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.FM_VOICE_CHROMIUM, args:['--no-sandbox']});
 try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const refuses = async (opening, needle) => {
    await page.waitForFunction(t => document.getElementById('allowance').textContent.startsWith(t), opening);
    const said = await page.textContent('#allowance');
    if (!said.includes(needle)) throw Error('the refusal did not say why: ' + said);
    if (!await page.evaluate(() => document.getElementById('connect').disabled)) {
      throw Error('a session was offered against: ' + said);
    }
    if (await page.evaluate(() => window.__opts !== undefined)) throw Error('a session was opened against: ' + said);
  };
  // The spent account first, while the pairing secret is still in the URL.
  await page.route('**/allowance', allowance(SPENT));
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
    window.__order = [];
    window.__offers = {};
    // The first attempt fails, standing in for an agent that is briefly unreachable.
    window.__failNext = true;
    window.ElevenLabsClient = {Conversation: {startSession: async opts => {
      window.__opts = {libsampleratePath: opts.libsampleratePath, connectionType: opts.connectionType,
                       conversationToken: opts.conversationToken, agentId: opts.agentId};
      window.__setMode = mode => opts.onModeChange({mode});
      // The platform's own barge-in signal, which is all this page is given: it
      // has already cancelled the turn the bridge was speaking into by the time
      // this fires, so what the page does with it is the only thing to assert.
      window.__interrupt = () => opts.onInterruption();
      // The agent is already mid-answer when this page connects, which is when
      // a marker would cut what it is saying in half.
      window.__setMode('speaking');
      const wait = ms => new Promise(done => setTimeout(done, ms));
      const claim = id => fetch('/speech', {method:'POST', headers:{'Content-Type':'application/json'},
                                            body:JSON.stringify({response_id:id, generation:'agent-lane-' + id})});
      // The ordinary shape: the bridge claims the answer, and only THEN does the
      // agent begin to speak it. That gap is real - synthesis takes a moment -
      // and it is the window in which a second marker would cut the first in half.
      const carry = async id => {
        await claim(id);
        await wait(700);
        window.__setMode('speaking');
        await wait(400);
        window.__setMode('listening');
        window.__carrying = false;
      };
      // The platform speaks a pause line of its own on any turn the bridge
      // answers with silence, and this page is never told whose speech it was.
      // Here one lands BEFORE this reply is claimed, so it cannot be that
      // reply's answer, and settling the marker on it would send the next one
      // into the gap before this answer is ever said.
      const paused = async id => {
        window.__setMode('speaking');
        await wait(300);
        window.__setMode('listening');
        await wait(900);
        await carry(id);
      };
      // Claimed and never spoken: the bridge took the answer and the platform
      // hung up, or the turn ran out after the claim was already written. That
      // answer is gone, and holding the slot for it would lose every answer
      // published afterwards too.
      const silent = async id => {
        await claim(id);
        window.__carrying = false;
      };
      return {
        sendUserMessage: async text => {
          window.__attempts++;
          if (window.__failNext) { window.__failNext = false; throw Error('agent unreachable'); }
          if (window.__carrying) window.__overlapped = true;
          window.__carrying = true;
          window.__sent.push(text);
          const id = text.slice('[firstmate-reply '.length, -1);
          if (!window.__order.includes(id)) window.__order.push(id);
          const nth = window.__order.indexOf(id) + 1;
          window.__offers[id] = (window.__offers[id] || 0) + 1;
          // The first answer is declined once - the bridge refuses a claim it
          // has no time left to finish - and must be offered again rather than
          // lost, since nothing tells this page that is what happened.
          if (nth === 1 && window.__offers[id] === 1) { window.__carrying = false; return; }
          if (nth === 2) { paused(id); return; }
          if (nth === 3) { silent(id); return; }
          carry(id);
        },
        endSession: async () => { window.__ended = true; },
      };
    }}};
  `}));
  await page.goto(process.argv[2]);
  await refuses('Not enough', String(SPENT.minimum_seconds));

  // Enough characters, but the account may bill past its included pool. That is
  // a different reason and it must refuse just as firmly.
  await reread(page, OVERAGE);
  await refuses('This account can run into paid overage', 'turned off');

  // An allowance that can cover a conversation. Before he presses anything, the
  // page has told him what the account can pay for; the number is the account's,
  // and nothing here claims anything about the work.
  await reread(page, HEALTHY);
  await page.waitForFunction(() => document.getElementById('allowance').textContent.startsWith('About '));
  const offered = await page.textContent('#allowance');
  if (!offered.includes(String(HEALTHY.seconds))) throw Error('the allowance was not stated in his terms: ' + offered);
  await page.waitForFunction(() => !document.getElementById('connect').disabled);
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

  // Every published answer is carried, in order, one at a time: the declined one
  // offered again, the one a pause line spoke over settled only by its own
  // answer, and the one claimed but never spoken giving up its slot so the
  // answers behind it are still carried.
  const expected = Number(process.argv[4]);
  await page.waitForFunction(n => new Set(window.__sent).size === n, expected, {timeout:90000});
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
  // The one claimed and never spoken did not take the rest of the session with
  // it: the answers published behind it were still carried.
  if (sent.indexOf(sent[sent.length - 1]) !== sent.length - 1) {
    throw Error('the answers behind a claimed-but-unspoken one were not carried');
  }
  const attempts = await page.evaluate(() => window.__attempts);
  if (attempts !== expected + 2) throw Error('unexpected number of attempts: ' + attempts);
  await page.waitForTimeout(2000);
  if ((await page.evaluate(() => window.__sent)).length !== sent.length) {
    throw Error('an answer already carried was announced again');
  }

  // He cuts in. The platform cancels the turn it was speaking into and tells the
  // page; the page's part is to say so rather than carry on as if it had not.
  await page.evaluate(() => window.__interrupt());
  await page.waitForFunction(() => [...document.querySelectorAll('#activity li')]
    .some(item => item.textContent.includes('You cut in')));

  await page.getByRole('button', {name:'Disconnect', exact:true}).click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Disconnected.'));
  if (!await page.evaluate(() => window.__ended)) throw Error('the agent session was not ended');
  if (errors.length) throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({result:'PASS', browser:browser.version(), page_errors:errors,
    evidence:'a refusal in words with no session opened against either an allowance too small for one exchange or an account that can bill past its included pool, the remaining conversation stated before the button is offered, pairing, a reload riding the session cookie, transport polling, a tokened session, silence while the agent has the floor, one marker outstanding at a time across a delayed speaking start, a pause line before a claim not settling that claim, an answer claimed but never spoken not blocking the answers behind it, every published answer carried once in order, a declined answer offered again, retry after an unreachable agent, a barge-in reported rather than talked over, and session end; stubbed vendor SDK and stand-in bridge, no account or acoustic acceptance'}, null, 2));
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
