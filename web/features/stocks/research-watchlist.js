/* Versioned requests prevent a previous user's responses from changing the view. */
export function createResearchWatchlist({api, esc, onUpdate}) {
  let ids = [], loaded = false, loading = false, error = '', generation = 0, saving = false;
  async function load() {
    if (loading || loaded) return;
    loading = true;
    const request = ++generation;
    try {
      const result = await api('/research/watchlist');
      if (request !== generation) return;
      ids = result.security_ids;
      loaded = true;
      error = '';
    } catch (failure) {
      if (request === generation) { error = failure.message; loaded = true; }
    } finally {
      if (request === generation) { loading = false; onUpdate(); }
    }
  }
  async function update(id, remove = false) {
    if (saving || !loaded || loading) return;
    const request = ++generation;
    saving = true;
    error = '';
    onUpdate();
    try {
      const result = await api(`/research/watchlist/${encodeURIComponent(id)}`, {method: remove ? 'DELETE' : 'PUT'});
      if (request === generation) ids = result.security_ids;
    } catch (failure) {
      if (request === generation) error = failure.message;
    } finally {
      if (request === generation) { saving = false; onUpdate(); }
    }
  }
  function panel(selected) {
    if (!loaded && !loading) queueMicrotask(load);
    const present = ids.includes(selected);
    return `<section class="panel research-watchlist"><h2>Research watchlist</h2>
      <p class="section-note">Saved research candidates for your signed-in account, separate from your holdings.</p>
      ${error ? `<p role="alert">${esc(error)}</p><button data-action="watchlist-retry">Retry loading</button>` : ''}
      ${loading || !loaded ? '<p>Loading watchlist…</p>' : ''}
      ${selected && loaded ? `<button data-action="watchlist-toggle" data-id="${esc(selected)}" data-remove="${present}" ${saving || error ? 'disabled' : ''}>${present ? 'Remove from watchlist' : 'Add to watchlist'}</button>` : ''}
      <ul class="research-watchlist-list">${ids.map(id => `<li><a href="#stocks/${encodeURIComponent(id)}">${esc(id)}</a><button class="text-button negative" data-action="watchlist-remove" data-id="${esc(id)}" ${saving ? 'disabled' : ''} aria-label="Remove ${esc(id)} from watchlist">Remove</button></li>`).join('')}</ul>
      ${loaded && !ids.length && !error ? '<p>Search and open a security above, then add it to your watchlist.</p>' : ''}
    </section>`;
  }
  return {
    panel, update,
    retry() { if (!saving && !loading) { loaded = false; return load(); } },
    invalidate() { generation++; ids = []; loaded = false; loading = false; saving = false; error = ''; },
  };
}
