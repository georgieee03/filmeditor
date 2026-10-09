/* Opt-in live AI acceptance inside the installed WKWebView. Synthetic media only. */
(async () => {
  const report={checks:{},errors:[]};
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  async function until(predicate,label,timeout=60000){const end=Date.now()+timeout;while(Date.now()<end){if(await predicate()){report.checks[label]=true;return;}await wait(250);}throw new Error(label+' timed out: '+document.getElementById('notice')?.textContent);}
  window.addEventListener('error',event=>report.errors.push(event.message));
  try{
    await until(()=>state.assets.length===1&&timeline?.revision&&!refreshing,'native synthetic project loaded');
    document.getElementById('quickstart-dialog')?.close();
    document.getElementById('ai-edit-open').click();
    if(!document.getElementById('ai-edit-dialog').open)throw new Error('AI edit brief did not open');
    document.getElementById('ai-edit-brief').value=nativeAIRequestedBrief;
    document.getElementById('ai-edit-start').click();
    await until(()=>codexBusy,'live Codex turn started from native AI button',60000);
    await until(()=>!codexBusy,'live Codex turn completed',360000);
    const snapshot=await api('/api/editor'),events=(await api('/api/events?after=0')).events;
    const clips=snapshot.tracks.flatMap(track=>track.items.filter(item=>item.type==='clip'));
    if(clips.length!==1||clips[0].start_frame!==0||clips[0].duration_frames!==30)throw new Error('AI did not create the exact requested one-second clip');
    if(events.some(event=>event.kind==='approval'))throw new Error('Unexpected approval event');
    report.failed_mcp_tools=events.filter(event=>event.kind==='activity'&&event.data.tool&&event.data.status==='failed').map(event=>event.data.tool);
    if(report.failed_mcp_tools.some(tool=>['render_preview','render_status','start_render'].includes(tool)))throw new Error('A render MCP response failed');
    report.checks['preview and export MCP responses accepted']=true;
    report.checks['native AI authored exact requested clip']=true;
    report.checks['no approval events']=true;
    report.sequence_profile=snapshot.profile;
    report.tool_calls=events.filter(event=>event.kind==='activity'&&event.data.tool).map(event=>event.data.tool);
    report.passed=report.errors.length===0;
  }catch(error){report.passed=false;report.error=error.message;}
  window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});
})();
