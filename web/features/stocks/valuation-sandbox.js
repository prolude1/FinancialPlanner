/** Manual P/E and per-share DCF scenarios for a selected security. */
const METHODS = ['pe', 'dcf'];

export function createValuationSandbox({ api, esc, onUpdate = () => {}, documentRef = globalThis.document }) {
  const bySecurity = new Map();
  const requests = new Map();
  let activeSecurityId = '';
  let generation = 0;
  let attached = false;

  function methodState() {
    return { inputs: {}, loading: false, error: '', result: null };
  }

  function ensure(securityId, currency = '') {
    if (!bySecurity.has(securityId)) {
      bySecurity.set(securityId, {
        pe: { ...methodState(), inputs: { currency: currency || '', eps: '', multiples: '10, 15, 20', reference_price: '' } },
        dcf: { ...methodState(), inputs: { currency: currency || '', free_cash_flow_per_share: '', annual_growth_percent: '5', discount_rate_percent: '10', terminal_growth_percent: '2', years: '10', reference_price: '' } },
      });
    }
    const state = bySecurity.get(securityId);
    if (currency) {
      for (const method of METHODS) if (!state[method].inputs.currency) state[method].inputs.currency = currency;
    }
    return state;
  }

  function activate(securityId, currency = '') {
    if (activeSecurityId !== securityId) {
      generation++;
      const previous = bySecurity.get(activeSecurityId);
      for (const [method, request] of requests) {
        request.controller.abort();
        if (previous) previous[method].loading = false;
      }
      requests.clear();
      activeSecurityId = securityId;
    }
    return ensure(securityId, currency);
  }

  function number(value) {
    if (value == null || String(value).trim() === '') return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }

  function escInput(value) {
    return esc(value).replace(/`/g, '&#96;');
  }

  function field(securityId, method, name, label, value, { type = 'number', step = 'any', min = null, max = null, placeholder = '' } = {}) {
    return `<label>${label}<input name="${name}" data-assumption="${name}" type="${type}" value="${escInput(value)}" ${step ? `step="${step}"` : ''} ${min == null ? '' : `min="${min}"`} ${max == null ? '' : `max="${max}"`} ${placeholder ? `placeholder="${escInput(placeholder)}"` : ''} inputmode="${type === 'text' ? 'text' : 'decimal'}" autocomplete="off"></label>`;
  }

  function renderPe(securityId, currency, referencePrice, state) {
    const method = state.pe, result = method.result;
    const outdated = result && method.resultInputs !== JSON.stringify(method.inputs);
    const scenarios = result?.scenarios || [];
    return `<section class="panel valuation-method" aria-labelledby="valuation-pe-title"><h3 id="valuation-pe-title">P/E scenarios</h3><p class="section-note">Enter your own annual earnings-per-share figure. The editable example multiples below are assumptions, not forecasts or provider estimates.</p>
      <form class="stock-valuation-form" data-security="${esc(securityId)}" data-method="pe"><label>Assumption currency<input data-assumption="currency" type="text" value="${escInput(method.inputs.currency || currency)}" maxlength="3" aria-label="P/E assumption currency" autocomplete="off"></label>
      ${field(securityId, 'pe', 'eps', `Annual earnings per share (${esc(method.inputs.currency || currency || 'currency not set')})`, method.inputs.eps)}
      ${field(securityId, 'pe', 'multiples', 'Editable example P/E multiples (comma-separated)', method.inputs.multiples, { type: 'text', step: '', placeholder: '10, 15, 20' })}
      ${field(securityId, 'pe', 'reference_price', `Optional reference price (${esc(method.inputs.currency || currency || 'currency not set')})`, method.inputs.reference_price, { min: '0.0000000001', placeholder: referencePrice == null ? '' : String(referencePrice) })}
      <button type="submit" ${state.pe.loading ? 'disabled' : ''}>${state.pe.loading ? 'Calculating…' : 'Calculate P/E scenarios'}</button></form>
      ${state.pe.error ? `<p class="error" role="alert">${esc(state.pe.error)}</p>` : ''}${outdated ? '<p class="notice soft" role="status">Inputs have changed since these results were calculated. Recalculate to update the scenarios.</p>' : ''}${scenarios.length ? `<p class="section-note">Calculated with annual EPS ${money(result.assumptions?.eps, result.currency)} and P/E multiples ${result.assumptions?.multiples?.map(esc).join(', ')}.</p><div class="table-wrap valuation-results"><table><caption>Manual P/E scenario results · ${esc(result.currency || method.inputs.currency || currency || 'Currency not reported')}</caption><thead><tr><th scope="col">P/E multiple</th><th scope="col" class="num">Implied value per share</th><th scope="col" class="num">Reference price</th><th scope="col" class="num">Difference</th></tr></thead><tbody>${scenarios.map(row => `<tr><td>${esc(row.multiple)}${row.status === 'not_meaningful' ? '<small>Not meaningful with non-positive EPS</small>' : ''}</td><td class="num">${money(row.value_per_share, result.currency || method.inputs.currency || currency)}</td><td class="num">${money(result.assumptions?.reference_price, result.currency || method.inputs.currency || currency)}</td><td class="num">${row.comparison_percent == null ? 'Unavailable' : `${Number(row.comparison_percent).toFixed(2)}%`}</td></tr>`).join('')}</tbody></table></div><p class="section-note">${esc(result.source || 'Results use only user-supplied assumptions.')}</p>` : ''}</section>`;
  }

  function renderDcf(securityId, currency, referencePrice, state) {
    const method = state.dcf, result = method.result;
    const outdated = result && method.resultInputs !== JSON.stringify(method.inputs);
    const schedule = result?.schedule || [];
    return `<section class="panel valuation-method" aria-labelledby="valuation-dcf-title"><h3 id="valuation-dcf-title">Discounted cash-flow scenarios</h3><p class="section-note">Enter annual equity cash flow per share: cash available to shareholders. The backend discounts this manual input; it does not divide company free cash flow by shares or infer debt, cash, share count, or total equity value.</p>
      <form class="stock-valuation-form" data-security="${esc(securityId)}" data-method="dcf"><label>Assumption currency<input data-assumption="currency" type="text" value="${escInput(method.inputs.currency || currency)}" maxlength="3" aria-label="DCF assumption currency" autocomplete="off"></label>
      ${field(securityId, 'dcf', 'free_cash_flow_per_share', `Annual equity cash flow per share (${esc(method.inputs.currency || currency || 'currency not set')})`, method.inputs.free_cash_flow_per_share)}
      ${field(securityId, 'dcf', 'annual_growth_percent', 'Editable example annual growth rate (%)', method.inputs.annual_growth_percent)}
      ${field(securityId, 'dcf', 'discount_rate_percent', 'Editable example discount rate (%)', method.inputs.discount_rate_percent)}
      ${field(securityId, 'dcf', 'terminal_growth_percent', 'Editable example terminal growth rate (%)', method.inputs.terminal_growth_percent)}
      ${field(securityId, 'dcf', 'years', 'Projection years (1–30)', method.inputs.years, { step: '1', min: '1', max: '30' })}
      ${field(securityId, 'dcf', 'reference_price', `Optional reference price (${esc(method.inputs.currency || currency || 'currency not set')})`, method.inputs.reference_price, { min: '0.0000000001', placeholder: referencePrice == null ? '' : String(referencePrice) })}
      <button type="submit" ${state.dcf.loading ? 'disabled' : ''}>${state.dcf.loading ? 'Calculating…' : 'Calculate DCF scenario'}</button></form>
      ${state.dcf.error ? `<p class="error" role="alert">${esc(state.dcf.error)}</p>` : ''}${outdated ? '<p class="notice soft" role="status">Inputs have changed since these results were calculated. Recalculate to update the scenario.</p>' : ''}${result ? `<div class="valuation-dcf-results">${result.status === 'not_meaningful' ? '<p role="status">No per-share value is calculated for these assumptions.</p>' : ''}<p><strong>Value per share:</strong> ${money(result.value_per_share, result.currency || method.inputs.currency || currency)}</p><p><strong>Terminal value per share:</strong> ${money(result.terminal_value_per_share, result.currency || method.inputs.currency || currency)}</p><p><strong>Present value of terminal value:</strong> ${money(result.terminal_value_present_per_share, result.currency || method.inputs.currency || currency)}</p>${result.comparison_percent == null ? '' : `<p><strong>Difference from reference price:</strong> ${Number(result.comparison_percent).toFixed(2)}%</p>`}${schedule.length ? `<div class="table-wrap valuation-results"><table><caption>Discounted per-share cash-flow schedule · ${esc(result.currency || method.inputs.currency || currency || 'Currency not reported')}</caption><thead><tr><th scope="col">Year</th><th scope="col" class="num">Equity cash flow per share</th><th scope="col" class="num">Discounted cash flow per share</th></tr></thead><tbody>${schedule.map(row => `<tr><td>${esc(row.year)}</td><td class="num">${money(row.cash_flow_per_share, result.currency || method.inputs.currency || currency)}</td><td class="num">${money(row.discounted_cash_flow_per_share, result.currency || method.inputs.currency || currency)}</td></tr>`).join('')}</tbody></table></div>` : ''}<p class="section-note">${esc(result.source || 'Results use only user-supplied assumptions.')}</p></div>` : ''}</section>`;
  }

  function money(value, currency) {
    return value == null ? 'Unavailable' : `${esc(currency || 'Currency not reported')} ${Number(value).toLocaleString('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  }

  function panel(securityId, currency = '', referencePrice = null) {
    const state = ensure(securityId, currency);
    return `<section class="panel stock-valuation-sandbox" data-security="${esc(securityId)}"><div class="panel-head"><h2>Valuation sandbox</h2></div><p class="section-note">Exploratory per-share scenarios using your assumptions. Outputs are uncertain and are not recommendations or predicted prices. Every amount uses the currency selected separately for each method.</p><div class="valuation-method-grid">${renderPe(securityId, currency, referencePrice, state)}${renderDcf(securityId, currency, referencePrice, state)}</div></section>`;
  }

  function parseRequest(method, state) {
    const inputs = state[method].inputs;
    const currency = String(inputs.currency || '').trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency)) return { error: 'Enter a three-letter assumption currency.' };
    const reference = number(inputs.reference_price);
    if (inputs.reference_price !== '' && (reference == null || reference <= 0)) return { error: 'Reference price must be greater than zero.' };
    if (method === 'pe') {
      const eps = number(inputs.eps);
      const multiples = String(inputs.multiples || '').split(',').map(item => item.trim()).filter(Boolean).map(Number);
      if (eps == null) return { error: 'Enter a valid earnings-per-share assumption.' };
      if (!multiples.length || multiples.length > 12 || multiples.some(value => !Number.isFinite(value) || value < 0 || value > 1000)) return { error: 'Enter 1 to 12 P/E multiples from 0 to 1,000, separated by commas.' };
      return { body: { method, currency, eps, multiples, reference_price: reference } };
    }
    const freeCashFlow = number(inputs.free_cash_flow_per_share);
    const annualGrowth = number(inputs.annual_growth_percent);
    const discountRate = number(inputs.discount_rate_percent);
    const terminalGrowth = number(inputs.terminal_growth_percent);
    const years = number(inputs.years);
    if (freeCashFlow == null || annualGrowth == null || discountRate == null || terminalGrowth == null) return { error: 'Enter valid numeric assumptions for annual equity cash flow per share and growth rates.' };
    if (!Number.isInteger(years) || years < 1 || years > 30) return { error: 'Projection years must be an integer from 1 to 30.' };
    if (annualGrowth <= -100 || annualGrowth > 100 || discountRate <= 0 || discountRate > 100 || terminalGrowth <= -100 || terminalGrowth > 100) return { error: 'Growth and discount rates must be within the supported percentage range.' };
    if (discountRate <= terminalGrowth) return { error: 'Discount rate must be greater than terminal growth.' };
    return { body: { method, currency, free_cash_flow_per_share: freeCashFlow, annual_growth_percent: annualGrowth, discount_rate_percent: discountRate, terminal_growth_percent: terminalGrowth, years, reference_price: reference } };
  }

  async function run(securityId, method) {
    if (securityId !== activeSecurityId || !METHODS.includes(method)) return;
    const state = ensure(securityId);
    const methodData = state[method];
    const parsed = parseRequest(method, state);
    if (parsed.error) {
      requests.get(method)?.controller.abort();
      requests.delete(method);
      methodData.loading = false;
      methodData.error = parsed.error;
      onUpdate(securityId, panel(securityId));
      return;
    }
    requests.get(method)?.controller.abort();
    const request = { generation, controller: new AbortController(), inputs: JSON.stringify(methodData.inputs) };
    requests.set(method, request);
    methodData.loading = true;
    methodData.error = '';
    onUpdate(securityId, panel(securityId));
    try {
      const result = await api(`/stocks/${encodeURIComponent(securityId)}/valuation`, {
        method: 'POST', body: JSON.stringify(parsed.body), signal: request.controller.signal,
      });
      if (request.generation !== generation || securityId !== activeSecurityId || requests.get(method) !== request) return;
      if (result?.security_id && result.security_id !== securityId) throw new Error('The valuation response was for a different security.');
      methodData.result = result;
      methodData.resultInputs = request.inputs;
    } catch (error) {
      if (request.generation !== generation || securityId !== activeSecurityId || error?.name === 'AbortError') return;
      methodData.error = error?.message || 'Unable to calculate this scenario.';
    } finally {
      if (request.generation === generation && securityId === activeSecurityId && requests.get(method) === request) {
        methodData.loading = false;
        requests.delete(method);
        onUpdate(securityId, panel(securityId));
      }
    }
  }

  function updateInput(form, input) {
    const securityId = form.dataset.security, method = form.dataset.method;
    if (!securityId || !METHODS.includes(method)) return;
    const state = ensure(securityId);
    const assumption = input.dataset.assumption;
    if (assumption === 'currency') state[method].inputs.currency = input.value;
      else if (Object.hasOwn(state[method].inputs, assumption)) state[method].inputs[assumption] = input.value;
  }

  function attach(target = documentRef) {
    if (!target || attached) return;
    attached = true;
    target.addEventListener('input', event => {
      const form = event.target.closest?.('.stock-valuation-form');
      if (form) updateInput(form, event.target);
    });
    target.addEventListener('change', event => {
      const form = event.target.closest?.('.stock-valuation-form');
      if (form) updateInput(form, event.target);
    });
    target.addEventListener('submit', event => {
      const form = event.target.closest?.('.stock-valuation-form');
      if (!form) return;
      event.preventDefault();
      const method = form.dataset.method;
      const inputs = form.querySelectorAll('[data-assumption]');
      inputs.forEach(input => updateInput(form, input));
      run(form.dataset.security, method);
    });
  }

  function invalidate() {
    generation++;
    for (const request of requests.values()) request.controller.abort();
    requests.clear();
    for (const state of bySecurity.values()) {
      state.pe.loading = false;
      state.dcf.loading = false;
    }
    bySecurity.clear();
    activeSecurityId = '';
  }

  return { panel, activate, attach, run, invalidate, getState: securityId => bySecurity.get(securityId) };
}
