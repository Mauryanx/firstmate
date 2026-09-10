// Optional mechanical browser acceptance against the real isolated HTTP transport.
// Invoked by fm-inbox-conversation.test.sh; no device or paid provider assertions.
const {chromium} = require(process.env.FM_VOICE_PLAYWRIGHT_MODULE);
const fs = require('fs');
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.FM_VOICE_CHROMIUM, args:['--no-sandbox']});
 try {
  const page=await browser.newPage();
  const errors=[], requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST')requests.push(new URL(r.url()).pathname);});
  await page.goto(process.argv[2]);
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('status').textContent==='Connected · microphone off');
  if(await page.evaluate(()=>location.hash))throw Error('pairing secret remained in location');
  // The answer waiting for r2 belongs to a question the captain has moved past,
  // so it must name that question instead of arriving as the current answer.
  await page.waitForFunction(()=>document.querySelector('#transcript').textContent.includes('answering your earlier question'));
  // No approved introduction clip is configured here, so the written framing carries it alone.
  if(await page.evaluate(()=>introBlob!==null))throw Error('Absent introduction artifact was not handled as optional');
  await page.getByRole('button',{name:'Stop speaking',exact:true}).click();
  await page.locator('#listen-mode').selectOption('push');
  if(!await page.locator('#talk').isDisabled())throw Error('Push-to-talk active before microphone permission');
  await page.locator('#pause').selectOption('1.2');
  await page.locator('#listen-mode').selectOption('natural');
  await page.locator('#text').fill('Synthetic browser transport check.');
  let lost=false;
  let lossConfirmed;const loss=new Promise(resolve=>{lossConfirmed=resolve;});
  await page.route('**/capture',async route=>{
   if(!lost){lost=true;await route.fetch();await route.abort('failed');lossConfirmed();}
   else await route.continue();
  });
  await page.evaluate(()=>{document.getElementById('send').click();document.getElementById('send').click();});
  await loss;
  if(await page.locator('#retry').isDisabled())throw Error('Uncertain capture was not retained for retry');
  await page.reload();
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('status').textContent==='Connected · microphone off');
  await page.getByRole('button',{name:'Retry saved message',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#transcript').textContent.includes('Synthetic browser transport check.'));
  await page.waitForTimeout(800);
  await page.getByRole('button',{name:'Stop speaking',exact:true}).click();
  await page.locator('#text').fill('A follow-up in the same conversation.');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#transcript').textContent.includes('A follow-up in the same conversation.'));
  const state=await page.evaluate(async()=>{const r=await fetch('/poll',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});return r.json();});
  const own=state.requests.slice(-2);
  if(state.requests.length!==5||own[1].previous_turn_id!==own[0].turn_id)throw Error('Retry or follow-up corrupted request identity/order');
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export timing',exact:true}).click();
  const download=await downloadPromise;
  const trace=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  if(trace.schema!=='fm-voice-timing.v1'||!trace.events.some(e=>e.event==='capture_saved'))throw Error('Timing export missing saved input');
  if(JSON.stringify(trace).includes('A follow-up'))throw Error('Timing export leaked transcript');
  await page.getByRole('button',{name:'Disconnect',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('status').textContent.startsWith('Disconnected.'));
  const counts={};for(const p of requests)counts[p]=(counts[p]||0)+1;
  if(errors.length)throw Error(JSON.stringify(errors));
  if(counts['/pair']!==1||counts['/capture']!==3||counts['/disconnect']!==1)throw Error(JSON.stringify(counts));
  console.log(JSON.stringify({result:'PASS',browser:browser.version(),page_errors:errors,request_counts:counts,evidence:'DOM controls, pairing, text capture, lost capture receipt plus reload retry, double-submit deduplication, follow-up order, timing export, local stop control and disconnect only; fixture provider, no microphone/speaker/acoustic acceptance'},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
