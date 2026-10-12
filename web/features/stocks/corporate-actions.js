/** Provider-reported events and dividend history, isolated from core stock data. */
const EVENT_LABELS = { earnings: 'Earnings date', ex_dividend: 'Ex-dividend date', dividend_date: 'Dividend date (provider)', dividend_payment: 'Dividend payment date' };

export function createCorporateActionsPanel({ api, esc, onUpdate = () => {} }) {
  let state = { securityId: '', events: sectionState(), dividends: sectionState(), holdingsDividends: sectionState() };
  let controller = null;
  let generation = 0;

  function sectionState() {
    return { loading: false, error: '', payload: null, refreshQueued: false };
  }

  function dateText(value) {
    return value == null || value === '' ? 'Not reported by provider' : esc(value);
  }

  function amountText(amount, currency) {
    if (amount == null || amount === '') return 'Unavailable';
    const formatted = Number(amount).toLocaleString('en-SG', { maximumFractionDigits: 6 });
    return `${esc(currency || 'Currency not reported')} ${formatted}`;
  }

  function freshness(payload) {
    const retrieved = payload?.cache?.fetched_at ? `Last retrieved ${esc(payload.cache.fetched_at)}` : '';
    return `${retrieved}${payload?.cache?.stale ? `${retrieved ? ' · ' : ''}Cached data may be stale` : ''}`;
  }

  function statusLabel(status) {
    return ({
      estimated: 'Estimated', estimated_with_unknown_accounts: 'Partial estimate · known accounts only',
      no_holding: 'No holding recorded before ex-date', no_recorded_holding_history: 'No recorded holding history',
      unknown: 'Unknown', unsupported: 'Unsupported', unknown_same_day_activity: 'Unknown · activity on ex-date',
    })[status] || status || 'Unknown';
  }

  function adjustmentLabel(status) {
    return ({
      verified_no_later_split: 'Verified · no later split found',
      unsupported: 'Unsupported · split adjustment unverified',
    })[status] || status || 'Unverified';
  }

  function reasonLabel(reason) {
    return ({
      split_adjustment_unverified: 'Split adjustment could not be verified; estimate excluded.',
      dividend_denomination_unverified: 'Dividend currency or units could not be verified; estimate excluded.',
    })[reason] || reason;
  }

  function eventsPanel(securityId, section) {
    const payload = section.payload;
    const rows = payload?.events || [];
    const source = typeof payload?.source === 'string' ? payload.source : payload?.source?.name;
    if (payload?.supported === false) return `<section class="panel stock-corporate-panel" aria-labelledby="stock-events-title"><div class="panel-head"><h2 id="stock-events-title">Company events</h2></div><p>Company events are not available for this security type.</p></section>`;
    return `<section class="panel stock-corporate-panel" aria-labelledby="stock-events-title">
      <div class="panel-head"><h2 id="stock-events-title">Company events</h2><button class="secondary small-button" data-action="stock-events-retry" data-id="${esc(securityId)}" ${section.loading ? 'disabled' : ''}>${section.loading ? 'Loading…' : 'Refresh'}</button></div>
      <p class="section-note">Provider-reported event dates are labeled by type. Missing dates are not inferred.</p>
      ${section.error ? `<p class="error" role="alert">Events unavailable: ${esc(section.error)} <button class="text-button" data-action="stock-events-retry" data-id="${esc(securityId)}">Retry</button></p>` : ''}
      ${section.loading && !payload ? '<p role="status">Loading company events…</p>' : ''}
      ${source || freshness(payload) ? `<p class="section-note">${source ? `Source: ${esc(source)}${freshness(payload) ? ' · ' : ''}` : ''}${freshness(payload)}</p>` : ''}
      ${rows.length ? `<ol class="stock-event-list">${rows.map(event => {
        const dates = Array.isArray(event.dates) ? event.dates : [];
        const range = event.date_range;
        const uncertain = event.date_status === 'provider_supplied_unverified';
        return `<li><article><h3>${esc(EVENT_LABELS[event.kind] || event.provider_label || event.kind || 'Company event')}</h3>${range ? `<p>Provider date range: ${dateText(range.start)} to ${dateText(range.end)}</p>` : ''}${dates.length ? `<ul class="stock-event-dates">${dates.map(date => `<li>${dateText(date)}</li>`).join('')}</ul>` : '<p>No date was supplied by the provider.</p>'}${event.date_source === 'provider' ? '<p class="stock-event-status">Provider-supplied date</p>' : ''}${uncertain ? '<p class="stock-event-status">Unverified provider estimate; dates may change.</p>' : ''}</article></li>`;
      }).join('')}</ol>` : payload && !section.loading ? '<p>No provider-reported company events are available.</p>' : ''}
    </section>`;
  }

  function dividendsPanel(securityId, section) {
    const payload = section.payload;
    const rows = payload?.dividends || [];
    const source = typeof payload?.source === 'string' ? payload.source : payload?.source?.name;
    return `<section class="panel stock-corporate-panel" aria-labelledby="stock-dividends-title">
      <div class="panel-head"><h2 id="stock-dividends-title">Dividend history</h2><button class="secondary small-button" data-action="stock-dividends-retry" data-id="${esc(securityId)}" ${section.loading ? 'disabled' : ''}>${section.loading ? 'Loading…' : 'Refresh'}</button></div>
      <p class="section-note">Historical provider data only. Amounts are per share in the reported currency; this does not post income to your ledger.</p>
      ${section.error ? `<p class="error" role="alert">Dividend history unavailable: ${esc(section.error)} <button class="text-button" data-action="stock-dividends-retry" data-id="${esc(securityId)}">Retry</button></p>` : ''}
      ${section.loading && !payload ? '<p role="status">Loading dividend history…</p>' : ''}
      ${source || freshness(payload) ? `<p class="section-note">${source ? `Source: ${esc(source)}${freshness(payload) ? ' · ' : ''}` : ''}${freshness(payload)}</p>` : ''}
      ${payload?.period_years ? `<p class="section-note">Provider history window: ${esc(payload.period_years)} years.</p>` : ''}
      ${payload?.date_semantics === 'ex_date' ? '<p class="section-note">Dates are ex-dividend dates. Record and payment dates are not supplied by this endpoint.</p>' : ''}
      ${rows.length ? `<div class="table-wrap stock-dividend-table"><table><thead><tr><th scope="col">Ex-dividend date</th><th scope="col" class="num">Amount per share</th><th scope="col">Currency</th><th scope="col">Adjustment status</th></tr></thead><tbody>${rows.map(row => `<tr><td>${dateText(row.ex_date)}</td><td class="num">${row.amount_per_share == null ? 'Unavailable' : Number(row.amount_per_share).toLocaleString('en-SG', {maximumFractionDigits: 6})}</td><td>${esc(row.currency || payload.currency || 'Not reported')}${row.denomination_status === 'unverified' ? '<small>Units or denomination unknown; excluded from holdings income.</small>' : ''}</td><td>${esc(adjustmentLabel(row.split_adjustment_status))}</td></tr>`).join('')}</tbody></table></div>` : payload && !section.loading ? '<p>No provider-reported dividend history is available.</p>' : ''}
    </section>`;
  }

  function holdingsDividendsPanel(securityId, section) {
    const payload = section.payload;
    const estimates = payload?.dividends || [];
    const source = typeof payload?.source === 'string' ? payload.source : payload?.source?.name;
    return `<section class="panel stock-corporate-panel" aria-labelledby="stock-holdings-dividends-title">
      <div class="panel-head"><h2 id="stock-holdings-dividends-title">Estimated dividend income from your holdings</h2><button class="secondary small-button" data-action="stock-holdings-dividends-retry" data-id="${esc(securityId)}" ${section.loading ? 'disabled' : ''}>${section.loading ? 'Loading…' : 'Refresh'}</button></div>
      <p class="section-note">Gross historical estimates use the shares held before each ex-dividend date. They are not actual cash receipts. Withholding, foreign-exchange conversion, reinvestment, and ledger posting are excluded.</p>
      ${section.error ? `<p class="error" role="alert">Holdings dividend estimates unavailable: ${esc(section.error)} <button class="text-button" data-action="stock-holdings-dividends-retry" data-id="${esc(securityId)}">Retry</button></p>` : ''}
      ${section.loading && !payload ? '<p role="status">Estimating holdings dividend income…</p>' : ''}
      ${source || freshness(payload) ? `<p class="section-note">${source ? `Source: ${esc(source)}${freshness(payload) ? ' · ' : ''}` : ''}${freshness(payload)}</p>` : ''}
      ${payload?.period_years ? `<p class="section-note">Provider history window: ${esc(payload.period_years)} years.</p>` : ''}
      ${estimates.length ? `<div class="table-wrap stock-dividend-table"><table><thead><tr><th scope="col">Ex-dividend date</th><th scope="col">Amount per share</th><th scope="col">Holding accounts</th><th scope="col" class="num">${estimates.some(row => row.status === 'estimated_with_unknown_accounts') ? 'Gross estimate / known-account subtotal' : 'Gross estimate'}</th><th scope="col">Status</th></tr></thead><tbody>${estimates.map(row => `<tr><td>${dateText(row.ex_date)}</td><td>${amountText(row.amount_per_share, row.currency || payload.currency)}</td><td>${row.accounts?.length ? row.accounts.map(account => `<span class="stock-dividend-account">${esc(account.account_name || account.account_id || 'Account unavailable')}: ${account.shares == null ? 'shares unknown' : `${Number(account.shares).toLocaleString('en-SG', {maximumFractionDigits: 6})} shares`} · ${statusLabel(account.status)}</span>`).join('') : 'No supported holdings'}</td><td class="num">${amountText(row.total_gross_income, row.currency || payload.currency)}</td><td>${statusLabel(row.status)}${row.unsupported_reason ? `<small class="stock-estimate-reason">${esc(reasonLabel(row.unsupported_reason))}</small>` : ''}</td></tr>`).join('')}</tbody></table></div>` : payload && !section.loading ? '<p>No historical dividend records are available for holdings in this security.</p>' : ''}
      ${payload?.limitations?.length ? `<ul class="stock-dividend-limitations">${payload.limitations.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}
      <p class="section-note">These estimates do not imply a future dividend or return. Each row uses provider-supplied ex-date and historical shares; unsupported and uncertain rows remain visible.</p>
    </section>`;
  }

  function panel(securityId) {
    const current = state.securityId === securityId ? state : {
      securityId,
      events: { ...sectionState(), loading: true },
      dividends: { ...sectionState(), loading: true },
      holdingsDividends: { ...sectionState(), loading: true },
    };
    return `<div class="stock-corporate-actions">${eventsPanel(securityId, current.events)}${dividendsPanel(securityId, current.dividends)}${holdingsDividendsPanel(securityId, current.holdingsDividends)}</div>`;
  }

  function notify(securityId) {
    onUpdate(securityId, panel(securityId));
  }

  async function loadSection(securityId, kind, requestGeneration = generation) {
    const section = state[kind];
    section.loading = true;
    section.error = '';
    notify(securityId);
    const endpoint = kind === 'events' ? 'events' : kind === 'dividends' ? 'dividends' : 'dividends/holdings';
    try {
      const payload = await api(`/stocks/${encodeURIComponent(securityId)}/${endpoint}`, { signal: controller?.signal });
      if (requestGeneration !== generation || state.securityId !== securityId) return;
      const key = kind === 'events' ? 'events' : 'dividends';
      if (payload?.security_id !== securityId || !Array.isArray(payload[key])) {
        throw new Error('The provider response was incomplete.');
      }
      section.payload = payload;
    } catch (error) {
      if (requestGeneration !== generation || state.securityId !== securityId || error?.name === 'AbortError') return;
      section.error = error?.message || 'Unable to load provider data.';
    } finally {
      if (requestGeneration === generation && state.securityId === securityId) {
        section.loading = false;
        notify(securityId);
        if (section.refreshQueued) {
          section.refreshQueued = false;
          loadSection(securityId, kind, requestGeneration);
        }
      }
    }
  }

  function load(securityId) {
    controller?.abort();
    controller = new AbortController();
    const requestGeneration = ++generation;
    state = { securityId, events: { ...sectionState(), loading: true }, dividends: { ...sectionState(), loading: true }, holdingsDividends: { ...sectionState(), loading: true } };
    notify(securityId);
    return Promise.allSettled([
      loadSection(securityId, 'events', requestGeneration),
      loadSection(securityId, 'dividends', requestGeneration),
      loadSection(securityId, 'holdingsDividends', requestGeneration),
    ]);
  }

  function retry(securityId, kind) {
    if (state.securityId !== securityId || !['events', 'dividends', 'holdingsDividends'].includes(kind)) return Promise.resolve();
    if (state[kind].loading) { state[kind].refreshQueued = true; return Promise.resolve(); }
    return loadSection(securityId, kind);
  }

  function invalidate() {
    generation++;
    controller?.abort();
    controller = null;
    state = { securityId: '', events: sectionState(), dividends: sectionState(), holdingsDividends: sectionState() };
  }

  return { panel, load, retry, invalidate, getState: () => state };
}
