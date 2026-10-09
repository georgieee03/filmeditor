'use strict';

(() => {
  if (window.filmEnhancePanelInstalled) return;
  window.filmEnhancePanelInstalled = true;
  if (typeof api !== 'function' || typeof call !== 'function') return;

  const button = (label, action, className = '') => {
    const node = el('button', label, className);
    node.type = 'button';
    node.addEventListener('click', async () => {
      if (node.disabled) return;
      node.disabled = true;
      try { await action(); }
      catch (error) { setMessage(errorText(error), true); }
      finally { if (node.isConnected) { node.disabled = false; if (node === run) updateContext(); } }
    });
    return node;
  };
  const field = (labelText, control) => {
    const node = el('label', undefined, 'film-enhance-field');
    node.append(el('span', labelText), control);
    return node;
  };
  const option = (label, value) => {
    const node = el('option', label);
    node.value = value;
    return node;
  };
  const dialog = el('dialog');
  dialog.id = 'film-enhance-dialog';
  dialog.className = 'film-enhance-dialog';

  const heading = el('div', undefined, 'film-enhance-heading');
  const title = el('div');
  const titleHeading = el('h2', 'Improve a source clip');
  titleHeading.id = 'film-enhance-title';
  title.append(el('span', 'SOURCE ENHANCEMENT', 'eyebrow'), titleHeading);
  dialog.setAttribute('aria-labelledby', titleHeading.id);
  const close = el('button', '×', 'film-enhance-close');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close enhancement panel');
  close.addEventListener('click', () => dialog.close());
  heading.append(title, close);
  dialog.append(heading,
    el('p', 'Create a separate result from the original. Sources can be up to 5 minutes; video processing requires supported SDR 8-bit footage. Dialogue cleanup also works on audio-only sources. The original stays in the bin.', 'film-enhance-intro'));

  const sourceSection = el('section', undefined, 'film-enhance-section');
  sourceSection.append(el('h3', 'Source'));
  const sourceDescription = el('p', 'Choose a selected timeline clip or the selected media in the Project panel.', 'film-enhance-help');
  const sourceChoice = el('select');
  sourceChoice.id = 'film-enhance-source';
  sourceSection.append(sourceDescription, field('Use source', sourceChoice));
  const sourceName = el('p', 'No source selected.', 'film-enhance-source-name');
  const sourceButtons = el('div', undefined, 'film-enhance-actions');
  const previewSource = button('Preview first frame', async () => {
    const source = getSource();
    const info = await prepareMedia(source.id, false, 'metadata_only');
    if (info?.kind !== 'video') throw new Error('First-frame preview is available for video sources.');
    await selectAsset(source.id);
    const video = $('video');
    if (video.hidden) throw new Error('The source video could not be opened in the monitor.');
    video.pause();
    if (!await waitForVideo(video, 1, 3500)) throw new Error('The source video did not load its first frame.');
    const needsSeek = Math.abs(video.currentTime) > 0.001;
    const seeked = needsSeek ? waitForVideoEvent(video, 'seeked', 3500, () => Math.abs(video.currentTime) <= 0.001) : Promise.resolve(true);
    video.currentTime = 0;
    if (!await seeked) throw new Error('The source video did not seek to its first frame.');
    if (!await waitForVideo(video, 2, 3500)) throw new Error('The source video could not decode its first frame.');
    dialog.close();
    notice('The source first frame is ready. Reopen Enhance… to enter the target center percentages.');
  });
  sourceButtons.append(previewSource);
  sourceSection.append(sourceName, sourceButtons);
  dialog.append(sourceSection);

  const settings = el('section', undefined, 'film-enhance-section');
  settings.append(el('h3', 'Enhancement'));
  const operation = el('select');
  operation.id = 'film-enhance-operation';
  operation.append(option('Stabilize motion', 'stabilize'), option('Reframe for a new shape', 'reframe'), option('Clean up dialogue audio', 'audio_cleanup'));
  const aspect = el('select');
  aspect.id = 'film-enhance-aspect';
  aspect.append(option('Vertical · 9:16', '9:16'), option('Square · 1:1', '1:1'), option('Portrait · 4:5', '4:5'));
  const subject = el('select');
  subject.id = 'film-enhance-subject';
  subject.append(option('Automatic subject follow', 'auto'), option('Manual target center', 'manual'));
  const strength = el('select');
  strength.id = 'film-enhance-strength';
  strength.append(option('Gentle', 'gentle'), option('Standard', 'standard'), option('Strong', 'strong'));
  strength.value = 'standard';
  settings.append(field('Operation', operation), field('Strength', strength), field('Output shape', aspect), field('Subject tracking', subject));

  const manual = el('div', undefined, 'film-enhance-manual');
  manual.append(el('p', 'Preview the first source frame, then place the tracking target center on the subject. The engine follows that region through the clip. Coordinates are percentages from the upper-left corner; keep the center around 12%–88% so the 24% target area has room near the edges.', 'film-enhance-help'));
  const centerX = el('input');
  centerX.type = 'number'; centerX.min = '0'; centerX.max = '100'; centerX.step = '1'; centerX.value = '50'; centerX.inputMode = 'decimal';
  centerX.id = 'film-enhance-center-x';
  const centerY = el('input');
  centerY.type = 'number'; centerY.min = '0'; centerY.max = '100'; centerY.step = '1'; centerY.value = '50'; centerY.inputMode = 'decimal';
  centerY.id = 'film-enhance-center-y';
  manual.append(field('Target center X (%)', centerX), field('Target center Y (%)', centerY));
  settings.append(manual);

  const run = button('Create enhanced copy', startJob, 'primary');
  run.id = 'film-enhance-start';
  settings.append(el('p', 'Stabilization and reframing make a new video. Audio cleanup makes a cleaned audio copy and keeps the picture stream when the source has video.', 'film-enhance-help'), run);
  dialog.append(settings);

  const statusSection = el('section', undefined, 'film-enhance-section');
  const statusHeading = el('div', undefined, 'film-enhance-section-heading');
  statusHeading.append(el('h3', 'Enhancement jobs'));
  const reload = button('Refresh', loadJobs, 'film-enhance-small-button');
  statusHeading.append(reload);
  statusSection.append(statusHeading);
  const message = el('p', '', 'film-enhance-message');
  message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');
  const jobList = el('div', undefined, 'film-enhance-jobs');
  jobList.append(el('p', 'No enhancement jobs yet.', 'film-enhance-help film-enhance-empty'));
  statusSection.append(message, jobList);
  dialog.append(statusSection);

  document.body.append(dialog);
  const anchor = document.querySelector('#inspector .panel-title');
  const open = el('button', 'Enhance…', 'film-enhance-open');
  open.id = 'film-enhance-open';
  open.type = 'button';
  open.title = 'Stabilize, reframe, or clean up audio on a source clip';
  const decorativeMark = anchor?.querySelector(':scope > span');
  if (decorativeMark) decorativeMark.hidden = true;
  anchor?.append(open);

  const jobs = new Map();
  const rowNodes = new Map();
  let pollTimer = null;
  let pollBusy = false;
  let startBusy = false;

  operation.addEventListener('change', updateControls);
  subject.addEventListener('change', updateControls);
  sourceChoice.addEventListener('change', updateContext);
  centerX.addEventListener('input', () => { validateManual(); updateContext(); });
  centerY.addEventListener('input', () => { validateManual(); updateContext(); });
  open.addEventListener('click', () => {
    updateContext();
    if (!dialog.open) dialog.showModal();
    loadJobs();
  });
  dialog.addEventListener('close', () => schedulePoll());
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && (dialog.open || hasActiveJobs())) loadJobs();
  });

  function setMessage(value, isError = false) {
    message.textContent = value;
    message.classList.toggle('error', isError);
  }
  function getClip() {
    try { return clip(); } catch { return null; }
  }
  function sourceChoices() {
    const choices = [];
    const current = getClip();
    if (current?.item?.type === 'clip' && current.item.asset_id) {
      const assetRecord = state.assets.find(item => item.id === current.item.asset_id);
      if (assetRecord) choices.push({ mode: 'clip', id: current.item.asset_id, asset: assetRecord, item: current.item, fromClip: true });
    }
    const assetRecord = state.assets.find(item => item.id === selectedAsset);
    if (assetRecord && !choices.some(choice => choice.id === assetRecord.id)) choices.push({ mode: 'media', id: assetRecord.id, asset: assetRecord, item: null, fromClip: false });
    return choices;
  }
  function getSource() {
    const choices = sourceChoices();
    const selected = choices.find(choice => choice.mode === sourceChoice.value) || choices[0];
    if (!selected) throw new Error('Select a timeline clip or media in the Project panel first.');
    return selected;
  }
  function updateContext() {
    const choices = sourceChoices();
    const previous = sourceChoice.value;
    const signature = JSON.stringify(choices.map(choice => [choice.mode, choice.id, choice.asset.name]));
    if (sourceChoice.dataset.signature !== signature) {
      sourceChoice.replaceChildren(...choices.map(choice => option(choice.fromClip ? choice.asset.name + ' · timeline clip' : choice.asset.name + ' · Project media', choice.mode)));
      sourceChoice.dataset.signature = signature;
    }
    const source = choices.find(choice => choice.mode === previous) || choices[0];
    sourceChoice.value = source?.mode || '';
    sourceChoice.disabled = choices.length < 2;
    if (!source) {
      sourceName.textContent = 'No source selected. Select a timeline clip or media in the Project panel.';
      previewSource.disabled = true;
      run.disabled = true;
      return;
    }
    const label = source.asset?.name || source.item?.name || 'Selected source';
    sourceName.textContent = source.fromClip ? label + ' · selected timeline clip' : label + ' · selected Project media';
    for (const control of [operation, strength, aspect, subject, centerX, centerY]) control.disabled = startBusy;
    previewSource.disabled = startBusy || !source.asset || media.get(source.id)?.kind === 'audio';
    sourceChoice.disabled = startBusy || choices.length < 2;
    run.disabled = startBusy || !source.asset || (operation.value === 'reframe' && subject.value === 'manual' && !manualCenterIsValid());
  }
  function updateControls() {
    const isReframe = operation.value === 'reframe';
    aspect.closest('label').hidden = !isReframe;
    subject.closest('label').hidden = !isReframe;
    manual.hidden = !isReframe || subject.value !== 'manual';
    validateManual();
    updateContext();
  }
  function validateManual() {
    const validX = centerX.value.trim() !== '' && Number.isFinite(Number(centerX.value)) && Number(centerX.value) >= 0 && Number(centerX.value) <= 100;
    const validY = centerY.value.trim() !== '' && Number.isFinite(Number(centerY.value)) && Number(centerY.value) >= 0 && Number(centerY.value) <= 100;
    centerX.setCustomValidity(validX ? '' : 'Enter a percentage from 0 to 100.');
    centerY.setCustomValidity(validY ? '' : 'Enter a percentage from 0 to 100.');
    return validX && validY;
  }
  function manualCenterIsValid() { return [centerX, centerY].every(node => node.value.trim() !== '' && Number.isFinite(Number(node.value)) && Number(node.value) >= 0 && Number(node.value) <= 100); }
  function waitForVideo(video, readyState, timeoutMs) {
    if (video.readyState >= readyState) return Promise.resolve(true);
    return new Promise(resolve => {
      let settled = false;
      let timer = null;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        video.removeEventListener('loadedmetadata', check);
        video.removeEventListener('loadeddata', check);
        video.removeEventListener('error', failed);
        resolve(video.readyState >= readyState);
      };
      const check = () => { if (video.readyState >= readyState) finish(); };
      const failed = () => finish();
      timer = setTimeout(finish, timeoutMs);
      video.addEventListener('loadedmetadata', check);
      video.addEventListener('loadeddata', check);
      video.addEventListener('error', failed, { once: true });
    });
  }
  function waitForVideoEvent(video, eventName, timeoutMs, predicate = () => true) {
    if (predicate()) return Promise.resolve(true);
    return new Promise(resolve => {
      let settled = false;
      let timer = null;
      const finish = success => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        video.removeEventListener(eventName, check);
        video.removeEventListener('error', failed);
        resolve(success);
      };
      const check = () => { if (predicate()) finish(true); };
      const failed = () => finish(false);
      timer = setTimeout(() => finish(false), timeoutMs);
      video.addEventListener(eventName, check);
      video.addEventListener('error', failed, { once: true });
    });
  }
  function responseJobs(response) {
    if (Array.isArray(response?.jobs)) return response.jobs;
    if (response?.job && typeof response.job === 'object') return [response.job];
    if (response?.job_id || response?.id) return [response];
    return [];
  }
  function jobId(job) { return String(job?.job_id || job?.id || job?.task_id || ''); }
  function sourceIdFor(job, fallback = '') {
    return String(job?.source_asset_id || job?.original_asset_id || job?.input_asset_id || fallback || '');
  }
  function resultIdFor(job) {
    return String(job?.asset_id || job?.result_asset_id || job?.asset?.id || job?.result?.asset_id || job?.output?.asset_id || '');
  }
  function isComplete(job) { return ['completed', 'complete', 'succeeded', 'success', 'done'].includes(String(job?.status || '').toLowerCase()); }
  function isActive(job) { return ['queued', 'running', 'processing', 'starting', 'cancelling'].includes(String(job?.status || '').toLowerCase()); }
  function progressValue(job) {
    const raw = Number(job?.progress_percent ?? job?.progress ?? job?.percent ?? 0);
    return Math.max(0, Math.min(100, raw <= 1 && raw > 0 ? raw * 100 : raw));
  }
  function progressLabel(job) {
    const stage = job?.stage_label || job?.stage || job?.phase || job?.current_stage || '';
    const value = Math.round(progressValue(job));
    return [job?.status || 'Unknown', stage, (isActive(job) || isComplete(job)) ? value + '%' : ''].filter(Boolean).join(' · ');
  }
  function warningsFor(job) {
    const values = Array.isArray(job?.warnings) ? job.warnings : job?.warning ? [job.warning] : [];
    return values.map(value => typeof value === 'string' ? value : value?.message || JSON.stringify(value)).filter(Boolean);
  }
  function putJob(job, fallbackSource = '') {
    const id = jobId(job);
    if (!id) return;
    const previous = jobs.get(id) || {};
    jobs.set(id, { ...previous, ...job, job_id: id, source_asset_id: sourceIdFor(job, previous.source_asset_id || fallbackSource) });
  }
  function hasActiveJobs() { return [...jobs.values()].some(isActive); }
  async function engine(name, args = {}) {
    return unpack(await api('/api/call', { name, arguments: args }));
  }
  async function loadJobs() {
    if (pollBusy) return;
    pollBusy = true;
    try {
      const response = await engine('film_enhance_status', {});
      for (const job of responseJobs(response)) putJob(job);
      paintJobs();
      if (!jobs.size && !message.textContent) setMessage('No enhancement jobs yet.');
      else if (message.textContent.startsWith('Could not load enhancement jobs:')) setMessage('Enhancement jobs refreshed.');
    } catch (error) {
      setMessage('Could not load enhancement jobs: ' + errorText(error), true);
    } finally {
      pollBusy = false;
      schedulePoll();
    }
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (!dialog.open && !hasActiveJobs()) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (document.visibilityState === 'hidden') { schedulePoll(); return; }
      loadJobs();
    }, dialog.open ? 2500 : 5000);
  }
  async function startJob() {
    if (startBusy) return;
    const source = getSource();
    if (!source.asset) throw new Error('Refresh the Project panel and select the source again.');
    const request = {
      asset_id: source.id,
      operation: operation.value,
      aspect: aspect.value,
      subject: operation.value === 'reframe' ? subject.value : 'auto',
      center_x: operation.value === 'reframe' && subject.value === 'manual' ? Number(centerX.value) / 100 : 0.5,
      center_y: operation.value === 'reframe' && subject.value === 'manual' ? Number(centerY.value) / 100 : 0.5,
      strength: strength.value
    };
    const manualCenter = request.operation === 'reframe' && request.subject === 'manual';
    if (manualCenter && ![centerX, centerY].every(node => node.reportValidity())) return;
    startBusy = true;
    updateContext();
    setMessage('Checking source and starting enhancement…');
    try {
      const info = await prepareMedia(source.id, false, 'metadata_only');
      if (request.operation === 'audio_cleanup') {
        if (!['audio', 'video'].includes(info?.kind)) throw new Error('Dialogue cleanup needs an audio or video source.');
        if (info.has_audio === false) throw new Error('This source has no audio track to clean up.');
      } else if (info?.kind !== 'video') {
        throw new Error('Stabilization and reframing need a video source. This asset is classified as ' + (info?.kind || 'unknown') + '.');
      }
      const duration = Number(info.duration);
      if (!Number.isFinite(duration) || duration <= 0) throw new Error('The source duration could not be read.');
      if (duration > 300) throw new Error('This source is longer than the 5-minute enhancement limit.');
      setMessage('Starting enhancement…');
      const result = await engine('film_enhance_start', request);
      const created = responseJobs(result);
      if (!created.length) throw new Error('The enhancement service did not return a job.');
      for (const job of created) putJob(job, source.id);
      paintJobs();
      setMessage('Enhancement started. The original media is unchanged.');
      schedulePoll();
    } finally {
      startBusy = false;
      updateContext();
    }
  }
  function paintJobs() {
    const list = [...jobs.values()].sort((a, b) => String(b.created_at || b.started_at || b.job_id).localeCompare(String(a.created_at || a.started_at || a.job_id)));
    const visibleIds = new Set(list.map(jobId));
    for (const [id, row] of rowNodes) {
      if (!visibleIds.has(id)) { row.remove(); rowNodes.delete(id); }
    }
    if (!list.length) {
      if (!jobList.querySelector('.film-enhance-empty')) jobList.replaceChildren(el('p', 'No enhancement jobs yet.', 'film-enhance-help film-enhance-empty'));
      return;
    }
    jobList.querySelector('.film-enhance-empty')?.remove();
    for (const job of list) {
      const id = jobId(job);
      let row = rowNodes.get(id);
      if (!row) { row = makeJobRow(id); rowNodes.set(id, row); jobList.prepend(row); }
      updateJobRow(row, job);
    }
  }
  function makeJobRow(id) {
    const row = el('article', undefined, 'film-enhance-job');
    row.dataset.jobId = id;
    const heading = el('div', undefined, 'film-enhance-job-heading');
    const name = el('strong', 'Enhancement');
    const stateLabel = el('span', 'Queued', 'film-enhance-job-state');
    heading.append(name, stateLabel);
    const progress = el('progress');
    progress.max = 100;
    progress.value = 0;
    progress.setAttribute('aria-label', 'Enhancement progress');
    const detail = el('p', '', 'film-enhance-job-detail');
    const warning = el('p', '', 'film-enhance-warning');
    const formatWarning = el('p', 'Matching changes dimensions for every clip in a non-empty sequence. Frame numbers and frame rate stay the same; undo remains available.', 'film-enhance-warning film-enhance-format-warning');
    const actions = el('div', undefined, 'film-enhance-actions');
    const preview = button('Preview result', async () => {
      const job = jobs.get(id), resultId = resultIdFor(job);
      if (!resultId) throw new Error('This job has no completed result to preview.');
      await assetForJob(job);
      await selectAsset(resultId);
      dialog.close();
      notice('Previewing the enhanced copy. The timeline is unchanged. Reopen Enhance… to apply it.');
    });
    preview.className = 'film-enhance-small-button';
    const use = button('Use on selected clip', () => replaceSelectedClip(id));
    use.className = 'film-enhance-small-button primary';
    const match = button('Match sequence to result', () => matchSequence(id));
    match.className = 'film-enhance-small-button';
    const cancel = button('Cancel', () => cancelJob(id));
    cancel.className = 'film-enhance-small-button danger';
    actions.append(preview, use, match, cancel);
    row.append(heading, progress, detail, warning, formatWarning, actions);
    return row;
  }
  function updateJobRow(row, job) {
    const id = jobId(job);
    const sourceId = sourceIdFor(job);
    const assetId = resultIdFor(job);
    const source = state.assets.find(record => record.id === sourceId);
    const stateLabel = row.querySelector('.film-enhance-job-state');
    const name = row.querySelector('strong');
    const progress = row.querySelector('progress');
    const detail = row.querySelector('.film-enhance-job-detail');
    const warning = row.querySelector('.film-enhance-warning');
    const formatWarning = row.querySelector('.film-enhance-format-warning');
    const actions = row.querySelectorAll('button');
    const label = (job.operation || 'enhancement').replaceAll('_', ' ');
    const sourceNameText = source?.name || job.source_name || '';
    if (name.textContent !== label + (sourceNameText ? ' · ' + sourceNameText : '')) name.textContent = label + (sourceNameText ? ' · ' + sourceNameText : '');
    stateLabel.textContent = job.status || 'Unknown';
    stateLabel.className = 'film-enhance-job-state ' + (isComplete(job) ? 'complete' : isActive(job) ? 'active' : 'failed');
    const amount = progressValue(job);
    if (progress.value !== amount) progress.value = amount;
    const detailText = [progressLabel(job), job.error || job.error_message || ''].filter(Boolean).join(' · ');
    progress.setAttribute('aria-label', 'Enhancement progress for ' + label + (sourceNameText ? ' · ' + sourceNameText : ''));
    progress.setAttribute('aria-valuetext', detailText);
    if (detail.textContent !== detailText) detail.textContent = detailText;
    const warningText = [...warningsFor(job), ...(job.error && !detailText.includes(job.error) ? [job.error] : [])].join(' ');
    if (warning.textContent !== warningText) warning.textContent = warningText;
    warning.hidden = !warningText;
    const nonempty = (timeline?.tracks || []).some(track => (track.items || []).some(item => item.type !== 'gap'));
    formatWarning.hidden = !assetId || !isComplete(job) || !nonempty;
    const clipSelection = getClip();
    const canUse = !!assetId && isComplete(job) && !!clipSelection && clipSelection.item.type === 'clip' && clipSelection.item.asset_id === sourceId;
    const audioOnlyResult = job.operation === 'audio_cleanup' && job.asset?.enhancement?.video_stream_index === null;
    const canMatch = !!assetId && isComplete(job) && !!timeline && !audioOnlyResult;
    actions[0].hidden = !assetId || !isComplete(job);
    actions[0].disabled = !assetId || !isComplete(job);
    actions[1].hidden = !assetId || !isComplete(job);
    actions[1].disabled = !canUse;
    actions[1].title = canUse ? 'Replace the selected clip media and keep its timing' : 'Select a timeline clip that uses this job’s source asset';
    actions[2].hidden = !assetId || !isComplete(job) || audioOnlyResult;
    actions[2].disabled = !canMatch;
    actions[2].title = 'Change sequence dimensions while keeping frame numbers and frame rate';
    actions[3].hidden = !isActive(job);
    actions[3].disabled = !isActive(job);
    row.dataset.assetId = assetId;
  }
  async function cancelJob(id) {
    await engine('film_enhance_cancel', { job_id: id });
    setMessage('Cancellation requested.');
    await loadJobs();
  }
  function completedJob(id) {
    const job = jobs.get(id);
    if (!job || !isComplete(job)) throw new Error('Wait for the enhancement job to finish first.');
    if (!resultIdFor(job)) throw new Error('The completed job has no result asset.');
    return job;
  }
  async function assetForJob(job) {
    const id = resultIdFor(job);
    for (let attempt = 0; attempt < 4; attempt += 1) {
      await refresh(true);
      const record = state.assets.find(assetRecord => assetRecord.id === id);
      if (record?.path) return record;
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw new Error('The enhanced result is not available as a project asset yet. Refresh the job list and try again.');
  }
  async function replaceSelectedClip(id) {
    const job = completedJob(id);
    const originalId = sourceIdFor(job);
    if (!originalId) throw new Error('The job source could not be matched to a project asset.');
    const current = getClip();
    if (!current || current.item.type !== 'clip' || current.item.asset_id !== originalId) {
      throw new Error('Select a timeline clip that uses the exact source asset for this enhancement.');
    }
    const oldStart = current.item.start_frame;
    const oldDuration = current.item.duration_frames;
    const oldRef = current.item.item_ref;
    const pathRecord = await assetForJob(job);
    const latest = getClip();
    if (!latest || latest.item.item_ref !== oldRef || latest.item.asset_id !== originalId) {
      throw new Error('The selected clip changed while the result was loading. Select the source clip again and retry.');
    }
    const next = await edit([{ op: 'replace_item_media', item_ref: latest.item.item_ref, path: pathRecord.path }]);
    for (const track of next.tracks || []) {
      const replacement = track.items.find(item => item.type === 'clip' && item.asset_id === pathRecord.id && item.start_frame === oldStart && item.duration_frames === oldDuration);
      if (replacement) { selectedClip = replacement.item_ref; selectedTrack = track.track_id; drawTimeline(); break; }
    }
    setMessage('Enhanced media is now on the selected clip. Undo is available from the timeline.');
  }
  async function matchSequence(id) {
    const job = completedJob(id);
    const assetRecord = await assetForJob(job);
    if (!timeline) throw new Error('Create a sequence before matching its format.');
    const path = timeline.path, revisionAtStart = timeline.revision;
    const info = await prepareMedia(assetRecord.id, false, 'metadata_only');
    const width = Number(info.width), height = Number(info.height);
    if (info.kind !== 'video' || !Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0) {
      throw new Error('The enhanced result does not have valid video dimensions.');
    }
    if (timeline?.path !== path || timeline?.revision !== revisionAtStart) throw new Error('The sequence changed while result dimensions were loading. Review the current timeline and retry.');
    await edit([{ op: 'set_profile', preserve_frame_numbers: true, width, height,
      sample_aspect_num: 1, sample_aspect_den: 1, display_aspect_num: width, display_aspect_den: height }]);
    setMessage('Sequence dimensions now match the enhanced result. Frame rate and frame numbers were preserved.');
  }

  updateControls();
  updateContext();
})();
