/* Native macOS transport. Browser development mode keeps its existing HTTP API. */
(() => {
  'use strict';
  if (!window.webkit?.messageHandlers?.film) return;
  const pending = new Map();
  // Request identifiers are unique to this document. Late responses from a project
  // that navigated away cannot resolve a new project's promises.
  const documentID = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join('-');
  let sequence = 0;
  function request(action, body = {}) {
    const id = documentID + '-' + (++sequence);
    return new Promise((resolve, reject) => {
      pending.set(id, {resolve, reject});
      try { window.webkit.messageHandlers.film.postMessage({id, action, body}); }
      catch (error) { pending.delete(id); reject(error); }
    });
  }
  window.FilmDesktop = Object.freeze({
    api: (route, body) => request('api', {route, ...(body === undefined ? {} : {body})}),
    importFiles: kind => request('importDialog', {kind}),
    saveMedia: (url, name) => request('saveMedia', {url, name}),
    revealWorkspace: () => request('revealWorkspace'),
    async uploadFiles(files, kind) {
      const assets = [];
      for (const file of files) {
        const upload = await request('upload', {action: 'begin', name: file.name, size: file.size, kind});
        try {
          for (let offset = 0; offset < file.size; offset += 256 * 1024) {
            const bytes = new Uint8Array(await file.slice(offset, offset + 256 * 1024).arrayBuffer());
            let binary = '';
            for (let start = 0; start < bytes.length; start += 8192) {
              binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
            }
            await request('upload', {action: 'chunk', id: upload.id, offset, data: btoa(binary)});
          }
          assets.push(await request('upload', {action: 'finish', id: upload.id}));
        } catch (error) {
          await request('upload', {action: 'cancel', id: upload.id}).catch(() => {});
          throw error;
        }
      }
      return assets;
    },
    _resolve(id, value, error) {
      const task = pending.get(id);
      if (!task) return;
      pending.delete(id);
      if (error) task.reject(new Error(error));
      else task.resolve(value);
    }
  });
  document.documentElement.classList.add('native-desktop');
  window.addEventListener('pagehide', () => {
    window.webkit.messageHandlers.film.postMessage({id: documentID + '-cancel', action: 'cancelUploads', body: {}});
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[download]');
    if (!link) return;
    event.preventDefault();
    window.FilmDesktop.saveMedia(link.href, link.download).catch(error => {
      const notice = document.getElementById('notice');
      if (notice) { notice.textContent = error.message; notice.classList.add('error'); }
    });
  });
})();
