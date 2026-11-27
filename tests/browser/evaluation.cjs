/* Run with PLAYWRIGHT_MODULE=/path/to/playwright and optional CINEID_BROWSER_EXECUTABLE. */
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
// The user requires regular Chrome to be closed. Fail closed if the process check is unavailable.
const running=execFileSync('ps',['-axo','comm='],{encoding:'utf8'});
if(running.split('\n').some(line=>/\/Google Chrome\.app\/Contents\/MacOS\/Google Chrome$/.test(line.trim())))
  throw Error('Close regular Google Chrome before running this browser test. No browser was launched.');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const port=Number(process.env.CINEID_TEST_PORT||8767),origin=`http://127.0.0.1:${port}`;
 const profile=fs.mkdtempSync(path.join(os.tmpdir(),'goldfish-eval-browser-profile-'));
 const server=spawn(path.join(root,'venv/bin/python'),[path.join(__dirname,'evaluation_server.py'),String(port)],{cwd:root,env:{...process.env,NO_ALBUMENTATIONS_UPDATE:'1'},stdio:['ignore','pipe','pipe']});
 let context;
 try{
  let ready=false;
  for(let i=0;i<300;i++){try{if((await fetch(origin+'/api/eval/status')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
  assert.ok(ready,'Fixture server did not start');
  context=await chromium.launchPersistentContext(profile,{headless:true,executablePath:process.env.CINEID_BROWSER_EXECUTABLE||undefined,viewport:{width:1280,height:1000}});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>r.request().url().startsWith(origin+'/')?r.continue():r.abort());
  await page.goto(origin+'/ui/#/settings');
  await page.getByLabel('Save each Identify frame locally').check();await page.getByLabel('I understand that my entire display may be saved').check();
  await page.getByRole('button',{name:'Save evaluation settings',exact:true}).click();
  await page.getByText('Evaluation mode ON — every Identify press can save your display.',{exact:true}).waitFor();
  await page.getByRole('link',{name:'Label evaluation frames',exact:true}).click();
  const canvas=page.locator('.eval-canvas');await canvas.waitFor();await page.waitForFunction(()=>document.querySelector('canvas')?.width===500);
  assert.equal(await page.getByText('Guesses are hidden. Draw every face, then choose identities.',{exact:true}).count(),1);
  const rect=await canvas.boundingBox();await page.mouse.move(rect.x+rect.width*.2,rect.y+rect.height*.2);await page.mouse.down();await page.mouse.move(rect.x+rect.width*.4,rect.y+rect.height*.65);await page.mouse.up();
  await page.getByLabel('Identity for face 1',{exact:true}).selectOption('mara');
  await page.getByLabel('I labelled every visible face; include this frame in scoring').check();await page.getByRole('button',{name:'Save labels',exact:true}).click();await page.getByText('Labels saved locally',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Reveal predictions',exact:true}).click();await page.getByText(/This exposure is recorded/).waitFor();
  await page.reload();await page.getByLabel('Identity for face 1',{exact:true}).waitFor();assert.equal(await page.getByLabel('Identity for face 1',{exact:true}).inputValue(),'mara');
  await page.getByLabel('Identity for face 1',{exact:true}).selectOption('cant_determine');await page.getByRole('button',{name:'Save labels',exact:true}).click();await page.getByText('Labels saved locally',{exact:true}).waitFor();
  const entries=(await(await page.request.get(origin+'/api/eval/frames')).json()).frames;
  const detail=await(await page.request.get(origin+`/api/eval/frames/${entries[0].id}`)).json();
  assert.equal(detail.label.prediction_revealed,true);assert.ok(detail.revision>=3);assert.deepEqual(errors,[]);
  await page.getByRole('link',{name:'Settings',exact:true}).click();page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Delete evaluation data',exact:true}).click();
  await page.getByText(/0 frames · 0.0 MB/).waitFor();
  const status=await(await page.request.get(origin+'/api/eval/status')).json();assert.equal(status.eval_mode,false);assert.equal(status.frames,0);
  console.log(JSON.stringify({passed:true,checks:['privacy acknowledgement','settings Save','drag face box','identity selection','labels Save/reload/edit','prediction exposure recorded','confirmed deletion'],browserErrors:errors}));
 }finally{
  if(context)await context.close();if(server.exitCode===null && server.signalCode===null){server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));}fs.rmSync(profile,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exitCode=1});
