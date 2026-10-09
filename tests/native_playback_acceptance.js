(async()=>{
  const report={checks:{}},video=document.getElementById('video');
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  async function until(fn,label,limit=60000){const end=Date.now()+limit;while(Date.now()<end){if(await fn()){report.checks[label]=true;return;}await wait(100);}throw Error(label+' timed out');}
  function check(value,label){if(!value)throw Error(label);report.checks[label]=true;}
  try {
    await until(()=>timeline?.revision&&state.assets.length===29&&!refreshing,'mixed bin restored');
    document.getElementById('quickstart-dialog')?.close();
    const movie=state.assets.find(a=>/\.(mp4|mov)$/i.test(a.name));
    const picture=state.assets.find(a=>/\.jpg$/i.test(a.name));
    check(movie&&picture,'video and still are separate assets');
    await selectAsset(picture.id);
    check(sourceInfo.kind==='image'&&sourceInfo.duration===5,'JPEG is a five-second still');
    await selectAsset(movie.id);
    check(sourceInfo.kind==='video'&&!sourceInfo.poster,'movie is playable video');
    await until(()=>video.readyState>=2&&video.videoWidth>0,'native video decoded');
    video.currentTime=1;await video.play();
    const tracks=document.getElementById('tracks')||document.querySelector('.tracks');
    let timelineReplacements=0;
    const observer=new MutationObserver(records=>{for(const r of records)if(r.type==='childList'&&r.target===tracks)timelineReplacements++;});
    if(tracks)observer.observe(tracks,{childList:true});
    await wait(8500);
    check(video.currentTime>8&&!video.paused,'source playback survives idle polling');
    video.pause();
    await refresh();await refresh();await loadRenders();await loadRenders();
    observer.disconnect();
    check(timelineReplacements===0,'unchanged refresh preserves timeline DOM');
    check(document.getElementById('viewer-overlay').hidden,'source loading overlay clears');
    report.source={width:video.videoWidth,height:video.videoHeight,duration:video.duration};
    setMode('program');await startRender(true);
    await until(async()=>{await loadRenders();return program?.revision===timeline.revision&&!activeRender;},'MLT mixed preview completes',120000);
    const expected=program.url;
    program={url:expected,revision:'obsolete-test-revision'};
    await loadRenders();
    check(program.revision===timeline.revision,'completed preview replaces obsolete revision');
    await until(()=>video.readyState>=2,'program preview decoded');
    await video.play();await until(()=>video.currentTime>.3&&!video.paused,'program preview plays');video.pause();
    video.currentTime=7.5;await until(()=>!video.seeking&&video.currentTime>7.3,'program seeks into late still');
    check(document.getElementById('save-state').textContent.includes('saved'),'saved state is consistent');
    report.timelineFrames=frameCount();report.passed=true;
  } catch(e){report.passed=false;report.error=e.message;report.video={ready:video.readyState,time:video.currentTime,error:video.error?.message};}
  window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});
})();
