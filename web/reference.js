/* Bounded reference measurements and local audio extraction. Untrusted text stays text. */
(() => {
  'use strict';
  const dialog=el('dialog',undefined,'music-dialog reference-dialog');dialog.id='reference-dialog';
  const heading=el('div',undefined,'dialog-heading');heading.append(el('h2','Reference video'),button('✕',()=>dialog.close()));dialog.append(heading);
  dialog.append(el('p','Choose an imported edited video to study its cuts and rhythm, or extract its audio as a new media asset.','hint'));
  const videos=el('select');videos.id='reference-asset';videos.setAttribute('aria-label','Imported reference video');
  const choose=el('label','Video');choose.append(videos);const row=el('div',undefined,'music-row');row.append(choose);dialog.append(row);
  const settings=el('div',undefined,'music-settings');
  const start=el('input'),windowLength=el('input');
  for(const [node,label,id,value,min,max] of [[start,'Start inside video (s)','reference-start',0,0,86400],[windowLength,'Analysis window (s)','reference-window',60,1,300]]){
    node.type='number';node.min=min;node.max=max;node.step='.1';node.value=value;node.id=id;const labelNode=el('label',label);labelNode.append(node);settings.append(labelNode);
  }
  const useWindow=el('input');useWindow.type='checkbox';useWindow.id='reference-extract-window';
  const windowLabel=el('label','Extract audio from this window');windowLabel.append(useWindow);settings.append(windowLabel);dialog.append(settings);
  dialog.append(el('p','Analysis is limited to 5 minutes. Audio extraction uses the full selected stream up to 5 minutes, or the window above when selected.','hint'));
  const status=el('p','Select a reference video.','music-status');status.id='reference-status';status.setAttribute('role','status');
  const summary=el('div');summary.id='reference-summary';summary.hidden=true;
  const images=el('div');images.id='reference-images';images.hidden=true;
  const details=el('details'),detailsHeading=el('summary','Measurement details'),diagnostics=el('pre',undefined,'music-credits');details.append(detailsHeading,diagnostics);details.hidden=true;
  let busy=false,lastReport=null;
  function numeric(node,label){const value=Number(node.value);if(!node.checkValidity()||!Number.isFinite(value))throw new Error('Check '+label+'.');return value;}
  function chosen(){if(!videos.value)throw new Error('Import and select a video first.');const asset=state.assets.find(item=>item.id===videos.value);if(!asset)throw new Error('The selected reference is no longer in the project.');return asset;}
  function windowArgs(){return {asset_id:chosen().id,start_seconds:numeric(start,'the start time'),max_seconds:numeric(windowLength,'the analysis window')};}
  function sync(){for(const action of [analyze,extract,review])action.disabled=busy||!videos.options.length;for(const node of [videos,start,windowLength,useWindow])node.disabled=busy;}
  async function mediaImage(path,image){const response=await api('/api/media-url',{path});image.src=response.url;}
  function resetReport(){lastReport=null;summary.hidden=true;images.hidden=true;details.hidden=true;summary.replaceChildren();images.replaceChildren();diagnostics.textContent='';}
  async function showReport(report){
    const cuts=report.candidates.filter(item=>item.kind==='hard_cut_candidate');
    const gradual=report.candidates.filter(item=>item.kind==='gradual_transition_candidate'||item.kind==='fade_candidate');
    const others=report.candidates.length-cuts.length-gradual.length;
    const beginning=Number(report.start_seconds),length=Number(report.duration_analyzed_seconds);
    summary.replaceChildren(el('p',`Scanned ${beginning.toFixed(2)}–${(beginning+length).toFixed(2)} s at ${report.sample_fps} samples/s. ${cuts.length} candidate hard cut${cuts.length===1?'':'s'}, ${gradual.length} gradual change${gradual.length===1?'':'s'}${others?`, ${others} other abrupt change${others===1?'':'s'}`:''}.`));
    if(report.audio?.available){
      summary.append(el('p',`Estimated audio pulse: ${Number(report.audio.estimated_audio_pulse_bpm).toFixed(1)} BPM${report.audio.median_estimated_beats_per_cut!=null?`. Candidate cuts are roughly ${report.audio.median_estimated_beats_per_cut} estimated beats apart at the median`:''}.`));
    }else summary.append(el('p','No usable audio pulse estimate was measured for this window.'));
    summary.append(el('p','Transition labels are candidates. Motion, lighting or graphics can resemble a cut; quick or similar-looking cuts can be missed. Review the images. Audio tempo can be half or double the musical tempo.','hint'));
    if(report.partial)summary.append(el('p','This report covers the selected window.','hint'));
    if(report.candidates_truncated)summary.append(el('p','The candidate list reached its reporting limit. Inspect a shorter window for detail.','hint'));
    for(const warning of report.warnings||[])summary.append(el('p',warning,'hint'));
    summary.hidden=false;images.replaceChildren();images.hidden=false;
    const sheet=el('img');sheet.id='reference-contact-sheet';sheet.alt='Sampled frames from the selected reference video';sheet.style.maxWidth='100%';sheet.style.height='auto';sheet.style.display='block';
    const caption=el('p',(report.contact_sheet_tiles||[]).map(item=>`Tile ${item.tile}: ${Number(item.source_seconds).toFixed(2)} s`).join(' · '),'hint');
    images.append(el('h3','Sampled contact sheet'),sheet,caption);
    if(report.contact_sheet_path)await mediaImage(report.contact_sheet_path,sheet);
    const preview=el('img');preview.id='reference-review-frame';preview.alt='Individual reference-video review frame';preview.style.maxWidth='100%';preview.style.height='auto';preview.hidden=true;
    const buttons=el('div',undefined,'music-row');buttons.style.flexWrap='wrap';
    for(const frame of report.review_frames||[]){buttons.append(button(`Frame at ${Number(frame.source_seconds).toFixed(2)} s`,async()=>{await mediaImage(frame.path,preview);preview.hidden=false;}));}
    if(buttons.childElementCount)images.append(buttons,preview);
    diagnostics.textContent=JSON.stringify(report,null,2);details.hidden=false;
  }
  const analyze=button('Analyze transitions',async()=>{
    const args=windowArgs();busy=true;sync();resetReport();status.textContent='Scanning the reference window and preparing review images…';
    try{const report=await call('film_reference_analyze',args);lastReport=report;await showReport(report);status.textContent='Measurements ready. Review the contact sheet and source frames.';}
    catch(error){status.textContent=errorText(error);throw error;}finally{busy=false;sync();}
  });analyze.id='reference-analyze';analyze.className='primary';
  const extract=button('Extract audio',async()=>{
    const asset=chosen();const args={asset_id:asset.id};
    if(useWindow.checked){args.start_seconds=numeric(start,'the start time');args.duration_seconds=numeric(windowLength,'the extraction window');}
    busy=true;sync();status.textContent='Extracting and validating the selected audio stream…';
    try{const extracted=await call('film_extract_audio',args);await refresh(true);status.textContent=`Created ${extracted.asset.name} (${Number(extracted.duration_seconds).toFixed(2)} s). The source video is preserved.`;notice('Extracted audio added to the Project panel.');if(state.assets.some(item=>item.id===extracted.asset.id))await selectAsset(extracted.asset.id);}
    catch(error){status.textContent=errorText(error);throw error;}finally{busy=false;sync();}
  });extract.id='reference-extract';
  const review=button('Review with Codex',()=>{
    const args=windowArgs();
    const prompt=`Review imported edited reference video asset_id=${JSON.stringify(args.asset_id)} from ${args.start_seconds} s for up to ${args.max_seconds} s. `+
      `Use film_reference_analyze, then film_view_image on the contact sheet and relevant individual review frames. `+
      `Describe the transition types visible in the actual images and the candidate cut-to-audio/BPM relationship. `+
      `Distinguish measured timing from ambiguous transition identity and tempo estimates. This request is a read-only reference review.`+
      (lastReport?.asset_id===args.asset_id&&lastReport.report_path?` A prior local report is at ${JSON.stringify(lastReport.report_path)}; its visual review is pending.`:'');
    const composer=$('prompt');composer.value=prompt;dialog.close();composer.focus();notice('Reference review prompt is ready. Send it when you are ready.');
  });review.id='reference-codex';
  const actions=el('div',undefined,'music-row');actions.style.flexWrap='wrap';actions.append(analyze,extract,review);dialog.append(actions,status,summary,images,details);
  videos.onchange=()=>{resetReport();status.textContent=videos.value?'Reference selected. Choose an analysis or extraction action.':'Select a reference video.';};
  function populate(current){
    const previous=current||videos.value;
    const available=state.assets.filter(asset=>/\.(mp4|mov|m4v|mkv|webm|avi|mts|m2ts|mxf)$/i.test(asset.path||asset.name));
    options(videos,available.map(asset=>({value:asset.id,label:`${asset.label||'Video'} · ${asset.name}`})));
    if([...videos.options].some(option=>option.value===previous))videos.value=previous;
    if(lastReport&&lastReport.asset_id!==videos.value)resetReport();
    status.textContent=available.length?'Select a video and an action.':'Import a video or video reference into the Project panel first.';sync();
  }
  const open=button('▧ Reference',()=>{populate(selectedAsset);dialog.showModal();});open.id='reference-open';open.title='Analyze an edited reference video or extract its audio';
  document.querySelector('.workspaces').append(open);document.body.append(dialog);
})();
