const assert = require('assert');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

(async () => {
  const { createCorporateActionsPanel } = await import('../web/features/stocks/corporate-actions.js');
  const requests = [], updates = [];
  const actions = createCorporateActionsPanel({
    api: (path, options) => {
      const wait = deferred();
      requests.push({ path, options, ...wait });
      return wait.promise;
    },
    esc: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]),
    onUpdate: (id, html) => updates.push({ id, html }),
  });

  const firstLoad = actions.load('NASDAQ:ABC');
  assert.deepEqual(requests.map(item => item.path), [
    '/stocks/NASDAQ%3AABC/events', '/stocks/NASDAQ%3AABC/dividends', '/stocks/NASDAQ%3AABC/dividends/holdings',
  ]);
  let html = actions.panel('NASDAQ:ABC');
  assert(html.includes('Loading company events'));
  assert(html.includes('Loading dividend history'));
  assert(html.includes('Estimating holdings dividend income'));
  requests[0].resolve({ security_id: 'NASDAQ:ABC', currency: 'USD', events: [
    { kind: 'earnings', dates: ['2026-11-02', '2026-11-05'], date_range: { start: '2026-11-02', end: '2026-11-05' }, date_source: 'provider', date_status: 'provider_supplied_unverified' },
    { kind: 'dividend_date', provider_label: 'Dividend Date', dates: ['2026-11-20'], date_source: 'provider', date_status: 'provider_supplied_unverified' },
  ], source: 'Provider X', cache: { fetched_at: '2026-10-10T12:00:00Z', stale: true } });
  requests[1].resolve({ security_id: 'NASDAQ:ABC', period_years: 5, currency: null, date_semantics: 'ex_date',
    dividends: [{ ex_date: '2026-08-01', amount_per_share: '0.25', currency: null, denomination_status: 'unverified', split_adjustment_status: 'unsupported' }],
    source: 'Provider X', cache: { fetched_at: '2026-10-10T12:00:00Z', stale: true } });
  requests[2].resolve({ security_id: 'NASDAQ:ABC', period_years: 5, currency: null, date_semantics: 'ex_date',
    dividends: [
      { ex_date: '2026-08-01', amount_per_share: '0.25', currency: 'USD', status: 'estimated_with_unknown_accounts',
        accounts: [{ account_id: 'broker', account_name: 'Brokerage', shares: '10', gross_income: '2.5', currency: 'USD', status: 'estimated' },
          { account_id: 'other', account_name: 'Other', shares: null, gross_income: null, status: 'unknown_same_day_activity' }], total_gross_income: '2.5' },
      { ex_date: '2026-05-01', amount_per_share: '0.2', currency: 'USD', status: 'unsupported', accounts: [], total_gross_income: null, unsupported_reason: 'split_adjustment_unverified' },
      { ex_date: '2026-03-01', amount_per_share: '0.2', currency: null, status: 'unsupported', accounts: [], total_gross_income: null, unsupported_reason: 'dividend_denomination_unverified' },
    ], limitations: ['Gross estimate only; excludes withholding tax, FX, and reinvestment.'], source: 'Provider X', cache: { fetched_at: '2026-10-10T12:00:00Z', stale: true } });
  await firstLoad;
  html = actions.panel('NASDAQ:ABC');
  assert(html.includes('Provider date range: 2026-11-02 to 2026-11-05'));
  assert(html.includes('Dividend date (provider)'));
  assert(html.includes('Unverified provider estimate; dates may change'));
  assert(html.includes('Last retrieved 2026-10-10T12:00:00Z · Cached data may be stale'));
  assert(html.includes('Dates are ex-dividend dates. Record and payment dates are not supplied'));
  assert(!html.includes('<th scope="col">Payment date</th>'));
  assert(html.includes('Units or denomination unknown; excluded from holdings income.'));
  assert(html.includes('Adjustment status'));
  assert(html.includes('Partial estimate · known accounts only'));
  assert(html.includes('Gross estimate / known-account subtotal'));
  assert(html.includes('Unknown · activity on ex-date'));
  assert(html.includes('Split adjustment could not be verified; estimate excluded.'));
  assert(html.includes('Dividend currency or units could not be verified; estimate excluded.'));
  assert(html.toLowerCase().includes('withholding tax, fx, and reinvestment'));
  actions.invalidate();
  assert.equal(actions.getState().securityId, '', 'logout clears the prior principal\'s holdings-income response');
  assert(!actions.panel('NASDAQ:ABC').includes('Partial estimate · known accounts only'));

  const isolated = createCorporateActionsPanel({
    api: async path => {
      if (path.endsWith('/events')) return { security_id: 'LSE:ETF', supported: false, events: [], source: 'Provider' };
      if (path.endsWith('/dividends/holdings')) throw new Error('Ledger estimate offline');
      return { security_id: 'LSE:ETF', period_years: 5, currency: 'GBP', date_semantics: 'ex_date', dividends: [], source: 'Provider' };
    }, esc: String,
  });
  await isolated.load('LSE:ETF');
  html = isolated.panel('LSE:ETF');
  assert(html.includes('Company events are not available for this security type.'));
  assert(html.includes('No provider-reported dividend history is available.'));
  assert(html.includes('Ledger estimate offline'));
  assert(html.includes('stock-holdings-dividends-retry'));

  const racing = createCorporateActionsPanel({ api: (_path, options) => {
    const wait = deferred();
    raceRequests.push({ ...wait, signal: options.signal });
    return wait.promise;
  }, esc: String });
  const raceRequests = [];
  const staleLoad = racing.load('NYSE:OLD');
  const currentLoad = racing.load('SGX:NEW');
  assert(raceRequests.slice(0, 3).every(item => item.signal.aborted), 'security switches abort all prior section requests');
  raceRequests.slice(3).forEach(item => item.resolve({ security_id: 'SGX:NEW', events: [], dividends: [] }));
  await currentLoad;
  raceRequests.slice(0, 3).forEach(item => item.resolve({ security_id: 'NYSE:OLD', events: [], dividends: [] }));
  await staleLoad;
  assert.equal(racing.getState().securityId, 'SGX:NEW');
  assert(!racing.panel('SGX:NEW').includes('NYSE:OLD'));
  assert(updates.some(update => update.id === 'NASDAQ:ABC'));
  console.log('PASS: independent corporate-event, provider-dividend, and holdings-income panels honor date and estimate limitations');
})().catch(error => { console.error(error); process.exitCode = 1; });
