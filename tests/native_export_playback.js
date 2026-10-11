/* Opt-in smoke test for export playback after an audio source has been selected. */
(async () => {
  const report = {checks: {}, errors: []};
  const video = document.getElementById('video');
  const captureError = event => {
    const detail = event?.error?.message || event?.reason?.message || event?.message || event?.reason;
    report.errors.push(String(detail || 'Unspecified browser error'));
  };
  window.addEventListener('error', captureError);
  window.addEventListener('unhandledrejection', captureError);
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function until(predicate, label, timeout = 30000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (await predicate()) { report.checks[label] = true; return; }
      await wait(100);
    }
    throw new Error(label + ' timed out');
  }
  function check(value, label) {
    if (!value) throw new Error(label);
    report.checks[label] = true;
  }

  let originalApi;
  let renderRequests = 0;
  let frameRequests = 0;
  try {
    await until(() => timeline?.revision && state.assets?.length && !refreshing,
      'native project loaded');
    const audioCandidates = state.assets.filter(asset => asset.kind === 'audio' ||
      /\.(mp3|m4a|aac|wav|aiff?|flac|ogg)$/i.test(asset.name));
    const configuredAudio = audioCandidates.find(asset =>
      asset.id === window.nativeExportPlaybackConfig?.audioAssetId);
    const cachedShortAudio = audioCandidates.map(asset => ({asset, duration: Number(media.get(asset.id)?.duration)}))
      .filter(item => Number.isFinite(item.duration) && item.duration > 0 && item.duration <= 15)
      .sort((a, b) => a.duration - b.duration)[0]?.asset;
    const namedShortAudio = audioCandidates.find(asset =>
      /generated|extract|4\s?sec|4\s?second|smoke|fixture/i.test(asset.name));
    const audio = configuredAudio || cachedShortAudio || namedShortAudio ||
      [...audioCandidates].sort((a, b) => Number(a.bytes || Infinity) - Number(b.bytes || Infinity))[0];
    await loadRenders();
    const finalJobs = renderJobs.filter(job => job.status === 'completed' &&
      job.project_revision === timeline.revision && job.url &&
      /(?:^|[\\/])exports[\\/]/i.test(job.output_path || '') &&
      /\.(mp4|mov|mkv|webm)$/i.test(job.output_path || ''));
    const finished = [...finalJobs].sort((a, b) =>
      Number(b.completed_at || b.created_at || 0) - Number(a.completed_at || a.created_at || 0))[0];
    check(Boolean(audio), 'audio source available');
    check(Boolean(finished?.url), 'latest completed current-revision video export available');

    if (state.codex_token_usage) {
      const snapshot = await api('/api/events?after=0');
      check(JSON.stringify(snapshot.token_usage) === JSON.stringify(state.codex_token_usage),
        'reopened token snapshot matches persisted project usage');
      const usage = snapshot.token_usage;
      const counts = [usage?.total?.totalTokens, usage?.last?.totalTokens,
        usage?.total?.inputTokens, usage?.total?.cachedInputTokens, usage?.total?.cacheWriteInputTokens,
        usage?.total?.outputTokens, usage?.total?.reasoningOutputTokens].filter(Number.isSafeInteger);
      check(counts.length > 0, 'reopened token snapshot contains reported usage counts');
      await until(() => counts.some(count => document.getElementById('token-meter').textContent
        .includes(new Intl.NumberFormat().format(count))),
      'normal token poll displays persisted usage');
      report.tokenUsage = {
        threadTotal: usage?.total?.totalTokens ?? null,
        latestModelUsage: usage?.last?.totalTokens ?? null,
        contextWindow: usage?.modelContextWindow ?? null,
        totalInput: usage?.total?.inputTokens ?? null,
        totalOutput: usage?.total?.outputTokens ?? null,
        latestInput: usage?.last?.inputTokens ?? null,
        latestOutput: usage?.last?.outputTokens ?? null,
      };
    }

    await selectAsset(audio.id);
    await until(() => sourceInfo?.kind === 'audio' && video.readyState >= 2,
      'audio source loaded');
    check(document.getElementById('monitor-badge').textContent === 'AUDIO',
      'selected source is identified as audio');

    const revisionBefore = timeline.revision;
    const framesBefore = frameCount();
    const playButton = [...document.querySelectorAll('[data-job-play]')].find(button =>
      button.closest('.job')?.dataset.jobId === finished.job_id);
    check(Boolean(playButton?.dataset.url), 'finished export has a playback control');
    const selectedUrl = playButton.dataset.url;
    originalApi = api;
    api = async (route, body) => {
      if (route === '/api/editor/render') renderRequests++;
      if (route === '/api/editor/frame') frameRequests++;
      return originalApi(route, body);
    };
    playButton.click();
    await until(() => mode === 'output' && video.readyState >= 2 &&
      video.currentTime > 0.15 && !video.paused, 'export playback advances');
    check(outputInfo?.url === selectedUrl && video.getAttribute('src') === selectedUrl,
      'export player uses the selected exact URL');
    check(document.getElementById('monitor-badge').textContent === 'EXPORT',
      'export playback has its own badge');
    check(document.getElementById('source-controls').hidden &&
      document.getElementById('mark-in').disabled && document.getElementById('mark-out').disabled,
      'source controls are disabled during export playback');
    await loadRenders();
    await refresh(true);
    check(mode === 'output' && outputInfo?.url === selectedUrl &&
      video.getAttribute('src') === selectedUrl &&
      document.getElementById('monitor-badge').textContent === 'EXPORT',
      'render polling and project refresh preserve export playback');

    const expectedFrames = Math.ceil(video.duration * fps());
    check(Number.isFinite(video.duration) && video.duration > 0,
      'export exposes its decoded duration');
    check(document.getElementById('monitor-duration').textContent === tc(expectedFrames) &&
      Number(document.getElementById('monitor-scrub').max) === Math.max(0, expectedFrames - 1),
      'transport duration and scrub range use the export duration');

    document.getElementById('play').click();
    await until(() => video.paused, 'export playback pauses');
    const pausedAt = video.currentTime;
    document.getElementById('play').click();
    await until(() => !video.paused && video.currentTime > pausedAt + 0.1,
      'export playback resumes');
    video.pause();

    const scrubFrames = Math.max(0, Math.min(expectedFrames - 1,
      Math.round(Math.min(video.duration * 0.55, Math.max(0.35, video.duration * 0.4)) * fps())));
    const scrubTime = scrubFrames / fps();
    const scrub = document.getElementById('monitor-scrub');
    scrub.value = String(scrubFrames);
    scrub.dispatchEvent(new Event('input', {bubbles: true}));
    await until(() => !video.seeking && Math.abs(video.currentTime - scrubTime) < 0.2,
      'export playback scrubs');
    check(timeline.revision === revisionBefore && frameCount() === framesBefore,
      'export scrub leaves the timeline unchanged');
    check(renderRequests === 0 && frameRequests === 0,
      'export playback starts no additional render or sequence frame');
    report.export = {jobId: finished.job_id, path: finished.output_path,
      duration: video.duration, currentTime: video.currentTime};
    document.getElementById('program-tab').click();
    check(mode === 'program', 'program tab restores program mode');
  } catch (error) {
    report.passed = false;
    report.error = error.message;
    report.stack = error.stack;
    report.video = {time: video.currentTime, paused: video.paused, duration: video.duration,
      readyState: video.readyState, networkState: video.networkState,
      error: video.error?.message || null};
  } finally {
    if (originalApi) api = originalApi;
    window.removeEventListener('error', captureError);
    window.removeEventListener('unhandledrejection', captureError);
  }
  report.passed = !report.error && report.errors.length === 0;
  const safeReport = JSON.parse(JSON.stringify(report));
  window.webkit.messageHandlers.film.postMessage({id: 'smoke', action: 'smokeComplete', body: safeReport});
})();
