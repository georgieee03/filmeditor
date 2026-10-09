/* Actual packaged WKWebView controls; no confirm stubs or synthetic pointer claims. */
(async () => {
  const report = {passed: false, checks: {}, errors: []};
  window.addEventListener('error', e => report.errors.push(e.message));
  window.addEventListener('unhandledrejection', e => report.errors.push(String(e.reason)));
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const check = (value, name) => { if (!value) throw new Error(name); report.checks[name] = true; };
  async function until(predicate, name, timeout = 90000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) { if (await predicate()) return; await wait(100); }
    throw new Error(name + ': ' + ($('notice')?.textContent || '') + ' ' + (document.querySelector('.film-enhance-message')?.textContent || ''));
  }
  function picture() { return timeline.tracks.find(t => t.name === 'V1').items.find(i => i.type === 'clip'); }
  function selectPicture() {
    const ref = picture().item_ref;
    const node = [...document.querySelectorAll('.clip')].find(n => n.dataset.ref === ref);
    node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  }
  function rowAction(row, text) {
    const control = [...row.querySelectorAll('button')].find(n => n.textContent === text);
    check(control && !control.disabled, text + ' enabled'); control.click();
  }
  try {
    await until(() => timeline?.revision && !refreshing && state.assets.length === 1, 'initial project');
    $('quickstart-dialog')?.close();
    check(location.protocol === 'filmapp:', 'packaged native interface');
    const original = state.assets[0];
    await selectAsset(original.id);
    $('target-track').value = timeline.tracks.find(t => t.name === 'V1').track_id;
    $('clip-in').value = '.5'; $('clip-out').value = '3.5'; $('append').click();
    await until(() => picture()?.duration_frames === 72, 'trimmed source append');
    const before = {...picture()}; const initialProfile = {...timeline.profile};
    selectPicture(); $('film-enhance-open').click();
    $('film-enhance-operation').value = 'reframe'; $('film-enhance-operation').dispatchEvent(new Event('change'));
    $('film-enhance-subject').value = 'auto'; $('film-enhance-aspect').value = '9:16';
    $('film-enhance-strength').value = 'standard'; $('film-enhance-start').click();
    await until(() => {
      const failed = document.querySelector('.film-enhance-job-state.failed');
      if (failed) throw new Error(failed.closest('article').textContent);
      return document.querySelector('.film-enhance-job[data-asset-id]:not([data-asset-id=""])');
    }, 'completed crop job');
    let row = document.querySelector('.film-enhance-job[data-asset-id]:not([data-asset-id=""])');
    const assetId = row.dataset.assetId; report.job_id = row.dataset.jobId;
    check(picture().asset_id === original.id, 'processing alone preserves timeline');
    rowAction(row, 'Preview result');
    await until(() => !$('film-enhance-dialog').open && selectedAsset === assetId && $('video').readyState >= 2, 'result preview');
    check(Math.abs($('video').videoWidth / $('video').videoHeight - 9/16) < .001, 'portrait output decodes in native monitor');
    $('video').currentTime = .4; await $('video').play();
    await until(() => $('video').currentTime > .7, 'portrait source playback'); $('video').pause();
    selectPicture(); $('film-enhance-open').click();
    await until(() => ![...row.querySelectorAll('button')].find(n => n.textContent === 'Use on selected clip').disabled, 'replacement enabled');
    const priorRevision = timeline.revision; rowAction(row, 'Use on selected clip');
    await until(() => timeline.revision !== priorRevision && picture().asset_id === assetId, 'revision-safe replacement');
    check(['in_frame', 'out_frame', 'start_frame', 'duration_frames'].every(key => picture()[key] === before[key]), 'replacement retains trimmed timing');
    rowAction(row, 'Match sequence to result');
    await until(() => Number(timeline.profile.width) / Number(timeline.profile.height) === 9/16, 'explicit portrait sequence');
    check(timeline.profile.frame_rate_num === initialProfile.frame_rate_num && timeline.profile.frame_rate_den === initialProfile.frame_rate_den, 'format match retains exact FPS');
    check(picture().duration_frames === 72, 'format match retains frame count');
    $('film-enhance-dialog').close(); setMode('program');
    $('preview-video').click();
    await until(() => program?.revision === timeline.revision && !activeRender, 'portrait sequence preview', 150000);
    report.preview = renderJobs.find(j => j.status === 'completed' && j.project_revision === timeline.revision)?.output_path?.split('/').pop();
    check(Boolean(report.preview), 'current revision preview exists');
    await $('video').play(); await until(() => $('video').currentTime > .5, 'portrait timeline playback'); $('video').pause();
    let revision = timeline.revision; $('undo').click();
    await until(() => timeline.revision !== revision, 'undo format');
    check(timeline.profile.width === initialProfile.width && timeline.profile.height === initialProfile.height, 'undo restores landscape profile');
    revision = timeline.revision; $('undo').click(); await until(() => timeline.revision !== revision, 'undo source replacement');
    check(picture().asset_id === original.id && picture().in_frame === before.in_frame, 'undo restores original clip and source range');
    check(report.errors.length === 0, 'no uncaught native JavaScript errors');
    report.passed = true;
  } catch (error) { report.error = error.message; }
  window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});
})();
