/* Exercise movies already received by the app. This script does not import files. */
const acceptedAssets = __ACCEPTED_ASSETS__;
(async () => {
  const report = {passed:false, checks:{}, sources:[], errors:[]};
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const check = (value, label) => { if (!value) throw new Error(label); report.checks[label] = true; };
  window.addEventListener('error', event => report.errors.push(event.message));
  window.addEventListener('unhandledrejection', event => report.errors.push(String(event.reason)));
  async function until(test, label, timeout=60000) {
    const end = Date.now()+timeout;
    while (Date.now()<end) { if (await test()) return; await wait(100); }
    throw new Error(label+': '+$('notice').textContent);
  }
  function clips() { return timeline.tracks.find(t => t.name==='V1').items.filter(i => i.type==='clip'); }
  function selectClip(item) {
    const node=[...document.querySelectorAll('.clip')].find(n=>n.dataset.ref===item.item_ref);
    check(node, 'timeline clip exists');
    node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  }
  async function pixels(at) {
    const v=$('video'); v.pause(); v.currentTime=at;
    await until(()=>!v.seeking && v.readyState>=2 && Math.abs(v.currentTime-at)<.06,'decoded seek');
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const c=document.createElement('canvas'); c.width=96; c.height=54;
    const context=c.getContext('2d'); context.drawImage(v,0,0,96,54);
    return context.getImageData(0,0,96,54).data;
  }
  function difference(a,b) { let sum=0; for(let i=0;i<a.length;i++) if(i%4!==3) sum+=Math.abs(a[i]-b[i]); return sum/(a.length*.75); }
  function luma(p) { let sum=0; for(let i=0;i<p.length;i+=4) sum+=.2126*p[i]+.7152*p[i+1]+.0722*p[i+2]; return sum/(p.length/4); }
  async function renderedPreview() {
    setMode('program'); $('preview-video').click();
    await until(()=>program?.revision===timeline.revision && !activeRender,'actual sequence render',240000);
    const job=renderJobs.find(j=>j.status==='completed'&&j.project_revision===timeline.revision&&j.output_path?.includes('/cache/'));
    check(job,'current sequence preview'); return job;
  }
  try {
    await until(()=>timeline?.revision && !refreshing && acceptedAssets.every(id=>state.assets.some(a=>a.id===id)),'imported project ready');
    check(location.protocol==='filmapp:' && window.FilmDesktop,'native application');
    check(clips().length===0,'test sequence starts empty');
    $('quickstart-dialog')?.close();
    for(const id of acceptedAssets) {
      await selectAsset(id);
      await until(()=>sourceInfo?.kind==='video' && !sourceInfo.loading && $('video').readyState>=2,'movie ready');
      const v=$('video'); check(v.duration>2.5 && v.videoWidth>0,'actual movie has duration and dimensions');
      const first=await pixels(.55), last=await pixels(1.7), motion=difference(first,last);
      check(motion>.3,'native source pictures change '+id);
      v.currentTime=.25; await v.play(); await until(()=>v.currentTime>.5 && !v.paused,'native source plays'); v.pause();
      report.sources.push({asset_id:id,width:v.videoWidth,height:v.videoHeight,duration:v.duration,native_motion_mad:motion});
      $('target-track').value=timeline.tracks.find(t=>t.name==='V1').track_id;
      $('clip-in').value='.5'; $('clip-out').value='2.5';
      const count=clips().length; $('append').click();
      await until(()=>clips().length===count+1 && !editingNow,'append actual movie');
    }
    const original=clips()[1].duration_frames;
    selectClip(clips()[1]); $('selected-out').value=String((clips()[1].out_frame+1-5)/fps());
    let revision=timeline.revision; $('apply-timing').click();
    await until(()=>timeline.revision!==revision && !editingNow,'trim saves');
    check(clips()[1].duration_frames===original-5,'trim five frames');
    revision=timeline.revision; $('undo').click(); await until(()=>timeline.revision!==revision && !editingNow,'undo trim');
    check(clips()[1].duration_frames===original,'undo restores trim');
    revision=timeline.revision; $('redo').click(); await until(()=>timeline.revision!==revision && !editingNow,'redo trim');
    check(clips()[1].duration_frames===original-5,'redo restores edit');
    revision=timeline.revision; $('undo').click(); await until(()=>timeline.revision!==revision && !editingNow,'restore continuous sequence');
    await renderedPreview(); const beforeLuma=luma(await pixels(.8));
    selectClip(clips()[0]); $('color-brightness').value='115'; $('color-saturation').value='120';
    $('color-brightness').dispatchEvent(new Event('input',{bubbles:true}));
    $('color-saturation').dispatchEvent(new Event('input',{bubbles:true}));
    revision=timeline.revision; $('apply-color').click(); await until(()=>timeline.revision!==revision && !editingNow,'color edit saves');
    report.clips=clips().map(i=>({asset_id:i.asset_id,start_frame:i.start_frame,in_frame:i.in_frame,out_frame:i.out_frame,duration_frames:i.duration_frames}));
    report.profile={...timeline.profile}; report.frames=frameCount();
    const job=await renderedPreview(); report.preview=job.output_path.split('/').pop();
    const afterLuma=luma(await pixels(.8));
    check(afterLuma>beforeLuma+.2,'brightness changes rendered pixels');
    report.brightness_luma={before:beforeLuma,after:afterLuma};
    report.program_motion=[];
    for(const item of report.clips) {
      const a=await pixels((item.start_frame+12)/fps()),b=await pixels((item.start_frame+40)/fps());
      const motion=difference(a,b); check(motion>.3,'native rendered pictures change '+item.asset_id);
      report.program_motion.push({asset_id:item.asset_id,native_motion_mad:motion});
    }
    $('video').currentTime=.1; await $('video').play(); await until(()=>$('video').currentTime>.5,'rendered sequence plays'); $('video').pause();
    const previousJobs=new Set(renderJobs.map(j=>j.job_id));
    $('export-top').click(); $('export-preset').value='h264-high';
    const form=$('export-form'); form.requestSubmit(form.querySelector('[type=submit]'));
    report.export_publication_checks=0;
    await until(async()=>{
      const current=await api('/api/state'); report.export_publication_checks++;
      check(!current.outputs.some(item=>item.path.split('/').pop().startsWith('.')),'render staging files stay hidden');
      return renderJobs.some(j=>!previousJobs.has(j.job_id)&&j.status==='completed'&&j.project_revision===timeline.revision&&j.output_path?.includes('/exports/'))&&!activeRender;
    },'high quality export',240000);
    const exported=renderJobs.find(j=>!previousJobs.has(j.job_id)&&j.status==='completed'&&j.project_revision===timeline.revision&&j.output_path?.includes('/exports/'));
    report.export=exported.output_path.split('/').pop();
    $('jobs-dialog').close(); showBin('exports');
    const exportRow=()=>[...$('outputs').children].find(row=>row.dataset.path==='exports/'+report.export);
    await until(()=>!!exportRow()?.querySelector('[data-export-play]'),'completed export appears');
    exportRow().querySelector('[data-export-play]').click();
    await until(()=>$('video').readyState>=2&&new URL($('video').src).searchParams.get('path')==='exports/'+report.export,'completed export loads');
    report.export_motion=[];
    for(const item of report.clips) {
      const a=await pixels((item.start_frame+12)/fps()),b=await pixels((item.start_frame+40)/fps());
      const motion=difference(a,b); check(motion>.3,'native exported pictures change '+item.asset_id);
      report.export_motion.push({asset_id:item.asset_id,native_motion_mad:motion});
    }
    $('video').currentTime=.1; await $('video').play(); await until(()=>$('video').currentTime>.5,'export plays'); $('video').pause();
    check(report.errors.length===0,'no uncaught JavaScript errors'); report.passed=true;
  } catch(error) { report.error=String(error.stack||error); }
  window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});
})();
