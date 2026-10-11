/* Opt-in installed WKWebView workflow. Config is supplied by its Python runner.
   Native upload IPC is exercised; Photos and file-panel provenance are separate. */
function nativeMusicInputFile(source) {
  const parts = source.embedded_chunks.map(chunk => {
    const binary = atob(chunk), bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    return bytes;
  });
  const file = new File(parts, source.name);
  // Release encoded chunks after reconstruction; only one File is imported at
  // a time through the app's existing 256 KiB native IPC upload implementation.
  source.embedded_chunks = [];
  return file;
}
(async () => {
  const cfg = nativeMusicConfig;
  const report = {passed: false, mode: cfg.mode, checks: {}, errors: [], media_errors: [], phases: []};
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const deadline = Date.now() + cfg.timeout_ms;
  const check = (value, label) => { if (!value) throw new Error(label); report.checks[label] = true; };
  const phase = name => report.phases.push({name, at: Date.now()});
  const $m = id => document.getElementById(id);
  window.addEventListener('error', e => report.errors.push(String(e.message)));
  window.addEventListener('unhandledrejection', e => report.errors.push(String(e.reason)));
  async function until(test, label, bound=60000) {
    const end = Math.min(deadline, Date.now() + bound);
    while (Date.now() < end) {
      if (await test()) { report.checks[label] = true; return; }
      await pause(250);
    }
    throw new Error(label + ' timed out: ' + ($m('notice')?.textContent || ''));
  }
  function videoClips(snapshot) {
    return snapshot.tracks.filter(track => track.type === 'video' || track.kind === 'video' || /^V\d+/.test(track.name))
      .flatMap(track => track.items.filter(item => item.type === 'clip').map(item => ({...item, track: track.name})))
      .sort((a, b) => a.start_frame - b.start_frame);
  }
  function jobRecord(job) {
    return Object.fromEntries(['job_id', 'status', 'output_path', 'project_revision', 'source_project_path', 'render_project_path']
      .filter(key => job[key] !== undefined).map(key => [key, job[key]]));
  }
  async function nativePlayback(job, label, clips, rate) {
    phase('native_' + label + '_playback');
    const v = $m('video');
    const errorsBefore = report.media_errors.length;
    setMode('program');
    const url = job.url || (await api('/api/media-url', {path: job.output_path})).url;
    show(url);
    await until(() => v.readyState >= 2 && v.videoWidth > 0 && v.currentSrc === url,
                label + ' native decoder ready on exact completed job');
    const samples = [];
    const times = [...new Set(clips.map(item => (item.start_frame + Math.min(item.duration_frames / 2, 15)) / rate))];
    for (const at of times.slice(0, 18)) {
      check(at >= 0 && at < v.duration, label + ' contains requested timeline sample ' + at.toFixed(3));
      v.pause(); v.currentTime = at;
      await until(() => !v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - at) < .12,
                  label + ' native seek ' + at.toFixed(3), 20000);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const canvas = document.createElement('canvas'); canvas.width = 96; canvas.height = 54;
      const context = canvas.getContext('2d'); context.drawImage(v, 0, 0, 96, 54);
      const pixels = context.getImageData(0, 0, 96, 54).data;
      let sum = 0, minimum = 255, maximum = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        const luma = .2126 * pixels[i] + .7152 * pixels[i + 1] + .0722 * pixels[i + 2];
        sum += luma; minimum = Math.min(minimum, luma); maximum = Math.max(maximum, luma);
      }
      samples.push({at_seconds: at, mean_luma: sum / (pixels.length / 4), luma_range: maximum - minimum});
    }
    check(samples.length > 0 && samples.every(s => s.mean_luma > 2 && s.luma_range > 2), label + ' sampled pictures are visible');
    v.currentTime = Math.min(.1, Math.max(0, v.duration - .5));
    const before = v.currentTime;
    await v.play();
    await until(() => !v.paused && v.currentTime > before + .25, label + ' native transport advances', 15000);
    v.pause();
    check(!v.error && report.media_errors.length === errorsBefore, label + ' has no native media error');
    return {duration_seconds: v.duration, width: v.videoWidth, height: v.videoHeight,
            before_seconds: before, after_seconds: v.currentTime, sampled_pictures: samples};
  }
  try {
    check(location.protocol === 'filmapp:' && !!window.FilmDesktop, 'installed native transport');
    if (cfg.mode === 'create') {
      phase('native_home_form');
      await until(() => !$m('project-picker').hidden, 'native home loaded');
      $m('quickstart-dialog')?.close();
      let created = null;
      // Defer navigation for this creation-only pass; the runner reopens its
      // returned ID. The actual form, backend and MLT initialization still run.
      openProject = async id => { created = await api('/api/library/open', {id}); created.id = id; };
      $m('create-project').click();
      const form = $m('project-form');
      form.elements.name.value = cfg.project_name;
      form.elements.format.value = 'auto';
      form.elements.format.dispatchEvent(new Event('change', {bubbles: true}));
      form.querySelector('button[type=submit]').click();
      await until(() => !!created?.id, 'native project created', 60000);
      check(created.url.startsWith('filmapp://app/?project=' + created.id), 'native project URL');
      report.project_id = created.id;
      report.passed = report.errors.length === 0;
    } else {
      await until(() => !!timeline?.revision && !refreshing, 'native project loaded');
      report.initial_project_path = timeline.path;
      $m('quickstart-dialog')?.close();
      $m('video').addEventListener('error', () => report.media_errors.push({code: $m('video').error?.code,
        message: $m('video').error?.message, current_time: $m('video').currentTime,
        source_path: new URL($m('video').currentSrc || location.href).searchParams.get('path')}));
      let beforeCreative = 0;
      if (cfg.mode === 'workflow') {
        check(videoClips(timeline).length === 0 && state.assets.length === 0, 'disposable project starts empty');
        check(!localStorage.getItem('native-music-submitted-' + cfg.run_id), 'creative run has not previously been submitted');
        phase('account_models');
        $m('connect-codex').click();
        await until(() => models.length > 0, 'account model list loaded', 120000);
        const selected = cfg.model || $m('model').value;
        if (selected) check(models.some(m => (m.model || m.id) === selected), 'selected model is available to account');
        $m('model').value = selected; updateEffort();
        if (cfg.effort) {
          check([...$m('effort').options].some(option => option.value === cfg.effort), 'selected effort is supported');
          $m('effort').value = cfg.effort;
        }
        report.model = selected || 'Account default'; report.effort = $m('effort').value || 'Default';
        const initialEvents = await api('/api/events?after=0');
        check(initialEvents.permission_mode === 'full-access', 'new project retains full access');
        const beforeImport = Math.max(0, ...initialEvents.events.map(event => event.id));
        phase('native_chunk_upload_import'); report.source_imports = [];
        const ids = [];
        for (const source of cfg.sources) {
          const file = nativeMusicInputFile(source);
          check(file.size === source.bytes, 'embedded source byte count ' + source.name);
          const assets = await window.FilmDesktop.uploadFiles([file], source.kind);
          check(assets.length === 1, 'native import returned one asset ' + source.name);
          ids.push(assets[0].id);
          report.source_imports.push({name: source.name, asset_id: assets[0].id, path: assets[0].path,
                                     source_sha256: source.sha256, bytes: source.bytes, kind: source.kind});
        }
        phase('automatic_import_setup');
        await finishImport(ids);
        await until(async () => {
          const events = await api('/api/events?after=' + beforeImport);
          if (events.approvals?.length) throw new Error('Automatic media setup requested input; retained evidence identifies the missing detail');
          return !events.busy;
        }, 'automatic import analysis finished', cfg.timeout_ms);
        await refresh(true); await pause(1800);
        const setup = await api('/api/events?after=' + beforeImport);
        check(!setup.busy, 'automatic setup is idle before creative submission');
        report.setup_events = setup.events;
        report.setup_user_event_ids = setup.events.filter(e => e.kind === 'user').map(e => e.id);
        check(videoClips(timeline).length === 0, 'technical import setup did not author a creative timeline');
        beforeCreative = Math.max(beforeImport, ...setup.events.map(event => event.id));
        report.before_creative_event_id = beforeCreative;
        phase('actual_ai_edit_dialog');
        $m('ai-edit-open').click();
        check($m('ai-edit-dialog').open, 'actual AI edit dialog opened');
        $m('ai-edit-brief').value = cfg.brief;
        // Conservative sentinel: a interrupted launch can only observe, never
        // retry the creative click automatically.
        localStorage.setItem('native-music-submitted-' + cfg.run_id, String(Date.now()));
        report.creative_button_clicked_at = Date.now();
        $m('ai-edit-start').click();
        await until(async () => {
          const events = await api('/api/events?after=' + beforeCreative);
          return events.events.some(e => e.kind === 'user' && e.data.text?.startsWith('Create and render my film. Creative brief:'));
        }, 'creative turn accepted from actual AI button', 120000);
        phase('live_creative_turn');
      } else {
        check(cfg.mode === 'observe', 'resume is observe-only');
        phase('observe_only_resume');
      }
      await until(async () => {
        const events = await api('/api/events?after=' + beforeCreative);
        if (events.approvals?.length) throw new Error('Creative turn requested input or approval; no response was manufactured');
        const finished = events.events.filter(e => e.kind === 'finished').at(-1);
        return !events.busy && !!finished;
      }, 'creative turn finished', cfg.timeout_ms);
      await refresh(true); await loadRenders();
      const allEvents = await api('/api/events?after=0');
      const finished = allEvents.events.filter(e => e.kind === 'finished').at(-1);
      check(finished?.data.status === 'completed', 'creative turn completed successfully');
      check(!allEvents.events.some(e => e.kind === 'approval'), 'no approval events');
      report.final_messages = allEvents.events.filter(e => e.kind === 'message').map(e => ({id: e.id, text: e.data.text}));
      report.tool_calls = allEvents.events.filter(e => e.kind === 'activity' && e.data.tool)
        .map(e => ({tool: e.data.tool, status: e.data.status, item_id: e.data.item_id}));
      report.failed_mcp_tools = report.tool_calls.filter(e => e.status === 'failed');
      const snapshot = await api('/api/editor');
      check(snapshot.path === report.initial_project_path, 'Codex edited the same active sequence');
      report.final_snapshot = snapshot; report.final_revision = snapshot.revision;
      const allClips = videoClips(snapshot), rate = Number(snapshot.profile.frame_rate_num) / Number(snapshot.profile.frame_rate_den);
      const duration = snapshot.duration_frames / rate;
      const sourceIDs = new Set(cfg.sources.filter(s => s.kind === 'footage').map(s => s.name));
      const knownMovies = state.assets.filter(asset => sourceIDs.has(asset.name));
      const moviePaths = new Set(knownMovies.map(asset => asset.path));
      const movieIDs = new Set(knownMovies.map(asset => asset.id));
      const sourceClips = allClips.filter(item => movieIDs.has(item.asset_id) || moviePaths.has(item.resource));
      const tracks = [...new Set(sourceClips.map(item => item.track))];
      const primary = tracks.sort((a, b) => sourceClips.filter(i => i.track === b).reduce((n, i) => n + i.duration_frames, 0) -
        sourceClips.filter(i => i.track === a).reduce((n, i) => n + i.duration_frames, 0))[0];
      const clips = allClips.filter(item => item.track === primary);
      const used = sourceClips.filter(item => item.track === primary);
      const distinct = new Set(used.map(item => item.asset_id || item.resource));
      const cuts = used.slice(1).filter((item, index) => {
        const previous = used[index];
        return previous.start_frame + previous.duration_frames === item.start_frame &&
          (previous.resource !== item.resource || previous.out_frame + 1 !== item.in_frame);
      });
      report.music_edit = {duration_seconds: duration, picture_segments: clips.length,
                          primary_picture_track: primary, picture_cut_count: cuts.length,
                          cut_evidence: 'candidate contiguous source-shot cuts; visible musical alignment requires independent review',
                          picture_cut_seconds: cuts.map(item => item.start_frame / rate),
                          distinct_imported_movies_used: distinct.size,
                          cuts: clips.map(item => ({track: item.track, asset_id: item.asset_id, resource: item.resource,
                            start_frame: item.start_frame, in_frame: item.in_frame, out_frame: item.out_frame,
                            duration_frames: item.duration_frames, cut_seconds: item.start_frame / rate}))};
      check(Number(snapshot.profile.width) === 720 && Number(snapshot.profile.height) === 1280 && rate === 30,
            'AI authored requested 720×1280 portrait at 30fps');
      check(duration >= 24 - 1 / rate && duration <= 30 + 1 / rate, 'AI authored bounded duration');
      check(distinct.size >= 3, 'AI used at least three actual imported movies');
      check(report.music_edit.picture_cut_count >= 5, 'AI authored at least five picture cuts');
      const jobs = (await api('/api/renders')).jobs;
      report.render_jobs = jobs.map(jobRecord);
      const finalPath = state.workspace + '/' + cfg.final_relative;
      const exports = jobs.filter(job => job.status === 'completed' && job.output_path === finalPath && job.project_revision === snapshot.revision);
      check(exports.length === 1, 'exact final H.264 job completed at saved revision');
      const previews = jobs.filter(job => job.status === 'completed' && job.output_path !== finalPath && /\.(mp4|mov)$/i.test(job.output_path || ''));
      check(previews.length > 0, 'AI completed a separate video preview');
      const preview = previews.find(job => job.project_revision === snapshot.revision) || previews[0];
      report.preview = jobRecord(preview); report.export = jobRecord(exports[0]);
      report.preview_playback = await nativePlayback(preview, 'preview', clips, rate);
      report.export_playback = await nativePlayback(exports[0], 'export', clips, rate);
      check(report.errors.length === 0, 'no uncaught JavaScript errors');
      check(report.media_errors.length === 0, 'no native playback errors');
      phase('native_review_complete'); report.passed = true;
    }
  } catch (error) {
    report.error = String(error.stack || error);
    report.phase_at_failure = report.phases.at(-1)?.name;
    try {
      const events = await api('/api/events?after=0');
      report.last_events = events.events;
      report.pending_approvals = events.approvals;
      report.codex_busy_at_failure = events.busy;
      report.last_notice = $m('notice')?.textContent;
      report.final_snapshot = await api('/api/editor');
      report.render_jobs = (await api('/api/renders')).jobs.map(jobRecord);
    } catch (captureError) { report.capture_error = String(captureError); }
  }
  function safe(value) {
    if (Array.isArray(value)) return value.map(safe);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !['url', 'token', 'accessToken', 'refreshToken', 'headers', 'authorization'].includes(key))
      .map(([key, item]) => [key, safe(item)]));
    if (typeof value === 'string') return value.replace(/([?&](?:sig|token)=)[^&\s]+/gi, '$1[redacted]')
      .replace(/data:[^\s"']+/g, '[media data omitted]');
    return value;
  }
  window.webkit.messageHandlers.film.postMessage({id: 'smoke', action: 'smokeComplete', body: safe(report)});
})();
