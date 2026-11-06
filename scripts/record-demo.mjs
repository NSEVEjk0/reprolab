import {createRequire} from 'node:module';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';

// Run from the repository or the Vultr host. One real repair, no automatic job retry.
// Only the demo access code enters the browser; the inference key is never read.
const root=resolve(process.env.REPROLAB_ROOT||'.');
const require=createRequire(resolve(root,'package.json'));
const {chromium}=require('@playwright/test');
const origin='https://139.84.249.47.sslip.io';
const out=resolve(root,'reports/submission');
let ffmpeg=process.env.FFMPEG_BIN;
if(!ffmpeg){
  const located=spawnSync('python3',['-c','import shutil; print(shutil.which("ffmpeg") or __import__("imageio_ffmpeg").get_ffmpeg_exe())'],{encoding:'utf8',timeout:5000});
  if(located.status!==0)throw new Error('An existing FFmpeg/libx264 installation is required. No repair was started.');
  ffmpeg=located.stdout.trim();
}
const available=spawnSync(ffmpeg,['-hide_banner','-encoders'],{encoding:'utf8',timeout:5000});
if(available.status!==0||!available.stdout.includes('libx264'))throw new Error('FFmpeg with its libx264 encoder is required before recording. No repair was started.');
const accessFile=process.env.REPROLAB_ACCESS_FILE||(existsSync('/etc/reprolab/demo-access.txt')?'/etc/reprolab/demo-access.txt':resolve(root,'.local/recording-access.txt'));
const access=(await readFile(accessFile,'utf8')).trim();
await mkdir(out,{recursive:true,mode:0o700});
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},bypassCSP:true,recordVideo:{dir:out,size:{width:1440,height:1000}}});
const page=await context.newPage(),video=page.video(),began=Date.now(),segments=[],pageErrors=[];
page.on('pageerror',error=>pageErrors.push(error.message));
const time=()=>((Date.now()-began)/1000).toFixed(3);
async function scene(caption,action,seconds=7){
  if(action)await action();
  await page.evaluate(text=>{
    let disclosure=document.querySelector('#recording-disclosure');
    if(!disclosure){disclosure=document.createElement('div');disclosure.id='recording-disclosure';disclosure.style.cssText='position:fixed;inset:0 0 auto;padding:9px 20px;background:#173c34;color:white;font:16px/1.4 system-ui;text-align:center;z-index:100001;pointer-events:none';document.body.append(disclosure);}
    disclosure.textContent='Actual Vultr live repair · Independent gVisor checks · Idle waiting edited';
    let box=document.querySelector('#recording-caption');
    if(!box){box=document.createElement('div');box.id='recording-caption';box.style.cssText='position:fixed;left:5%;right:5%;bottom:22px;padding:18px 24px;background:#173c34;color:white;border-radius:10px;font:22px/1.4 system-ui;z-index:99999;box-shadow:0 5px 30px #0003;pointer-events:none';document.body.append(box);}
    box.textContent=text;
  },caption);
  const start=time();await page.waitForTimeout(350);await page.screenshot({path:resolve(out,`reprolab-scene-${segments.length+1}.png`)});await page.waitForTimeout(seconds*1000-350);segments.push({start,end:time()});
}
let evidence;
try{
  await page.goto(origin,{waitUntil:'networkidle'});
  await page.getByRole('button',{name:/Discount rounding/}).click();
  await page.locator('#access').waitFor({state:'visible'});
  try{await page.locator('#access').fill(access);}catch{throw new Error('Could not enter the private demo access code. No repair was started.');}
  await page.locator('#access-label').evaluate(el=>{el.style.display='none';});
  await page.locator('#run').waitFor({state:'visible'});
  if(!await page.locator('#run').isEnabled())throw new Error('The live repair worker is unavailable. No repair was started.');
  await scene('ReproLab: a plausible AI repair can still be wrong. Start with a reproducible Python failure.');
  await scene('A real Vultr inference call proposes the repair. Acceptance stays with the independent evaluator.',()=>page.locator('#run').click(),5);
  await page.waitForFunction(()=>['Repair passes','Not verified'].includes(document.querySelector('#result-badge')?.textContent)||!document.querySelector('#error')?.classList.contains('hidden'),{},{timeout:240000});
  if(await page.locator('#result-badge').textContent()!=='Repair passes')throw new Error('The real repair did not pass; no success video was exported.');
  if(await page.locator('#candidate-passed').textContent()!=='20'||Number(await page.locator('#baseline-failed').textContent())<1)throw new Error('Independent baseline/candidate checks were not confirmed.');
  await scene('The original fails. The candidate passes all 20 independent cases; a success banner alone cannot pass.',()=>page.locator('#totals').scrollIntoViewIfNeeded());
  const downloadEvent=page.waitForEvent('download');await page.locator('#download').click();
  const download=await downloadEvent;await download.saveAs(resolve(out,'reprolab-live-evidence.json'));
  evidence=JSON.parse(await readFile(resolve(out,'reprolab-live-evidence.json'),'utf8'));
  assert.equal(createHash('sha256').update(evidence.canonical_payload).digest('hex'),evidence.sha256,'Downloaded evidence must match its canonical bytes');
  assert.deepEqual(JSON.parse(evidence.canonical_payload),evidence.payload,'Displayed and canonical evidence must agree');
  if(evidence.payload.status!=='verified'||evidence.payload.evidence_origin==='ci-reference-evaluation')throw new Error('The downloaded record is not a verified live repair.');
  for(const name of ['baseline','evaluation']){
    const run=evidence.payload[name];
    if(run.runtime!=='runsc'||!run.policy_verified||!run.teardown.confirmed||!Object.values(run.policy).every(Boolean))throw new Error('Sandbox policy or cleanup was not confirmed.');
  }
  await scene('Containment: this actual run used gVisor, no network, a read-only root, nonroot identity and bounded resources.',async()=>{
    await page.evaluate(run=>{
      const panel=document.createElement('section');panel.style.cssText='position:fixed;inset:18% 15% 22%;padding:50px;background:#f1f6f0;border:2px solid #aec9b7;border-radius:14px;z-index:9999;font:25px/1.7 system-ui;color:#173c34';
      const title=document.createElement('h2');title.textContent='Actual sandbox evidence';panel.append(title);
      for(const text of [`Runtime: ${run.runtime}`,`Independent policy check: ${run.policy_verified?'confirmed':'unconfirmed'}`,`Network blocked: ${run.policy.no_network}`,`Read-only root: ${run.policy.readonly_root}`,`Nonroot execution: ${run.policy.nonroot}`,`Sandbox removal: ${run.teardown.confirmed?'confirmed':'unconfirmed'}`]){const p=document.createElement('div');p.textContent=text;panel.append(p);}
      panel.id='containment-evidence';document.body.append(panel);
    },evidence.payload.evaluation);
  });
  await scene('Download the exact candidate and evidence. Source hashes, independent checks and confirmed cleanup travel together.',async()=>{await page.locator('#containment-evidence').evaluate(el=>el.remove());await page.locator('#candidate-code').scrollIntoViewIfNeeded();});
  await scene('Lifecycle containment: both actual gVisor sandboxes were removed. Verification requires confirmed teardown.',async()=>{
    await page.evaluate(({baseline,evaluation})=>{
      const panel=document.createElement('section');panel.id='lifecycle-evidence';panel.style.cssText='position:fixed;inset:18% 12% 22%;padding:44px;background:#f1f6f0;border:2px solid #aec9b7;border-radius:14px;z-index:9999;font:24px/1.6 system-ui;color:#173c34';
      const heading=document.createElement('h2');heading.textContent='Actual sandbox lifecycle evidence';panel.append(heading);
      for(const [title,run] of [['Original failure',baseline],['Generated repair',evaluation]]){const name=document.createElement('h3');name.textContent=title;panel.append(name);for(const text of [`Runtime: ${run.runtime}`,`Sandbox: ${run.sandbox_id}`,`Removal: ${run.teardown.confirmed?'confirmed':'unconfirmed'}`]){const row=document.createElement('div');row.textContent=text;row.style.overflowWrap='anywhere';panel.append(row);}}
      document.body.append(panel);
    },{baseline:evidence.payload.baseline,evaluation:evidence.payload.evaluation});
  });
  await scene('First scope: three Python fixtures. Hashes bind recorded bytes; they are not host attestations. Code: github.com/NSEVEjk0/reprolab',async()=>{await page.locator('#lifecycle-evidence').evaluate(el=>el.remove());await page.locator('#fingerprint-list').scrollIntoViewIfNeeded();});
  assert.deepEqual(pageErrors,[],'The actual live workflow must have no browser errors');
}finally{await context.close();await browser.close();}
if(!evidence)throw new Error('No live evidence was recorded.');
const raw=await video.path();
const filters=segments.map((s,i)=>`[0:v]trim=start=${s.start}:end=${s.end},setpts=PTS-STARTPTS[v${i}]`).join(';')+';'+segments.map((_,i)=>`[v${i}]`).join('')+`concat=n=${segments.length}:v=1:a=0[out]`;
const destination=resolve(out,'reprolab-demo.mp4');
const encoded=spawnSync(ffmpeg,['-hide_banner','-loglevel','error','-y','-i',raw,'-filter_complex',filters,'-map','[out]','-t','55','-r','24','-c:v','libx264','-preset','fast','-threads','2','-pix_fmt','yuv420p','-movflags','+faststart',destination],{encoding:'utf8',timeout:120000});
if(encoded.status!==0)throw new Error('MP4 export failed; the original private recording was retained.');
const duration=segments.reduce((sum,s)=>sum+Number(s.end)-Number(s.start),0);
const metadata=spawnSync(ffmpeg,['-hide_banner','-i',destination],{encoding:'utf8',timeout:10000});
const measured=/Duration: (\d+):(\d+):(\d+\.\d+)/.exec(metadata.stderr);
assert.ok(measured,'The exported MP4 duration must be readable');
const measuredSeconds=Number(measured[1])*3600+Number(measured[2])*60+Number(measured[3]);
assert.ok(measuredSeconds>40&&measuredSeconds<=55,'The MP4 must remain below 55 seconds');
const videoSHA256=createHash('sha256').update(await readFile(destination)).digest('hex');
await writeFile(resolve(out,'recording.json'),JSON.stringify({schema:'reprolab-recording-v1',origin,fixture:'rounding',liveEvidenceSHA256:evidence.sha256,videoSHA256,editedIdleWaiting:true,maximumDurationSeconds:55,selectedSeconds:duration,durationSeconds:measuredSeconds,video:'reprolab-demo.mp4',automaticJobRetries:0,liveRepairsStarted:1,containmentMoment:'Independent recorded sandbox policy and confirmed removal of the baseline/candidate gVisor sandboxes',candidateExecutedLocally:false},null,2)+'\n');
console.log(JSON.stringify({video:destination,durationSeconds:measuredSeconds,videoSHA256,liveRepairVerified:true,containmentPolicyConfirmed:true,automaticJobRetries:0}));
