const assert = require('assert');

(async () => {
  const { BROKERAGE_RETURN_SCENARIOS, brokerageProjectionDates, brokerageProjectionSeries, elapsedCalendarYears } =
    await import('../web/features/dashboard/brokerage-projection.js');
  const { createDashboardPages } = await import('../web/features/dashboard/dashboard-pages.js');

  assert.equal(elapsedCalendarYears('2023-01-01', '2025-01-01'), 2);
  const rows = [
    { date: '2025-01-01' },
    { date: '2026-01-01' },
  ];
  const deposits = [
    { date: '2023-01-01', amount_sgd: '5000' },
    { date: '2026-01-01', amount_sgd: '2000' },
  ];
  const series = brokerageProjectionSeries(rows, deposits);
  assert.equal(series.available, true);
  assert.equal(series.complete, true);
  assert.deepEqual(BROKERAGE_RETURN_SCENARIOS, [0.05, 0.10, 0.15]);
  assert.equal(series.values[0].scenarios[1], 5000 * 1.1 ** 2);
  assert.equal(series.values[1].scenarios[1], 5000 * 1.1 ** 3 + 2000);
  const partial = brokerageProjectionSeries(rows, [
    { date: '2023-01-01', amount_sgd: '5000' },
    { date: '2025-01-01', amount_sgd: '250', missing_fx_count: 2, stale_fx_count: 3,
      stale_fx_last_success_at: '2025-01-03T09:00:00Z' },
  ]);
  assert.equal(partial.available, true);
  assert.equal(partial.complete, false);
  assert.equal(partial.missingFxCount, 2);
  assert.deepEqual(partial.missingFxDates, ['2025-01-01']);
  assert.equal(partial.staleFxCount, 3);
  assert.deepEqual(partial.staleFxDates, ['2025-01-01']);
  assert.deepEqual(partial.staleFxLastSuccessAt, ['2025-01-03T09:00:00Z']);
  assert.equal(partial.values.length, rows.length, 'valid partial projections remain available across the whole history');
  assert.equal(partial.values[0].scenarios[1], 5000 * 1.1 ** 2 + 250);
  assert.equal(partial.values[1].scenarios[1], 5000 * 1.1 ** 3 + 250 * 1.1);

  const legacyMissing = brokerageProjectionSeries(rows, [{ date: '2025-01-01', amount_sgd: null }]);
  assert.equal(legacyMissing.available, true);
  assert.equal(legacyMissing.complete, false);
  assert.equal(legacyMissing.missingFxCount, 1, 'legacy null amounts still produce an FX warning');
  assert.equal(legacyMissing.values[0].scenarios[0], 0);

  const staleValuations = [{ date: '2026-01-01', value_sgd: '10000' }, { date: '2026-03-01', value_sgd: '11000' }];
  const recentDeposits = [{ date: '2026-04-01', amount_sgd: '500', stale_fx_count: 2,
    stale_fx_last_success_at: '2026-04-10T09:00:00Z' }];
  const timeline = brokerageProjectionDates(staleValuations, recentDeposits, { extendAfterLast: true });
  assert.deepEqual(timeline.map(point => point.date), ['2026-01-01', '2026-03-01', '2026-04-01']);
  assert.equal(Object.hasOwn(timeline.at(-1), 'value_sgd'), false, 'projection-only dates carry no fabricated valuation');
  const extended = brokerageProjectionSeries(timeline, recentDeposits);
  assert.equal(extended.values.at(-1).scenarios[1], 500);
  assert.equal(brokerageProjectionDates(staleValuations, recentDeposits).at(-1).date, '2026-03-01',
    'deposit dates only extend the right edge when the visible range reaches the latest valuation');
  assert.deepEqual(brokerageProjectionDates([], recentDeposits, { throughDate: '2026-04-15' }).map(point => point.date),
    ['2026-04-01', '2026-04-15'], 'deposit dates establish a projection timeline when no valuations exist');

  const chartState = { broker: { period: '3m', metric: 'value_sgd', start: 0, end: 1, showProjections: true } };
  const plot = { innerHTML: '' };
  const projectionButton = { classList: { toggle() {} }, setAttribute() {}, disabled: false };
  const cursor = { attrs: { hidden: '' }, removeAttribute(name) { delete this.attrs[name]; }, setAttribute(name, value) { this.attrs[name] = value; } };
  const hoverPoint = { attrs: { hidden: '' }, removeAttribute(name) { delete this.attrs[name]; }, setAttribute(name, value) { this.attrs[name] = value; } };
  const tooltip = { hidden: true, style: {}, innerHTML: '' };
  const svg = { getBoundingClientRect: () => ({ left: 0, width: 800 }), querySelector(selector) {
    return selector === '.chart-cursor' ? cursor : selector === '.chart-hover-point' ? hoverPoint : null;
  } };
  const chartRoot = {
    dataset: { account: 'broker' },
    querySelector(selector) { return selector === '.chart-plot' ? plot : selector === '[data-action="chart-projection"]' ? projectionButton : selector === '.chart-tooltip' ? tooltip : null; },
    querySelectorAll() { return []; },
  };
  global.document = { querySelector: () => chartRoot };
  global.CSS = { escape: value => value };
  const pages = createDashboardPages({ $: () => ({ textContent: '' }), esc: value => String(value), fmt: value => Number(value).toFixed(2), labels: {}, colors: {}, chartState,
    badge() {}, empty() {}, historyTable() {}, latestTransactionsFirst() {} });
  pages.setData({ portfolio_history: { broker: staleValuations }, portfolio_deposits: { broker: recentDeposits } });
  pages.drawBrokerageChart('broker');
  assert(plot.innerHTML.includes('2026-04-01'), 'the chart axis reaches a deposit newer than its last valuation');
  assert(plot.innerHTML.includes('Account value is available through 2026-03-01'));
  assert(plot.innerHTML.includes('no account valuation is available'));
  assert(plot.innerHTML.includes('FX refresh unavailable; using last successfully retrieved historical rates'));
  assert(plot.innerHTML.includes('Last successful retrieval:'));
  assert(plot.innerHTML.includes('10 Apr 2026'));
  const actualPoints = plot.innerHTML.match(/chart-line-actual" points="([^"]*)"/)[1].split(' ');
  assert(Number(actualPoints.at(-1).split(',')[0]) < 782, 'the actual value line ends before the projection-only date');
  pages.chartHover({ clientX: 800, target: { closest: selector => selector === '.brokerage-chart' ? chartRoot : selector === 'svg' ? svg : null } });
  assert(tooltip.innerHTML.includes('Account valuation unavailable for this date'));
  assert(tooltip.innerHTML.includes('5%: SGD 500.00'));

  chartState.newBroker = { period: '3m', metric: 'gross_change_sgd', start: 0, end: 0, showProjections: false };
  chartRoot.dataset.account = 'newBroker';
  pages.setData({ as_of: '2026-04-15', portfolio_history: { newBroker: [] }, portfolio_deposits: { newBroker: recentDeposits } });
  pages.drawBrokerageChart('newBroker');
  assert(plot.innerHTML.includes('2026-04-15'));
  assert(plot.innerHTML.includes('No account valuation history is available'));
  assert(plot.innerHTML.includes('chart-projection-line'), 'deposit projections remain visible without valuation history');
  assert.equal(chartState.newBroker.showProjections, true);
  assert.equal(projectionButton.disabled, true, 'the projection toggle cannot hide the only available chart data');
  assert(!plot.innerHTML.includes('chart-line-actual'), 'a projection-only chart draws no synthetic valuation line');
  pages.setData({ as_of: '2026-04-15', accounts: [{ id: 'newBroker', name: 'New account', type: 'brokerage', sgd: '0', complete: false, cash: [], holdings: [] }],
    history: [], portfolio_history: { newBroker: [] }, portfolio_deposits: { newBroker: recentDeposits } });
  const noHistoryPage = pages.accountsPage('newBroker');
  assert.equal(chartState.newBroker.period, 'all');
  assert(noHistoryPage.includes('data-action="chart-period" data-value="1m" disabled'));
  assert(noHistoryPage.includes('data-action="chart-period" data-value="3m" disabled'));
  assert(noHistoryPage.includes('data-action="chart-period" data-value="1y" disabled'));
  assert(noHistoryPage.includes('class="active" data-action="chart-period" data-value="all" disabled'));
  pages.chartHover({ clientX: 800, target: { closest: selector => selector === '.brokerage-chart' ? chartRoot : selector === 'svg' ? svg : null } });
  assert(tooltip.innerHTML.includes('Account valuation unavailable for this date'));
  assert(tooltip.innerHTML.includes('5%: SGD'));

  console.log('PASS: partial FX coverage remains visible and newer deposits extend only the projection timeline');
})().catch(error => { console.error(error); process.exitCode = 1; });
