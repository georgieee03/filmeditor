/* Native WKWebView acceptance for importing and editing synthetic media only. */
const fixtures = __MULTICLIP_FIXTURES__;
(async () => {
  const report = {passed: false, checks: {}, skipped: [], errors: []};
  const video = document.getElementById('video');
  window.addEventListener('error', event => report.errors.push(event.message || 'Uncaught browser error'));
  window.addEventListener('unhandledrejection', event => report.errors.push(String(event.reason?.message || event.reason || 'Unhandled promise rejection')));
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function check(value, label) {
    if (!value) throw new Error(label);
    report.checks[label] = true;
  }
  async function until(predicate, label, timeout = 60000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (await predicate()) { report.checks[label] = true; return; }
      await wait(100);
    }
    throw new Error(label + ' timed out: ' + document.getElementById('notice')?.textContent);
  }
  function clipItems(name) {
    const track = timeline?.tracks.find(item => item.name === name);
    return (track?.items || []).filter(item => item.type === 'clip');
  }
  function allItems(name, type) {
    const track = timeline?.tracks.find(item => item.name === name);
    return (track?.items || []).filter(item => item.type === type);
  }
  function selectTimelineClip(itemRef) {
    const node = [...document.querySelectorAll('.clip:not(.transition)')]
      .find(item => item.dataset.ref === itemRef);
    if (!node) throw new Error('Timeline clip was not found for selection.');
    node.focus();
    node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
  }
  async function importThroughInput(items, kind, expectedCount, label) {
    const input = document.getElementById('files');
    document.getElementById('import-kind').value = kind;
    const transfer = new DataTransfer();
    for (const item of items) {
      const binary = atob(item.base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
      transfer.items.add(new File([bytes], item.name, {type: item.type}));
    }
    Object.defineProperty(input, 'files', {configurable: true, value: transfer.files});
    input.dispatchEvent(new Event('change', {bubbles: true}));
    await until(() => state.assets.length === expectedCount, label + ' stored through the native import bridge', 90000);
    await until(() => document.getElementById('notice').textContent.includes(' imported.'), label + ' import flow finished', 90000);
  }
  function findAsset(name) {
    const value = state.assets.find(item => item.name === name);
    if (!value) throw new Error('Imported asset missing: ' + name);
    return value;
  }
  async function appendAsset(asset, trackName, range) {
    await selectAsset(asset.id);
    await until(() => sourceInfo?.kind && sourceInfo.loading === false, 'source metadata ready for ' + asset.name);
    const target = document.getElementById('target-track');
    target.value = timeline.tracks.find(track => track.name === trackName)?.track_id;
    if (!target.value) throw new Error('Timeline track missing: ' + trackName);
    document.getElementById('clip-in').value = String(range[0]);
    document.getElementById('clip-out').value = String(range[1]);
    const previous = clipItems(trackName).length;
    document.getElementById('append').click();
    await until(() => clipItems(trackName).length === previous + 1, 'append ' + asset.name + ' to ' + trackName);
    return clipItems(trackName).at(-1);
  }
  async function clickRevision(buttonId, label, timeout = 45000) {
    const before = timeline.revision;
    document.getElementById(buttonId).click();
    await until(() => timeline.revision !== before, label, timeout);
  }
  function clipByAsset(assetId, trackName = 'V1') {
    const track = timeline?.tracks.find(value => value.name === trackName);
    const item = track?.items.find(value => value.type === 'clip' && value.asset_id === assetId);
    if (!item) throw new Error('Edited clip is missing from the ' + trackName + ' track.');
    return {track, item};
  }
  function clipEvidence(item) {
    return {item_ref: item.item_ref, asset_id: item.asset_id, start_frame: item.start_frame,
      duration_frames: item.duration_frames, in_frame: item.in_frame, out_frame: item.out_frame};
  }
  function previewJobFor(rev) {
    return renderJobs.find(job => job.status === 'completed' && job.project_revision === rev &&
      job.output_path && job.url && job.output_path.includes('/cache/'));
  }
  async function renderPreview(rev, label) {
    document.getElementById('preview-video').click();
    await until(async () => {
      await loadRenders();
      return program?.revision === rev && !activeRender;
    }, label + ' completed', 180000);
    const job = previewJobFor(rev);
    if (!job) throw new Error(label + ' completed without a cache preview path.');
    return {file: job.output_path.split(/[\\/]/).pop(), frames: frameCount()};
  }
  async function editControl(id, value) {
    const input = document.getElementById(id);
    input.value = String(value);
    input.dispatchEvent(new Event('input', {bubbles: true}));
  }
  try {
    await until(() => timeline?.revision && !refreshing && state.assets.length === 0, 'native empty project loaded');
    check(Boolean(window.FilmDesktop) && location.protocol === 'filmapp:', 'native WKWebView and desktop bridge active');
    document.getElementById('zoom').value = '55';
    document.getElementById('quickstart-dialog')?.close();
    await importThroughInput(fixtures.filter(item => item.kind === 'footage'), 'footage', 3, 'three video files');
    await importThroughInput(fixtures.filter(item => item.kind === 'audio'), 'audio', 4, 'song file');

    const videoFixtures = fixtures.filter(item => item.kind === 'footage');
    const videoAssets = videoFixtures.map(item => findAsset(item.name));
    const audioAsset = findAsset(fixtures.find(item => item.kind === 'audio').name);
    const sourceInfoReport = [];
    for (const item of videoAssets) {
      await selectAsset(item.id);
      await until(() => sourceInfo?.kind === 'video' && !sourceInfo.loading, 'video identified as motion footage: ' + item.name);
      await until(() => video.readyState >= 2 && video.videoWidth === 640, 'video decoded in WKWebView: ' + item.name);
      video.currentTime = 0.25;
      await video.play();
      await until(() => video.currentTime > 0.4 && !video.paused, 'source playback advances: ' + item.name, 15000);
      video.pause();
      sourceInfoReport.push({kind: sourceInfo.kind, width: video.videoWidth, height: video.videoHeight,
        duration: Number(video.duration.toFixed(3)), poster: Boolean(sourceInfo.poster)});
    }
    check(sourceInfoReport.length === 3, 'all three distinct motion sources decoded and played');
    check(sourceInfoReport.every(item => item.kind === 'video' && !item.poster),
      'the native monitor classifies every H.264 source as video rather than a still poster');

    for (const item of videoAssets) await appendAsset(item, 'V1', [0.5, 2.5]);
    const videoTrack = timeline.tracks.find(track => track.name === 'V1');
    check(clipItems('V1').length === 3, 'three imported videos appended to V1 through source controls');
    check(clipItems('V1').every(item => item.duration_frames >= 47 && item.duration_frames <= 49), 'video source ranges preserve transition handles');
    check(clipItems('V1').map(item => item.asset_id).join(',') === videoAssets.map(item => item.id).join(','), 'appended video order matches multi-file import order');
    await appendAsset(audioAsset, 'A1', [0, 1.5]);
    await appendAsset(audioAsset, 'A1', [0, 1.5]);
    check(clipItems('A1').length === 2 && clipItems('A1').every(item => item.asset_id === audioAsset.id),
      'synthetic song appended twice to A1 for an audible before/after volume comparison');

    const snap = document.getElementById('snap');
    if (snapping) snap.click();
    const trimTarget = clipItems('V1')[1];
    const originalTrimDuration = trimTarget.duration_frames;
    selectTimelineClip(trimTarget.item_ref);
    const trimRevision = timeline.revision;
    document.getElementById('selected-out').value = String((trimTarget.out_frame + 1 - 5) / fps());
    document.getElementById('apply-timing').click();
    await until(() => timeline.revision !== trimRevision && clipByAsset(trimTarget.asset_id).item.duration_frames !== originalTrimDuration,
      'trim edit saved');
    const trimmedDuration = clipByAsset(trimTarget.asset_id).item.duration_frames;
    check(trimmedDuration === originalTrimDuration - 5, 'Apply timing trims the selected clip by five frames');
    await clickRevision('undo', 'undo restores original trim');
    check(clipByAsset(trimTarget.asset_id).item.duration_frames === originalTrimDuration, 'Undo restores the clip duration');
    await clickRevision('redo', 'redo reapplies trim');
    check(clipByAsset(trimTarget.asset_id).item.duration_frames === trimmedDuration, 'Redo reapplies the clip duration');
    await clickRevision('undo', 'undo restores sequence before move test');

    const moveTarget = clipItems('V1')[2];
    const originalStart = moveTarget.start_frame;
    selectTimelineClip(moveTarget.item_ref);
    const moveRevision = timeline.revision;
    document.getElementById('selected-start').value = String((originalStart + 24) / fps());
    document.getElementById('apply-timing').click();
    await until(() => timeline.revision !== moveRevision && clipByAsset(moveTarget.asset_id).item.start_frame !== originalStart,
      'move edit saved');
    const movedStart = clipByAsset(moveTarget.asset_id).item.start_frame;
    check(movedStart === originalStart + 24, 'Apply timing moves the selected clip by one second');
    await clickRevision('undo', 'undo restores original clip position');
    check(clipByAsset(moveTarget.asset_id).item.start_frame === originalStart, 'Undo restores the clip position');
    await clickRevision('redo', 'redo reapplies clip move');
    check(clipByAsset(moveTarget.asset_id).item.start_frame === movedStart, 'Redo reapplies the clip position');
    await clickRevision('undo', 'undo restores contiguous video sequence');
    check(clipItems('V1').every((item, index, items) => index === 0 || item.start_frame === items[index - 1].start_frame + items[index - 1].duration_frames),
      'video clips return to contiguous positions');

    const baselineRevision = timeline.revision;
    const baselineClips = clipItems('V1').map(clipEvidence);
    const baselineSongClips = clipItems('A1').map(clipEvidence);
    setMode('program');
    const baselinePreview = await renderPreview(baselineRevision, 'unmodified baseline preview');
    report.baseline = {revision: baselineRevision, previewFile: baselinePreview.file,
      timelineFrames: baselinePreview.frames, clips: baselineClips, songClips: baselineSongClips};
    check(true, 'baseline sequence renders before effects are applied');

    const videoClip = clipItems('V1')[2];
    selectTimelineClip(videoClip.item_ref);
    await editControl('effect-x', 40);
    await editControl('effect-y', 40);
    await editControl('effect-scale', 60);
    await editControl('effect-volume', -3);
    await clickRevision('apply-effects', 'position, scale and volume effects saved');
    const transformed = clipByAsset(videoClip.asset_id).item;
    check(transformed.filters?.some(filter => filter.properties?.['shotcut:mcpTransform'] === '1'), 'position and scale filter saved on video');
    check(transformed.filters?.some(filter => filter.properties?.['shotcut:mcpVolume'] === '1'), 'volume filter saved on video');

    const colorServices = await call('list_mlt_services', {kind: 'filter'});
    const filterNames = colorServices.services || [];
    const colorReady = filterNames.includes('brightness') && filterNames.includes('frei0r.saturat0r');
    if (!colorReady) throw new Error('The installed MLT stack does not expose the brightness and saturation filters under test.');
    const colorClip = clipItems('V1')[0];
    selectTimelineClip(colorClip.item_ref);
    await editControl('color-brightness', 130);
    await editControl('color-saturation', 160);
    await clickRevision('apply-color', 'brightness and saturation effects saved');
    const colored = clipByAsset(colorClip.asset_id).item;
    check(colored.filters?.some(filter => filter.properties?.['film:brightness'] === '1'), 'brightness filter saved');
    check(colored.filters?.some(filter => filter.properties?.['film:saturation'] === '1'), 'saturation filter saved');

    await until(() => document.getElementById('film-advanced-open'), 'advanced timeline tools loaded');
    const advancedOpen = document.getElementById('film-advanced-open');
    const firstClip = clipItems('V1')[0];
    selectTimelineClip(firstClip.item_ref);
    advancedOpen.click();
    await until(() => document.getElementById('film-advanced-dialog')?.open, 'advanced timeline dialog opened');
    const transitions = await call('list_mlt_services', {kind: 'transition'});
    if ((transitions.services || []).includes('luma')) {
      document.getElementById('film-advanced-transition-duration').value = '8';
      const addDissolve = [...document.querySelectorAll('#film-advanced-dialog button')]
        .find(button => button.textContent.includes('Add cross dissolve after selected clip'));
      if (!addDissolve) throw new Error('Cross dissolve control missing.');
      const before = timeline.revision;
      addDissolve.click();
      await until(() => timeline.revision !== before && allItems('V1', 'transition').length > 0, 'cross dissolve saved');
      check(true, 'advanced UI adds an MLT cross dissolve');
    } else {
      report.skipped.push('Cross dissolve UI: installed MLT luma transition service is unavailable.');
    }
    document.getElementById('film-advanced-title-text').value = 'Synthetic multi-clip title';
    document.getElementById('film-advanced-title-duration').value = '1';
    const titleButton = [...document.querySelectorAll('#film-advanced-dialog button')]
      .find(button => button.textContent.includes('Create title on a new video track'));
    if (!titleButton) throw new Error('Title generator control missing.');
    const titleRevision = timeline.revision;
    titleButton.click();
    await until(() => timeline.revision !== titleRevision && clipItems('Titles').length === 1, 'generated title saved');
    check(true, 'advanced UI creates a title clip on a new video track');
    document.getElementById('film-advanced-dialog').querySelector('.film-advanced-close').click();

    const adjustedAudioClip = clipItems('A1')[1];
    selectTimelineClip(adjustedAudioClip.item_ref);
    await editControl('effect-volume', -6);
    await clickRevision('apply-effects', 'song volume effect saved');
    check(clipItems('A1').some(item => item.start_frame === adjustedAudioClip.start_frame &&
      item.filters?.some(filter => filter.properties?.['shotcut:mcpVolume'] === '1')),
      'minus-six-decibel volume effect saved on the second song clip');

    const previewRevision = timeline.revision;
    const finalPreview = await renderPreview(previewRevision, 'final sequence preview');
    await until(() => video.readyState >= 2 && video.videoWidth === 640, 'rendered preview decoded in WKWebView');
    video.currentTime = 0.25;
    await video.play();
    await until(() => video.currentTime > 0.5 && !video.paused, 'rendered preview playback advances', 15000);
    video.pause();
    const seekTo = Math.max(1, frameCount() / fps() * 0.78);
    video.currentTime = seekTo;
    await until(() => !video.seeking && Math.abs(video.currentTime - seekTo) < 0.25, 'rendered preview seeks to later clips');
    report.preview = {width: video.videoWidth, height: video.videoHeight,
      duration: video.duration, timelineFrames: finalPreview.frames, previewFile: finalPreview.file};
    report.clips = clipItems('V1').map(clipEvidence);
    report.songClips = clipItems('A1').map(clipEvidence);
    report.videoSources = sourceInfoReport;
    report.audioClipCount = clipItems('A1').length;
    report.skipped.push('SRT caption import, keyframe animation, and other advanced controls were outside this bounded multi-clip pass.');
    check(!report.errors.length, 'no uncaught browser JavaScript errors');
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.message;
    report.stack = error.stack;
    report.errors.push(error.message);
    report.video = {time: video.currentTime, paused: video.paused, duration: video.duration,
      readyState: video.readyState, networkState: video.networkState,
      error: video.error?.message || null};
  }
  window.webkit.messageHandlers.film.postMessage({id: 'smoke', action: 'smokeComplete', body: report});
})();
