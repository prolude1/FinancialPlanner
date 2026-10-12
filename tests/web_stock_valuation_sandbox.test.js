const assert = require('assert');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

(async () => {
  const { createValuationSandbox } = await import('../web/features/stocks/valuation-sandbox.js');
  const listeners = new Map(), calls = [], updates = [];
  const module = createValuationSandbox({
    api: async (path, options) => {
      const call = { path, options, body: JSON.parse(options.body) };
      calls.push(call);
      if (call.body.method === 'pe') return { security_id: 'NASDAQ:ABC', method: 'pe', currency: 'SGD',
        assumptions: { eps: '4', multiples: ['10', '15'], reference_price: '50' },
        scenarios: [{ multiple: '10', status: 'calculated', value_per_share: '40', comparison_percent: '-20' },
          { multiple: '15', status: 'calculated', value_per_share: '60', comparison_percent: '20' }], source: 'User supplied assumptions; no recommendation' };
      return { security_id: 'NASDAQ:ABC', method: 'dcf', currency: 'USD', status: 'calculated', value_per_share: '33.50',
        assumptions: { free_cash_flow_per_share: '2', annual_growth_percent: '5', discount_rate_percent: '10', terminal_growth_percent: '2', years: 2, reference_price: null },
        schedule: [{ year: 1, cash_flow_per_share: '2.1', discounted_cash_flow_per_share: '1.909' }, { year: 2, cash_flow_per_share: '2.205', discounted_cash_flow_per_share: '1.822' }],
        terminal_value_per_share: '27.56', terminal_value_present_per_share: '22.77', comparison_percent: null, source: 'User supplied assumptions; no recommendation' };
    },
    esc: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]),
    documentRef: { addEventListener: (type, listener) => listeners.set(type, listener) },
    onUpdate: (id, html) => updates.push({ id, html }),
  });
  module.attach();
  module.activate('NASDAQ:ABC', 'USD');
  let html = module.panel('NASDAQ:ABC', 'USD', '48.25');
  assert(html.includes('P/E scenarios'));
  assert(html.includes('Discounted cash-flow scenarios'));
  assert(html.includes('Annual equity cash flow per share'));
  assert(html.includes('does not divide company free cash flow by shares'));
  assert(html.includes('Editable example annual growth rate'));

  const state = module.getState('NASDAQ:ABC');
  Object.assign(state.pe.inputs, { currency: 'sgd', eps: '4', multiples: '10, 15', reference_price: '50' });
  await module.run('NASDAQ:ABC', 'pe');
  assert.deepEqual(calls[0].body, { method: 'pe', currency: 'SGD', eps: 4, multiples: [10, 15], reference_price: 50 });
  html = module.panel('NASDAQ:ABC', 'USD', '48.25');
  assert(html.includes('SGD 40.00'));
  assert(html.includes('-20.00%'));
  assert(html.includes('Calculated with annual EPS SGD 4.00 and P/E multiples 10, 15'));
  state.pe.inputs.eps = '5';
  html = module.panel('NASDAQ:ABC', 'USD');
  assert(html.includes('Inputs have changed since these results were calculated'));
  assert(html.includes('value="5"'), 'manual inputs survive panel updates');

  Object.assign(state.dcf.inputs, { currency: 'USD', free_cash_flow_per_share: '2', annual_growth_percent: '5', discount_rate_percent: '10', terminal_growth_percent: '2', years: '2', reference_price: '' });
  await module.run('NASDAQ:ABC', 'dcf');
  assert.deepEqual(calls[1].body, { method: 'dcf', currency: 'USD', free_cash_flow_per_share: 2, annual_growth_percent: 5,
    discount_rate_percent: 10, terminal_growth_percent: 2, years: 2, reference_price: null });
  html = module.panel('NASDAQ:ABC', 'USD');
  assert(html.includes('Value per share:'));
  assert(html.includes('USD 33.50'));
  assert(html.includes('Discounted per-share cash-flow schedule'));
  assert(html.includes('USD 1.909'));

  const form = { dataset: { security: 'NASDAQ:ABC', method: 'pe' } };
  listeners.get('input')({ target: { value: 'EUR', dataset: { assumption: 'currency' }, closest: () => form } });
  assert.equal(state.pe.inputs.currency, 'EUR', 'currency input is saved against its method');
  assert.equal(state.dcf.inputs.currency, 'USD', 'P/E and DCF currencies remain independent');

  state.dcf.inputs.years = '31';
  await module.run('NASDAQ:ABC', 'dcf');
  assert.equal(calls.length, 2, 'invalid assumptions are rejected before making a request');
  assert(module.panel('NASDAQ:ABC').includes('Projection years must be an integer from 1 to 30'));

  const late = deferred();
  let lateCalls = 0;
  const races = createValuationSandbox({ api: (_path, options) => { lateCalls++; return late.promise; }, esc: String });
  races.activate('NYSE:OLD', 'USD');
  Object.assign(races.getState('NYSE:OLD').pe.inputs, { currency: 'USD', eps: '1', multiples: '10' });
  const pending = races.run('NYSE:OLD', 'pe');
  races.activate('SGX:NEW', 'SGD');
  assert.equal(lateCalls, 1);
  late.resolve({ security_id: 'NYSE:OLD', method: 'pe', currency: 'USD', scenarios: [{ multiple: '10', value_per_share: '10' }] });
  await pending;
  assert.equal(races.getState('NYSE:OLD').pe.result, null, 'late results cannot land after a security switch');
  races.invalidate();
  assert.equal(races.getState('NYSE:OLD'), undefined, 'logout clears per-user assumptions and results');
  console.log('PASS: manual P/E and DCF valuation requests, currency isolation, editable assumptions, and stale-response/logout handling');
})().catch(error => { console.error(error); process.exitCode = 1; });
