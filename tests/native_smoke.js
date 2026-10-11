/* Runs inside the actual app's WKWebView; uses only the isolated synthetic project. */
(async () => {
  const report = {checks: {}, errors: []};
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function until(predicate, label, timeout = 60000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) { if (await predicate()) { report.checks[label] = true; return; } await wait(100); }
    throw new Error(label + ' timed out: ' + document.getElementById('notice')?.textContent);
  }
  function check(value, label) { if (!value) throw new Error(label); report.checks[label] = true; }
  window.addEventListener('error', event => report.errors.push(event.message));
  try {
    await until(() => state.assets.length && timeline?.revision && !refreshing, 'native project loaded');
    check(Boolean(window.FilmDesktop), 'native IPC adapter active');
    check(location.protocol === 'filmapp:', 'custom scheme UI');
    check(document.querySelectorAll('.track-strip').length >= 6, 'six visible tracks');
    check(document.querySelectorAll('.clip').length > 0, 'persisted timeline restored');
    document.getElementById('media-info').click();
    await until(() => document.getElementById('media-detection-dialog').open, 'native local media detection dialog');
    check(document.querySelector('#media-detection-dialog .detection-items').textContent.includes('640 × 360'), 'native detection reports source dimensions');
    document.getElementById('media-detection-dialog').close();
    document.getElementById('ai-edit-open').click();
    check(document.getElementById('ai-edit-dialog').open, 'native AI edit and render brief opens');
    document.getElementById('ai-edit-dialog').close();
    document.getElementById('quickstart-open').click();
    check(document.getElementById('quickstart-dialog').open, 'in-app quickstart opens');
    for(let step=0;step<6;step++){document.getElementById('quickstart-next').click();await wait(0);}
    check(document.querySelector('#quickstart-dialog h1').textContent === 'Preview, then export', 'all seven quickstart steps reachable');
    document.getElementById('quickstart-next').click();
    check(!document.getElementById('quickstart-dialog').open, 'quickstart closes into editing');
    check(Boolean(document.getElementById('film-advanced-open')), 'advanced controls loaded from local scheme');
    document.getElementById('film-advanced-open').click();
    check(document.getElementById('film-advanced-dialog').open, 'native advanced controls dialog opens');
    document.getElementById('film-advanced-dialog').close();
    document.getElementById('music-open').click();
    check(document.getElementById('music-dialog').open, 'native music dialog opens');
    check(document.getElementById('music-asset').options.length > 0, 'local song available in native music controls');
    if (!timeline.tracks.some(t => t.name === 'Music')) {
      document.getElementById('music-score').click();
      await until(() => timeline.tracks.some(t => t.name === 'Music'), 'native song fitting saves Music track', 120000);
    } else check(true, 'fitted soundtrack persists after native reopen');
    document.getElementById('music-dialog').close();
    const video = document.getElementById('video');
    await selectAsset(state.assets[0].id);
    await until(() => video.readyState >= 2 && video.videoWidth > 0, 'H264 source decoded in WKWebView');
    check(sourceInfo.url.startsWith('filmapp://app/media?'), 'source uses signed local scheme');
    await video.play();
    await until(() => video.currentTime > .25 && !video.paused, 'native source playback advances', 15000);
    video.pause(); video.currentTime = 2.1;
    await until(() => !video.seeking && Math.abs(video.currentTime - 2.1) < .2, 'native source seeks');
    report.source = {width: video.videoWidth, height: video.videoHeight, duration: video.duration, time: video.currentTime};
    setMode('program');
    await loadRenders();
    if (!program || program.revision !== timeline.revision) {
      await startRender(true);
      await until(async () => { await loadRenders(); return program?.revision === timeline?.revision; }, 'real timeline preview rendered', 120000);
    }
    displayProgram();
    await until(() => video.readyState >= 2 && video.videoWidth > 0, 'timeline preview decodes in WKWebView');
    await video.play();
    await until(() => video.currentTime > .2 && !video.paused, 'native timeline playback advances', 15000);
    video.pause();
    video.currentTime = 1.3;
    await until(() => !video.seeking && Math.abs(video.currentTime - 1.3) < .2, 'native timeline seek');
    const before = timeline.revision;
    const response = unpack(await FilmDesktop.api('/api/call', {name: 'film_brief', arguments: {text: 'Native synthetic validation only'}}));
    await refresh(true); check(state.brief === 'Native synthetic validation only', 'native MCP-shared state updates');
    check(timeline.revision === before, 'brief leaves timeline unchanged');
    report.timeline = {revision: timeline.revision, frames: frameCount(), clips: document.querySelectorAll('.clip').length};
    report.passed = report.errors.length === 0;
  } catch (error) {
    report.passed = false; report.error = error.message; report.stack = error.stack;
    const v = document.getElementById('video');
    report.video = {time: v.currentTime, paused: v.paused, duration: v.duration,
      readyState: v.readyState, networkState: v.networkState, error: v.error?.message || null,
      buffered: Array.from({length:v.buffered.length},(_,i)=>[v.buffered.start(i),v.buffered.end(i)])};
  }
  window.webkit.messageHandlers.film.postMessage({id: 'smoke', action: 'smokeComplete', body: report});
})();
