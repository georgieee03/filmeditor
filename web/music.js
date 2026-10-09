/* Local songs and an optional attributed public catalogue. Text never becomes markup. */
(() => {
  'use strict';
  const dialog=el('dialog',undefined,'music-dialog');dialog.id='music-dialog';
  const heading=el('div',undefined,'dialog-heading');heading.append(el('h2','Music'),button('✕',()=>dialog.close()));dialog.append(heading);
  dialog.append(el('p','Import your own song, then fit it to the picture. Common DRM-free audio files are supported.','hint'));
  const local=el('div',undefined,'music-row'),songs=el('select');songs.id='music-asset';songs.setAttribute('aria-label','Imported song');
  const file=el('input');file.type='file';file.multiple=true;file.accept='audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.aif,.aiff,.wma,.alac';file.hidden=true;
  async function importLocal(){
    if(window.FilmDesktop){const assets=await window.FilmDesktop.importFiles('audio');await finishImport(assets.map(a=>a.id));populate(assets.at(-1)?.id);}
    else file.click();
  }
  file.onchange=async()=>{try{const old=$('import-kind').value;$('import-kind').value='audio';try{await upload(file.files);}finally{$('import-kind').value=old;}populate(state.assets.at(-1)?.id);}catch(e){status.textContent=errorText(e);}finally{file.value='';}};
  local.append(songs,button('Import song…',importLocal),file);dialog.append(local);
  const settings=el('div',undefined,'music-settings');const fields={};
  for(const [key,label,value,min,max] of [['gain_db','Music level (dB)',-18,-60,0],['source_offset_seconds','Start inside song (s)',0,0,86400],['fade_in_seconds','Fade in (s)',.6,0,30],['fade_out_seconds','Fade out (s)',1.2,0,30]]){
    const row=el('label',label),input=el('input');input.type='number';input.min=min;input.max=max;input.step='.1';input.value=value;input.id='music-'+key;fields[key]=input;row.append(input);settings.append(row);
  }
  const loop=el('input');loop.type='checkbox';loop.checked=true;loop.id='music-loop';const loopLabel=el('label','Loop to fill picture');loopLabel.append(loop);settings.append(loopLabel);
  const beats=el('input');beats.type='checkbox';beats.checked=true;beats.id='music-beats';const beatLabel=el('label','Estimated beat markers');beatLabel.append(beats);settings.append(beatLabel);dialog.append(settings);
  const status=el('p','Choose a song. Existing picture and dialogue stay in place.','music-status');status.id='music-status';status.setAttribute('role','status');
  const score=button('Fit song to sequence',async()=>{
    if(!songs.value)throw new Error('Import or select a song first.');
    status.textContent='Preparing soundtrack and estimated beat markers…';
    const args={asset_id:songs.value,project_path:timeline?.path,expected_revision:revision(),loop:loop.checked,beat_markers:beats.checked};
    for(const [key,node] of Object.entries(fields)){const value=Number(node.value);if(!node.checkValidity()||!Number.isFinite(value))throw new Error('Check '+key.replaceAll('_',' '));args[key]=value;}
    try{const result=await call('film_music_score',args);await refresh(true);populate(songs.value);status.textContent=`Added ${result.duration_seconds.toFixed(2)} s on ${result.track}. ${result.estimated_bpm?`Estimated tempo: ${result.estimated_bpm} BPM.`:''} Preview the mix before export.`;credits.textContent=result.attribution;credits.hidden=false;notice('Soundtrack fitted. Press Play to review the mix.');}
    catch(e){status.textContent=errorText(e);throw e;}
  });score.id='music-score';score.className='primary';dialog.append(score,status);
  const credits=el('pre',undefined,'music-credits');credits.hidden=true;dialog.append(credits);
  dialog.append(el('h3','Discover music'));
  const search=el('form',undefined,'music-row'),query=el('input');query.id='music-query';query.placeholder='Mood, genre, title or artist';query.setAttribute('aria-label','Search music');query.maxLength=200;query.required=true;
  const submit=el('button','Search');submit.type='submit';search.append(query,submit);dialog.append(search);
  dialog.append(el('p','Search powered by Openverse · FilmEditor is not endorsed by Openverse.','hint'));
  dialog.append(el('p','CC0, Public Domain Mark and CC BY results. This catalog does not contain every song. Source metadata is not independently verified; retain credits and check source terms before publishing.','hint'));
  const results=el('div',undefined,'music-results');results.id='music-results';dialog.append(results);
  search.onsubmit=async e=>{e.preventDefault();submit.disabled=true;results.replaceChildren(el('p','Searching…'));try{
    const found=await call('film_music_search',{query:query.value.trim(),limit:12});results.replaceChildren();
    if(!found.tracks.length)results.append(el('p','No matching tracks. Try another query or import your own song.'));
    for(const track of found.tracks){const row=el('article',undefined,'music-result');row.append(el('strong',track.title),el('span',`${track.creator} · ${track.license.toUpperCase()} ${track.license_version||''} · ${Math.round(track.duration_seconds||0)} s${track.preview_only?' · preview audio':''}`));
      const detail=el('details'),summary=el('summary','Source and credits'),text=el('pre',track.attribution+'\nSource: '+track.source_url+'\nLicense: '+track.license_url);detail.append(summary,text);row.append(detail);
      row.append(button('Import song',async()=>{status.textContent='Downloading and validating '+track.title+'…';try{const imported=await call('film_music_import',{track_id:track.id});await refresh(true);populate(imported.asset.id);credits.textContent=imported.attribution;credits.hidden=false;status.textContent='Imported '+imported.asset.name+'. Choose Fit song to sequence, or place it manually.';await prepareMedia(imported.asset.id);notice('Song imported.');}catch(e){status.textContent=errorText(e);throw e;}}));results.append(row);
    }
  }catch(e){results.replaceChildren(el('p',errorText(e)));}finally{submit.disabled=false;}};
  function populate(current){const previous=current||songs.value;options(songs,state.assets.filter(a=>a.kind==='audio'||/\.(wav|mp3|m4a|aac|flac|ogg|opus|aiff?|wma|alac)$/i.test(a.name)).map(a=>({value:a.id,label:a.name})));if([...songs.options].some(o=>o.value===previous))songs.value=previous;score.disabled=!songs.options.length;}
  const open=button('♫ Music',()=>{populate(selectedAsset);dialog.showModal();});open.id='music-open';open.title='Import any supported local song or discover music';document.querySelector('.workspaces').append(open);document.body.append(dialog);
})();
