/* Display app-server token counts for the active FilmEditor Codex thread. */
(function (global) {
  'use strict';

  const countFields = [
    ['inputTokens', 'Input'],
    ['cachedInputTokens', 'Cached input'],
    ['cacheWriteInputTokens', 'Cache write input'],
    ['outputTokens', 'Output'],
    ['reasoningOutputTokens', 'Reasoning'],
  ];

  function validCount(value) {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  }

  function formatted(value) {
    return new Intl.NumberFormat().format(value);
  }

  function meterFor(host) {
    return Array.from(host.children).find(child => child.id === 'codex-token-usage') || null;
  }

  function renderTokenUsage(usage, target) {
    if (typeof document === 'undefined') return null;
    const host = target || document.getElementById('messages');
    if (!host || typeof host.append !== 'function') return null;

    let meter = meterFor(host);
    const total = usage && usage.total && typeof usage.total === 'object' ? usage.total : null;
    const last = usage && usage.last && typeof usage.last === 'object' ? usage.last : null;
    const hasCount = Boolean(total && validCount(total.totalTokens)) ||
      Boolean(last && validCount(last.totalTokens)) ||
      Boolean(total && countFields.some(([key]) => validCount(total[key])));

    if (!hasCount) {
      if (meter) meter.remove();
      return null;
    }

    if (!meter) {
      meter = document.createElement('section');
      meter.id = 'codex-token-usage';
      meter.setAttribute('aria-label', 'Codex thread token usage');
      meter.style.cssText = 'display:flex;flex-direction:column;gap:3px;margin:8px 0 4px;padding:7px 9px;border:1px solid #383d46;border-radius:6px;background:#1e2024;color:#98a2b0;font-size:10px;line-height:1.4;';
      host.append(meter);
    }

    meter.replaceChildren();
    const heading = document.createElement('div');
    heading.textContent = 'Codex thread usage';
    heading.style.cssText = 'color:#c3cad4;font-weight:600;';
    meter.append(heading);

    if (total && validCount(total.totalTokens)) {
      const totalLine = document.createElement('div');
      totalLine.textContent = `Thread total: ${formatted(total.totalTokens)} tokens`;
      meter.append(totalLine);
    }

    const detailParts = [];
    for (const [key, label] of countFields) {
      if (total && validCount(total[key])) detailParts.push(`${label}: ${formatted(total[key])}`);
    }
    if (detailParts.length) {
      const details = document.createElement('div');
      details.textContent = detailParts.join(' · ');
      meter.append(details);
    }

    if (last && validCount(last.totalTokens)) {
      const contextWindow = usage && validCount(usage.modelContextWindow) && usage.modelContextWindow > 0
        ? usage.modelContextWindow
        : null;
      const lastLine = document.createElement('div');
      if (contextWindow !== null) {
        const percent = last.totalTokens / contextWindow * 100;
        const reportedPercent = percent === 0 ? '0%' : percent < 0.1 ? '<0.1%' : `${(Math.round(percent * 10) / 10).toFixed(1)}%`;
        lastLine.textContent = `Latest model usage: ${formatted(last.totalTokens)} / ${formatted(contextWindow)} context window (${reportedPercent})`;
        meter.append(lastLine);

        const progress = document.createElement('progress');
        progress.max = 100;
        progress.value = Math.min(100, percent);
        progress.setAttribute('aria-label', 'Latest reported tokens compared with model context window');
        progress.setAttribute('aria-valuetext', `${formatted(last.totalTokens)} of ${formatted(contextWindow)} tokens (${reportedPercent})`);
        progress.style.cssText = 'width:100%;height:5px;accent-color:#7899c7;';
        meter.append(progress);
      } else {
        lastLine.textContent = `Latest model usage: ${formatted(last.totalTokens)} tokens`;
        meter.append(lastLine);
      }
    }

    return meter;
  }

  global.renderTokenUsage = renderTokenUsage;
  global.FilmEditorTokenUsage = { renderTokenUsage };
  if (typeof module === 'object' && module.exports) module.exports = { renderTokenUsage };
})(typeof globalThis === 'object' ? globalThis : window);
