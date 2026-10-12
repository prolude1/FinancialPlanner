const assert = require('assert');

(async () => {
  const { createDashboardPages } = await import('../web/features/dashboard/dashboard-pages.js');
  const rows = [
    { date: '2026-01-01', value_sgd: '1000', gross_change_sgd: null, adjusted_change_sgd: null },
    { date: '2026-01-02', value_sgd: '1010', gross_change_sgd: '10', adjusted_change_sgd: '10' },
    { date: '2026-01-03', value_sgd: '1030', gross_change_sgd: '20', adjusted_change_sgd: '20' },
    { date: '2026-01-04', value_sgd: '1035', gross_change_sgd: null, adjusted_change_sgd: null },
    { date: '2026-01-05', value_sgd: '1040', gross_change_sgd: '5', adjusted_change_sgd: '5' },
    { date: '2026-01-06', value_sgd: '1050', gross_change_sgd: '10', adjusted_change_sgd: '10' },
  ];
  const plot = { innerHTML: '' }, tooltip = { hidden: true, style: {}, innerHTML: '' };
  const projectionButton = { classList: { toggle() {} }, setAttribute() {}, disabled: false };
  const root = {
    dataset: { account: 'broker' },
    querySelector(selector) { return selector === '.chart-plot' ? plot : selector === '.chart-tooltip' ? tooltip : selector === '[data-action="chart-projection"]' ? projectionButton : null; },
    querySelectorAll() { return []; },
  };
  const cursor = { removeAttribute() {}, setAttribute() {} }, hoverPoint = { setAttribute() {}, removeAttribute() {} };
  const svg = { getBoundingClientRect: () => ({ left: 0, width: 800 }), querySelector(selector) {
    return selector === '.chart-cursor' ? cursor : selector === '.chart-hover-point' ? hoverPoint : null;
  } };
  global.document = { querySelector: () => root };
  global.CSS = { escape: value => value };
  const chartState = {
    broker: { period: 'all', metric: 'gross_change_sgd', start: 0, end: rows.length - 1, showProjections: false },
    noMovement: { period: 'all', metric: 'adjusted_change_sgd', start: 0, end: 3, showProjections: false },
  };
  const pages = createDashboardPages({
    $: () => ({ textContent: '' }), esc: value => String(value), fmt: value => Number(value).toFixed(2),
    labels: {}, colors: {}, chartState, badge() {}, empty: () => '', historyTable: () => '', latestTransactionsFirst: () => [],
  });
  pages.setData({ portfolio_history: { broker: rows, noMovement: rows.slice(0, 4).map(row => ({ ...row, adjusted_change_sgd: null })) }, portfolio_deposits: {} });
  pages.drawBrokerageChart('broker');
  assert.equal((plot.innerHTML.match(/class="chart-line chart-line-actual"/g) || []).length, 2,
    'null movement observations split the line into two valid segments');
  assert.equal((plot.innerHTML.match(/class="chart-point" cx=/g) || []).length, 4,
    'null movement observations do not produce zero-valued point markers');
  assert(plot.innerHTML.includes('Changes are measured since the previous available valuation'));
  assert(plot.innerHTML.includes('Missing movement observations are left blank'));

  pages.chartHover({ clientX: 497, target: { closest: selector => selector === '.brokerage-chart' ? root : selector === 'svg' ? svg : null } });
  assert(tooltip.innerHTML.includes('Value movement: Unavailable'));
  assert(!tooltip.innerHTML.includes('SGD 0.00'));

  root.dataset.account = 'noMovement';
  pages.drawBrokerageChart('noMovement');
  assert(plot.innerHTML.includes('Investment movement is unavailable for this period'));
  assert.equal((plot.innerHTML.match(/class="chart-point" cx=/g) || []).length, 0);
  console.log('PASS: nullable brokerage movements render as gaps and unavailable hover values');
})().catch(error => { console.error(error); process.exitCode = 1; });
