const assert = require('assert');

(async () => {
  const { createBrokerageChartController } = await import('../web/features/dashboard/brokerage-chart-controller.js');
  const rows = [{ date: '2026-01-01' }, { date: '2026-02-01' }, { date: '2026-03-01' }, { date: '2026-04-01' }];
  const accountRoot = { dataset: { account: 'broker' }, querySelector: () => ({ getBoundingClientRect: () => ({ left: 100, width: 800 }) }) };
  const listeners = new Map();
  const documentRef = {
    addEventListener(type, listener, options) { listeners.set(type, { listener, options }); },
    querySelectorAll: () => [{ dataset: { account: 'broker' } }],
  };
  const draws = [], pages = {
    historyRange(_rows, period) { return period === '1m' ? [3, 3] : [0, 3]; },
    drawBrokerageChart(accountId) { draws.push(accountId); },
    chartHover(event) { this.hovered = event; },
  };
  const controller = createBrokerageChartController({
    getData: () => ({ portfolio_history: { broker: rows } }),
    getDashboardPages: () => pages,
    documentRef,
  });
  controller.state.broker = { period: '3m', metric: 'value_sgd', start: 1, end: 2, showProjections: true };
  controller.attach();
  controller.attach();
  assert.deepEqual([...listeners.keys()].sort(), ['pointermove', 'wheel'], 'chart listeners attach once');
  assert.equal(listeners.get('wheel').options.passive, false);

  const button = action => ({ dataset: { action, value: '1m' }, closest: () => accountRoot });
  assert.equal(controller.handleAction(button('chart-period')), true);
  assert.deepEqual([controller.state.broker.start, controller.state.broker.end], [3, 3]);
  assert.equal(controller.state.broker.period, '1m');
  controller.handleAction(button('chart-metric'));
  assert.equal(controller.state.broker.metric, '1m');
  controller.handleAction(button('chart-projection'));
  assert.equal(controller.state.broker.showProjections, false);
  assert.equal(controller.handleAction(button('unrelated')), false);
  assert.deepEqual(draws, ['broker', 'broker', 'broker']);

  const pointerEvent = { target: { matches: selector => selector === '[data-action="chart-hover"]' } };
  listeners.get('pointermove').listener(pointerEvent);
  assert.equal(pages.hovered, pointerEvent);

  let prevented = false;
  const wheelEvent = { target: { closest: selector => selector === '.brokerage-chart' ? accountRoot : null }, deltaY: 100, clientX: 500,
    preventDefault() { prevented = true; } };
  listeners.get('wheel').listener(wheelEvent);
  assert.equal(prevented, true);
  assert.deepEqual([controller.state.broker.start, controller.state.broker.end], [2, 3]);
  assert.equal(controller.state.broker.period, 'custom');
  assert.equal(draws.at(-1), 'broker');

  controller.drawAll();
  assert.equal(draws.at(-1), 'broker');
  console.log('PASS: brokerage chart state, action dispatch, hover, zoom, and drawing live in the chart controller');
})().catch(error => { console.error(error); process.exitCode = 1; });
