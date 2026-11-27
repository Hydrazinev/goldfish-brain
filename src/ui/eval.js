"use strict";

// Local evaluation only. No keys, external images, model requests, or uploads.
function evalSettingsCard() {
  const host = h('section', {class:'card stack'}, h('h2', {text:'Recognition evaluation'}));
  const draw = async () => {
    const r = await api('/eval/status');
    if (!r.ok) { host.append(h('p', {text:r.data.detail || 'Evaluation storage unavailable.'})); return; }
    const s = r.data;
    const enabled = h('input', {type:'checkbox',id:'eval-enabled',checked:s.eval_mode});
    const ack = h('input', {type:'checkbox',id:'eval-ack',checked:s.eval_mode});
    const cap = h('input', {type:'number',id:'eval-cap',min:10,max:5000,value:s.max_mb});
    const message = h('p', {class:'msg',role:'status'});
    const save = h('button', {text:'Save evaluation settings',type:'button',onclick:async()=>{
      save.disabled=true;
      const response = await api('/eval/settings',{method:'POST',body:{eval_mode:enabled.checked,max_mb:Number(cap.value),acknowledge_full_display:ack.checked}});
      save.disabled=false;
      if (response.ok) { await draw(); } else message.textContent=response.data.detail || response.data.message || 'Could not save.';
    }});
    const remove = h('button', {text:'Delete evaluation data',type:'button',onclick:async()=>{
      if (!confirm('Delete all evaluation frames, labels, splits and replay files? This turns evaluation mode off. Ask results and your library are kept.')) return;
      remove.disabled=true;
      const response=await api('/eval/delete',{method:'POST',body:{confirmation:'DELETE'}});
      if(response.ok) await draw(); else {remove.disabled=false;message.textContent=response.data.detail || 'Could not delete evaluation data.';}
    }});
    host.replaceChildren(h('h2',{text:'Recognition evaluation'}),
      h('p',{class:'banner',text:'Whole-display capture: Identify saves the entire display, including other windows and private content. Evaluation files stay on this Mac. The evaluation corpus is never read by feedback or upload code.'}),
      h('label',{class:'check',for:'eval-enabled'},enabled,'Save each Identify frame locally'),
      h('label',{class:'check',for:'eval-ack'},ack,'I understand that my entire display may be saved'),
      h('label',{class:'field',for:'eval-cap'},'Storage cap (MB, 10–5000)',cap),
      h('p',{class:'help',text:`${s.frames} frames · ${(s.bytes/1048576).toFixed(1)} MB / ${s.max_mb} MB. ${s.last_capture || ''}`}),
      h('p',{class:s.eval_mode?'banner':'help',text:s.eval_mode?'Evaluation mode ON — every Identify press can save your display.':'Evaluation mode is off. No evaluation frames are being collected.'}),
      h('div',{class:'row wrap'},save,h('a',{class:'btn',href:'#/eval',text:'Label evaluation frames'}),remove),message);
  };
  draw(); return host;
}

async function viewEval() {
  clearTimers();
  const host=h('section',{class:'card stack'});
  app.replaceChildren(h('div',{class:'stack'},h('h1',{text:'Label recognition frames'}),
    h('p',{class:'muted',text:'Draw a box around every face, including missed faces. Label before revealing guesses when possible. Real frames stay on this Mac.'}),host));
  const r=await api('/eval/frames');
  if(!r.ok){host.append(h('p',{text:r.data.detail || 'Could not load frames.'}));return;}
  const frames=r.data.frames;
  if(!frames.length){host.append(h('p',{text:'No evaluation frames yet. Enable evaluation in Settings, then press ⌘⇧X during normal viewing.'}),h('a',{class:'btn',href:'#/settings',text:'Open Settings'}));return;}
  let current=0,info=null,faces=[],selected=-1,dirty=false,loading=0,namesEpoch=0;
  const status=h('p',{class:'msg',role:'status','aria-live':'polite'});
  const chooser=h('select',{'aria-label':'Evaluation frame'},frames.map((f,i)=>h('option',{value:i,text:`${i+1}. ${f.show_id} S${f.season}E${f.episode}${f.complete?' · labelled':''}`})));
  const canvas=h('canvas',{class:'eval-canvas','aria-label':'Frame: drag to draw a face box. Keyboard users can use Add face box and coordinates below.'});
  const image=new Image(); let start=null,preview=null;
  const list=h('div',{class:'eval-face-list'}),editor=h('div',{class:'stack'}),predictionText=h('p',{class:'help'});
  const noFaces=h('input',{type:'checkbox',id:'eval-no-faces'});
  const complete=h('input',{type:'checkbox',id:'eval-complete'});
  const allNames=h('input',{type:'checkbox',id:'eval-all-names'});
  const paint=()=>{
    if(!info || !image.complete || !image.naturalWidth)return;
    const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0);
    ctx.lineWidth=Math.max(2,canvas.width/500);
    faces.forEach((f,i)=>{ctx.strokeStyle=i===selected?'#ce3e21':'#19865b';ctx.setLineDash([]);ctx.strokeRect(f.bbox[0],f.bbox[1],f.bbox[2]-f.bbox[0],f.bbox[3]-f.bbox[1]);ctx.font=`${Math.max(16,canvas.width/65)}px sans-serif`;ctx.fillStyle=ctx.strokeStyle;ctx.fillText(String(i+1),f.bbox[0]+3,Math.max(20,f.bbox[1]-4));});
    (info.predictions||[]).forEach(p=>{if(!p.bbox)return;ctx.strokeStyle='#446eb4';ctx.setLineDash([8,5]);ctx.strokeRect(p.bbox[0],p.bbox[1],p.bbox[2]-p.bbox[0],p.bbox[3]-p.bbox[1]);});ctx.setLineDash([]);
    if(preview){ctx.strokeStyle='#ce3e21';ctx.strokeRect(preview[0],preview[1],preview[2]-preview[0],preview[3]-preview[1]);}
  };
  const changed=()=>{dirty=true;status.textContent='Unsaved labels';paint();};
  const drawEditor=()=>{
    list.replaceChildren(...faces.map((f,i)=>h('div',{class:'row'},h('button',{type:'button',text:`Face ${i+1}: ${(info.choices||[]).find(c=>c.id===f.identity)?.name || ({extra:'Extra',not_in_gallery:'Not in gallery',cant_determine:'Cannot determine',false_detection:'False detection'}[f.identity]) || (f.identity?'Identity saved':'choose identity')}`,'aria-pressed':i===selected,onclick:()=>{selected=i;drawEditor();paint();}}),h('button',{type:'button',text:'Remove','aria-label':`Remove face ${i+1}`,onclick:()=>{faces.splice(i,1);selected=Math.min(selected,faces.length-1);drawEditor();changed();}}))));
    editor.replaceChildren();if(selected<0 || !faces[selected])return;
    const face=faces[selected];
    const identities=[['','Choose an identity'],...(info.choices||[]).map(c=>[c.id,c.name]),['not_in_gallery','Cast member without a gallery reference'],['extra','Extra / not a cast member'],['cant_determine','Cannot determine identity'],['false_detection','False face detection']];
    if(face.identity && !identities.some(x=>x[0]===face.identity))identities.push([face.identity,'Previously saved identity (name withheld)']);
    const identity=h('select',{'aria-label':`Identity for face ${selected+1}`},identities.map(([value,text])=>h('option',{value,text})));identity.value=face.identity||'';
    identity.addEventListener('change',()=>{face.identity=identity.value;changed();drawEditor();});
    const coords=h('div',{class:'row wrap'});
    ['Left','Top','Right','Bottom'].forEach((name,i)=>{const input=h('input',{type:'number',min:0,max:i%2?info.height:info.width,value:Math.round(face.bbox[i]),'aria-label':`${name} coordinate`});input.addEventListener('change',()=>{face.bbox[i]=Number(input.value);changed();});coords.append(h('label',{class:'field'},name,input));});
    const tags=h('div',{class:'row wrap'});
    ['dark','profile','small','occluded'].forEach(tag=>{const input=h('input',{type:'checkbox',checked:(face.tags||[]).includes(tag),'aria-label':tag});input.addEventListener('change',()=>{face.tags=input.checked?[...new Set([...(face.tags||[]),tag])]:(face.tags||[]).filter(t=>t!==tag);changed();});tags.append(h('label',{class:'check'},input,tag));});
    // One-click picker: a photo for every character, then the special answers.
    const pick=value=>{face.identity=value;identity.value=value;changed();const next=faces.findIndex((f,i)=>i!==selected&&!f.identity);if(next>=0)selected=next;drawEditor();};
    const grid=h('div',{class:'pick-grid',role:'group','aria-label':'Click who this face is'},...(info.choices||[]).map(c=>h('button',{type:'button',class:'pick','aria-pressed':String(face.identity===c.id),title:c.name,onclick:()=>pick(c.id)},h('img',{src:`/api/characters/${encodeURIComponent(c.id)}/photo`,alt:'',loading:'lazy',width:56,height:56}),h('span',{text:c.name}))));
    const specials=h('div',{class:'pick-grid specials'},...[['extra','Extra / not cast'],['not_in_gallery','Cast, no photos'],['cant_determine','Not sure'],['false_detection','Not a face']].map(([v,t])=>h('button',{type:'button',class:'pick special','aria-pressed':String(face.identity===v),onclick:()=>pick(v)},t)));
    editor.append(h('h3',{text:`Face ${selected+1}: click who it is`}),grid,specials,h('details',{},h('summary',{text:'Use the list instead'}),identity),coords,tags);
  };
  const addBox=box=>{faces.push({bbox:box,identity:'',tags:[]});selected=faces.length-1;noFaces.checked=false;drawEditor();changed();};
  const point=e=>{const r=canvas.getBoundingClientRect();return[Math.max(0,Math.min(canvas.width,(e.clientX-r.left)*canvas.width/r.width)),Math.max(0,Math.min(canvas.height,(e.clientY-r.top)*canvas.height/r.height))];};
  canvas.addEventListener('pointerdown',e=>{if(!info)return;start=point(e);canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!start)return;const p=point(e);preview=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.max(start[0],p[0]),Math.max(start[1],p[1])];paint();});
  canvas.addEventListener('pointerup',e=>{if(!start)return;const p=point(e),box=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.max(start[0],p[0]),Math.max(start[1],p[1])];start=preview=null;if(box[2]-box[0]>=3 && box[3]-box[1]>=3)addBox(box);paint();});
  canvas.addEventListener('pointercancel',()=>{start=preview=null;paint();});
  const load=async index=>{
    if(dirty && !confirm('Discard unsaved labels for this frame?')){chooser.value=current;return;}
    const token=++loading;namesEpoch++;dirty=false;info=null;faces=[];selected=-1;start=preview=null;drawEditor();status.textContent='Loading frame…';current=index;chooser.value=index;const requestedAll=allNames.checked;
    const response=await api(`/eval/frames/${frames[index].id}${allNames.checked?'?all=true':''}`);
    if(token!==loading || requestedAll!==allNames.checked)return;
    if(!response.ok){status.textContent=response.data.detail||'Could not load frame.';return;}
    info=response.data;faces=JSON.parse(JSON.stringify(info.label?.faces||[]));selected=faces.length?0:-1;dirty=false;
    noFaces.checked=!!info.label?.no_faces;complete.checked=!!info.label?.complete;
    canvas.width=info.width;canvas.height=info.height;image.onload=paint;image.src=`/api/eval/frames/${info.id}/image`;
    predictionText.textContent=info.prediction_revealed?`Predictions revealed: ${(info.predictions||[]).map(p=>p.name).join(', ')||'no detections'}`:'Guesses are hidden. Draw every face, then choose identities.';
    drawEditor();status.textContent='';
  };
  chooser.addEventListener('change',()=>load(Number(chooser.value)));
  allNames.addEventListener('change',async()=>{
    if(allNames.checked && !confirm('Show all gallery names, including characters you may not have met?')){allNames.checked=false;return;}
    if(!info){load(current);return;}
    const fid=info.id,mode=allNames.checked,epoch=++namesEpoch;
    if(!mode){info.choices=[];info.predictions=[];predictionText.textContent='Refreshing hidden-name view…';drawEditor();paint();}
    const r=await api(`/eval/frames/${fid}${mode?'?all=true':''}`);
    if(!info || info.id!==fid || mode!==allNames.checked || epoch!==namesEpoch)return;
    if(r.ok){info.choices=r.data.choices;info.predictions=r.data.predictions;predictionText.textContent=info.prediction_revealed?`Predictions revealed: ${(info.predictions||[]).map(p=>p.name).join(', ')||'no detections'}`:'Guesses are hidden.';drawEditor();paint();}
  });
  noFaces.addEventListener('change',()=>{if(noFaces.checked && faces.length){noFaces.checked=false;status.textContent='Remove the face boxes before marking no faces.';return;}changed();});
  complete.addEventListener('change',changed);
  const save=h('button',{type:'button',class:'primary',text:'Save labels',onclick:async()=>{
    if(!info)return;save.disabled=true;const fid=info.id;
    const r=await api(`/eval/frames/${fid}/labels`,{method:'POST',body:{base_revision:info.revision,faces,no_faces:noFaces.checked,complete:complete.checked}});
    save.disabled=false;
    if(!info || info.id!==fid)return;
    if(r.ok){info.revision=r.data.revision;info.label=r.data.label;dirty=false;status.textContent='Labels saved locally';}else status.textContent=r.data.detail||r.data.message||'Could not save labels.';
  }});
  const reveal=h('button',{type:'button',text:'Reveal predictions',onclick:async()=>{
    if(!info)return;reveal.disabled=true;const fid=info.id,mode=allNames.checked;
    let r=await api(`/eval/frames/${fid}/reveal${mode?'?all=true':''}`,{method:'POST',body:{base_revision:info.revision}});reveal.disabled=false;
    if(!info || info.id!==fid)return;
    if(mode!==allNames.checked)r=await api(`/eval/frames/${fid}${allNames.checked?'?all=true':''}`);
    if(!info || info.id!==fid)return;
    if(r.ok){info={...info,...r.data};predictionText.textContent=`Predictions revealed: ${(info.predictions||[]).map(p=>p.name).join(', ')||'no detections'}. This exposure is recorded.`;paint();}else status.textContent=r.data.detail||'Could not reveal predictions.';
  }});
  host.append(h('div',{class:'row wrap'},h('button',{text:'Previous',onclick:()=>load(Math.max(0,current-1))}),chooser,h('button',{text:'Next',onclick:()=>load(Math.min(frames.length-1,current+1))})),
    canvas,predictionText,h('div',{class:'row wrap'},h('button',{text:'Add face box',onclick:()=>{if(info)addBox([info.width*.25,info.height*.2,info.width*.5,info.height*.6]);}}),reveal),
    h('label',{class:'check',for:'eval-all-names'},allNames,'Show all gallery names (spoilers)'),list,editor,
    h('label',{class:'check',for:'eval-no-faces'},noFaces,'No faces here'),h('label',{class:'check',for:'eval-complete'},complete,'I labelled every visible face; include this frame in scoring'),save,status);
  await load(0);
}
