/* Detection is local first; the AI receives only unresolved decisions or an edit request. */
(() => {
  let pendingAnalysis=new Set(),startingAnalysis=false,lastDetection=null;
  const reportDialog=el('dialog',undefined,'assistant-dialog');reportDialog.id='media-detection-dialog';
  const header=el('div',undefined,'dialog-heading');header.append(el('h2','Detected media'),button('✕',()=>reportDialog.close()));reportDialog.append(header);
  const summary=el('p',undefined,'hint'),items=el('div',undefined,'detection-items');reportDialog.append(summary,items);
  const analyze=button('Let Codex resolve unknowns',async()=>{reportDialog.close();await startAnalysis(true);});analyze.id='analyze-unknowns';reportDialog.append(analyze);
  function paintDetection(value){lastDetection=value;summary.textContent=value.configured?'Sequence matched the detected video.':value.status==='manual_settings_preserved'?'Your chosen sequence settings were preserved.':value.status==='nonempty_preserved'?'The existing edit and sequence settings were preserved.':'Local metadata analysis complete.';items.replaceChildren();
    for(const asset of value.assets||[]){const row=el('article',undefined,'detection-item');row.append(el('strong',asset.name));const v=asset.video;row.append(el('p',v?`${v.width} × ${v.height} · ${v.fps_num}/${v.fps_den} fps · ${v.codec||v.codec_name||v.pixel_format||'video'} · ${v.color_type||'Unknown color'}`:asset.kind||'Media'));if(asset.reasons?.length)row.append(el('p',asset.reasons.join(' '),'hint'));items.append(row);}
    if(value.reasons?.length)items.append(el('p',value.reasons.join(' '),'hint'));analyze.disabled=!value.requires_ai;
  }
  async function startAnalysis(explicit=false){
    if(explicit){const current=await call('film_detect_media',{});paintDetection(current);for(const item of current.assets||[])if(item.requires_ai)pendingAnalysis.add(item.asset_id);if(!pendingAnalysis.size&&current.requires_ai)(current.assets||[]).forEach(item=>pendingAnalysis.add(item.asset_id));}
    if(startingAnalysis||codexBusy||!pendingAnalysis.size){if(explicit&&codexBusy)throw new Error('Codex is working; unknown media is queued for its next analysis.');return;}
    startingAnalysis=true;const ids=[...pendingAnalysis];
    try{
      const text='Resolve only the inconclusive technical setup for these imported asset IDs: '+JSON.stringify(ids)+'. Run film_detect_media and inspect_project. Local metadata detection has already run; use its known facts without reinterpreting them. For genuinely unresolved media, inspect multiple timestamped frames through film_frames and film_view_image, plus metadata. Configure the empty sequence with a fresh revision if the evidence supports it. Preserve explicit manual settings and existing edits. Never infer a camera Log curve, LUT or HDR conversion from appearance alone; ask only for the missing source fact if it cannot be established. Report what is known, configured and still unresolved. This is a setup task: do not make a creative edit or export.';
      const args={text,images:[]};if($('model').value)args.model=$('model').value;if($('effort').value)args.effort=$('effort').value;
      await api('/api/codex/start',args);ids.forEach(id=>pendingAnalysis.delete(id));await poll();notice('Codex is resolving the unknown media settings.');
    }catch(e){notice('Media imported. Automatic AI analysis could not start: '+errorText(e)+'. Use Media info to retry.',true);if(explicit)throw e;}
    finally{startingAnalysis=false;}
  }
  window.detectImportedMedia=async ids=>{
    if(!ids.length||!timeline)return;notice('Detecting media and sequence settings…');
    try{const value=await call('film_configure_from_media',{asset_ids:ids,project_path:timeline.path,expected_revision:revision()});paintDetection(value);await refresh(true);
      for(const item of value.assets||[])if(item.requires_ai)pendingAnalysis.add(item.asset_id);
      if(value.requires_ai&&!pendingAnalysis.size)ids.forEach(id=>pendingAnalysis.add(id));
      if(value.requires_ai)await startAnalysis();
      else notice(value.configured?'Sequence configured from detected media.':'Media detected. Sequence settings preserved.');
    }catch(e){notice('Imported media is available; automatic setup needs review: '+errorText(e),true);}
  };
  const mediaInfo=button('Media info',async()=>{if(!state.assets.length)throw new Error('Import media first.');paintDetection(await call('film_detect_media',{}));reportDialog.showModal();});mediaInfo.id='media-info';mediaInfo.disabled=!editing;document.querySelector('.bin-footer').append(mediaInfo);document.body.append(reportDialog);

  const dialog=el('dialog',undefined,'assistant-dialog');dialog.id='ai-edit-dialog';const title=el('div',undefined,'dialog-heading');title.append(el('h2','AI edit & render'),button('✕',()=>dialog.close()));dialog.append(title);
  dialog.append(el('p','Describe the result. Codex will inspect your media, edit the saved sequence, render a preview, review it, and export the completed film. Leave the brief blank to let it choose a concise edit with natural sound.','hint'));
  const brief=el('textarea');brief.id='ai-edit-brief';brief.rows=5;brief.placeholder='For example: make a calm 30-second travel reel, preserve the best natural sound, and add my imported song softly.';brief.setAttribute('aria-label','Editing brief');dialog.append(brief);
  const status=el('p',undefined,'hint');status.id='ai-edit-status';dialog.append(status);
  const start=button('Start AI edit & render',async()=>{
    if(codexBusy)throw new Error('Let the current Codex task finish, or stop it first.');
    if(!state.assets.length)throw new Error('Import your media before starting an AI edit.');
    const request=brief.value.trim()||state.brief?.trim()||'Create a concise, coherent edit using the imported video. Choose a sensible length and pacing based on the actual footage. Preserve useful natural sound. Do not add music unless I have asked for it.';
    const text='Create and render my film. Creative brief:\n'+request+'\n\nWorkflow: detect media locally with film_detect_media and configure the empty sequence through film_configure_from_media using a current revision. Analyze frames only for unresolved technical decisions and for the creative edit itself. Respect explicit project settings and preserve originals. Inspect representative timestamped frames and actual image content before choosing shots. Edit the same active sequence; use revision-safe transactions. When the brief asks to cut to music, use film_music_cut_plan with the actual song source offset and beat phase, then author the returned frame boundaries with whole-beat pacing. Inspect and trim the visual shots to fit those boundaries. Render and inspect a preview, fix problems, then start a final H.264 export in exports/ and poll it to completion. Report its delivery_path in the dedicated videos-only project folder, or any delivery_warning. Preserve music credits when music is requested. If a necessary camera color transform cannot be established, report the specific missing fact instead of guessing. Do not ask for permission to perform these authorized edits or renders. Finish by reporting the saved output, duration and any remaining limitations.';
    const args={text,images:[]};if($('model').value)args.model=$('model').value;if($('effort').value)args.effort=$('effort').value;
    status.textContent='Starting Codex…';await api('/api/codex/start',args);pendingAnalysis.clear();await poll();dialog.close();notice('Codex is editing and rendering your film. Follow progress in the chat.');
  });start.id='ai-edit-start';start.className='primary';dialog.append(start);
  const open=button('AI edit',()=>{if(!brief.value.trim()&&state.brief)brief.value=state.brief;status.textContent='Uses your selected model and the current project permission mode.';dialog.showModal();});open.id='ai-edit-open';open.className='primary';open.disabled=!editing;document.querySelector('.workspaces').append(open);document.body.append(dialog);
  // A busy turn gets to finish; queued imports are reconsidered afterwards.
  let wasBusy=false;setInterval(()=>{if(wasBusy&&!codexBusy&&pendingAnalysis.size)startAnalysis();wasBusy=codexBusy;},1500);
  const format=$('project-form').elements.format,rate=$('project-form').elements.fps_num;
  const automatic=()=>{rate.disabled=format.value==='auto';};format.addEventListener('change',automatic);automatic();
})();
