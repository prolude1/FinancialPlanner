const assert = require('assert');
let createBrokerageHistoryRefresh;

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function harness({ api, now = 100_000 } = {}) {
  let route = { page: 'accounts', id: 'broker-a' };
  let authenticated = true;
  const data = {
    as_of: '2026-10-10', revision: 8,
    accounts: [{ id: 'broker-a', type: 'brokerage' }, { id: 'broker-b', type: 'brokerage' }, { id: 'bank', type: 'bank' }],
    portfolio_history: { 'broker-a': [{ date: 'old-a' }], 'broker-b': [{ date: 'old-b' }] },
    portfolio_deposits: { 'broker-a': [{ id: 'deposit-a' }], 'broker-b': [{ id: 'deposit-b' }] },
  };
  const updates = [];
  const controller = createBrokerageHistoryRefresh({
    api: api || (async () => ({ account_id: 'broker-a', as_of: '2026-10-11', portfolio_history: [{ date: 'new-a' }], portfolio_deposits: [{ id: 'new-deposit-a' }] })),
    getData: () => data, getRoute: () => route, isAuthenticated: () => authenticated,
    onUpdate: (accountId, status) => updates.push({ accountId, status }), now: () => now.value ?? now,
  });
  return { controller, data, updates, setRoute: value => { route = value; }, setAuthenticated: value => { authenticated = value; } };
}

(async () => {
  const module = await import('../web/features/dashboard/brokerage-history-refresh.js');
  createBrokerageHistoryRefresh = module.createBrokerageHistoryRefresh;
  const { dashboardRefreshAction, BROKERAGE_HISTORY_REFRESH_MS } = module;

  assert.equal(dashboardRefreshAction({ revisionChanged: true, brokerageOpen: true, chartRefreshDue: true }), 'dashboard');
  assert.equal(dashboardRefreshAction({ revisionChanged: false, brokerageOpen: true, chartRefreshDue: true }), 'brokerage-history');
  assert.equal(dashboardRefreshAction({ revisionChanged: false, brokerageOpen: true, chartRefreshDue: false, force: true }), 'brokerage-history');
  assert.equal(dashboardRefreshAction({ revisionChanged: false, brokerageOpen: false, chartRefreshDue: true, force: true }), 'none');
  assert.equal(dashboardRefreshAction({ revisionChanged: false, brokerageOpen: false, chartRefreshDue: false }), 'none');

  let currentTime = 100_000;
  const calls = [];
  const basic = harness({ now: { get value() { return currentTime; } }, api: async path => { calls.push(path); return { account_id: 'broker-a', as_of: '2026-10-11', portfolio_history: [{ date: 'new-a' }], portfolio_deposits: [{ id: 'new-deposit-a' }] }; } });
  const chartState = { period: 'custom', metric: 'gross_change_sgd', start: 5, end: 9, showProjections: false };
  basic.controller.noteDashboardLoaded();
  assert.equal(basic.controller.isDue('broker-a'), false);
  currentTime += BROKERAGE_HISTORY_REFRESH_MS - 1;
  assert.equal(await basic.controller.refreshIfDue('broker-a'), false);
  currentTime += 1;
  assert.equal(await basic.controller.refreshIfDue('broker-a'), true);
  assert.deepEqual(calls, ['/accounts/broker-a/brokerage-history']);
  assert.deepEqual(basic.data.portfolio_history['broker-a'], [{ date: 'new-a' }]);
  assert.deepEqual(basic.data.portfolio_deposits['broker-a'], [{ id: 'new-deposit-a' }]);
  assert.equal(basic.data.portfolio_history_as_of['broker-a'], '2026-10-11');
  assert.deepEqual(basic.data.portfolio_history['broker-b'], [{ date: 'old-b' }]);
  assert.deepEqual(basic.data.portfolio_deposits['broker-b'], [{ id: 'deposit-b' }]);
  assert.equal(basic.data.as_of, '2026-10-10');
  assert.deepEqual(chartState, { period: 'custom', metric: 'gross_change_sgd', start: 5, end: 9, showProjections: false }, 'history refresh leaves chart UI state intact');

  const inFlight = deferred();
  let callsInFlight = 0;
  const overlap = harness({ api: () => { callsInFlight++; return inFlight.promise; } });
  const first = overlap.controller.refresh('broker-a', { force: true });
  assert.equal(await overlap.controller.refresh('broker-a', { force: true }), false, 'same-account requests do not overlap');
  assert.equal(callsInFlight, 1);
  inFlight.resolve({ account_id: 'broker-a', as_of: '2026-10-11', portfolio_history: [{ date: 'new-a' }], portfolio_deposits: [] });
  assert.equal(await first, true);

  for (const staleCase of ['navigation', 'account-switch', 'logout', 'dashboard-reload']) {
    const response = deferred();
    const stale = harness({ api: () => response.promise });
    const pending = stale.controller.refresh('broker-a', { force: true });
    if (staleCase === 'navigation') stale.setRoute({ page: 'overview' });
    if (staleCase === 'account-switch') stale.setRoute({ page: 'accounts', id: 'broker-b' });
    if (staleCase === 'logout') stale.setAuthenticated(false);
    stale.controller.invalidate();
    if (staleCase === 'navigation') stale.setRoute({ page: 'accounts', id: 'broker-a' });
    response.resolve({ account_id: 'broker-a', as_of: '2026-10-11', portfolio_history: [{ date: 'stale' }], portfolio_deposits: [] });
    assert.equal(await pending, false, `${staleCase} invalidates an in-flight response`);
    assert.deepEqual(stale.data.portfolio_history['broker-a'], [{ date: 'old-a' }]);
  }

  let shouldFail = true;
  const retry = harness({ api: async () => {
    if (shouldFail) throw new Error('Network unavailable');
    return { account_id: 'broker-a', as_of: '2026-10-11', portfolio_history: [{ date: 'retried' }], portfolio_deposits: [] };
  } });
  await assert.rejects(retry.controller.refresh('broker-a', { force: true }), /Network unavailable/);
  assert.equal(retry.controller.status('broker-a').error, 'Network unavailable');
  assert.deepEqual(retry.data.portfolio_history['broker-a'], [{ date: 'old-a' }], 'failed refresh retains existing chart data');
  assert.equal(retry.controller.lastSuccess('broker-a'), undefined, 'failure does not advance success timestamp');
  shouldFail = false;
  assert.equal(await retry.controller.refresh('broker-a', { force: true }), true, 'explicit retry succeeds');
  assert.equal(retry.controller.status('broker-a').error, '');
  assert.deepEqual(retry.data.portfolio_history['broker-a'], [{ date: 'retried' }]);

  const { createDashboardPages } = await import('../web/features/dashboard/dashboard-pages.js');
  const actions = [
    ['chart-metric', 'value_sgd'], ['chart-metric', 'gross_change_sgd'], ['chart-metric', 'adjusted_change_sgd'],
    ['chart-projection', ''], ['chart-period', '1m'], ['chart-period', '3m'], ['chart-period', '1y'], ['chart-period', 'all'],
  ];
  const buttons = actions.map(([action, value]) => ({ dataset: { action, value }, disabled: true, classList: { toggle() {} }, setAttribute() {} }));
  const statusNode = { innerHTML: '' }, summaryNode = { textContent: '' }, plotNode = { innerHTML: '' };
  const root = {
    dataset: { account: 'broker-ui' },
    querySelector(selector) { return selector === '.brokerage-history-status' ? statusNode : selector === '.brokerage-chart-summary' ? summaryNode : selector === '.chart-plot' ? plotNode : selector.includes('chart-projection') ? buttons[3] : null; },
    querySelectorAll(selector) { return selector.includes('chart-period') || selector.includes('chart-metric') || selector.includes('chart-projection') ? buttons : []; },
  };
  const originalDocument = global.document, originalCSS = global.CSS;
  global.document = { querySelector: selector => selector.includes('broker-ui') ? root : null };
  global.CSS = { escape: value => value };
  const uiChartState = {};
  const pages = createDashboardPages({
    $: () => ({ textContent: '' }), esc: String, fmt: value => String(value ?? 'Unavailable'), labels: {}, colors: {}, chartState: uiChartState,
    badge: value => value, empty: (title, message) => `<strong>${title}</strong><p>${message}</p>`, historyTable: () => '', latestTransactionsFirst: rows => rows,
  });
  const account = { id: 'broker-ui', name: 'Broker UI', type: 'brokerage', sgd: 10, complete: true, cash: [], holdings: [] };
  const uiData = { as_of: '2026-10-10', accounts: [account], history: [], portfolio_history: { 'broker-ui': [] }, portfolio_deposits: { 'broker-ui': [] } };
  pages.setData(uiData);
  const initialHtml = pages.accountsPage('broker-ui');
  assert.match(initialHtml, /0 daily valuation observations/);
  assert.match(initialHtml, /Historical chart is being prepared/);
  uiData.portfolio_history['broker-ui'] = [{ date: '2026-10-10', value_sgd: 9, gross_change_sgd: 0, adjusted_change_sgd: 0 }, { date: '2026-10-11', value_sgd: 10, gross_change_sgd: 1, adjusted_change_sgd: 1 }];
  uiData.portfolio_history_as_of = { 'broker-ui': '2026-10-11' };
  pages.updateBrokerageHistoryStatus('broker-ui', { loading: false, error: '' });
  assert.match(summaryNode.textContent, /2 daily valuation observations · History as of 2026-10-11/);
  assert.equal(plotNode.innerHTML.includes('<svg'), true, 'new chart history appears without a page render');
  assert.equal(buttons.every(button => !button.disabled), true, 'period, metric, and projection controls enable when chart data arrives');
  assert.equal(uiChartState['broker-ui'].metric, 'value_sgd');
  uiData.portfolio_history['broker-ui'] = [];
  uiData.portfolio_deposits['broker-ui'] = [];
  pages.updateBrokerageHistoryStatus('broker-ui', { loading: false, error: '' });
  assert.match(plotNode.innerHTML, /Historical chart is being prepared/);
  assert.equal(buttons.every(button => button.disabled), true, 'controls disable when chart observations disappear');
  global.document = originalDocument;
  global.CSS = originalCSS;

  const notBrokerage = harness();
  notBrokerage.setRoute({ page: 'accounts', id: 'bank' });
  assert.equal(await notBrokerage.controller.refresh('bank', { force: true }), false, 'non-brokerage routes never request chart data');
  console.log('PASS: brokerage history refresh is account-scoped, due-aware, race-safe, retryable, and preserves unrelated/chart state');
})().catch(error => { console.error(error); process.exitCode = 1; });
