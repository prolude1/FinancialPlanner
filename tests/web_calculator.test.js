const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const context = vm.createContext({console, URLSearchParams, AbortController, queueMicrotask: () => {}});
const sharedSource = fs.readFileSync('web/app.js', 'utf8').split("document.addEventListener('click'")[0];
vm.runInContext(fs.readFileSync('web/calculator.js', 'utf8'), context);
vm.runInContext(sharedSource, context);

assert.equal(vm.runInContext(`contributionWindowSummary({cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan'})`, context), 'Contributions: entire plan');
assert.equal(vm.runInContext(`contributionWindowSummary({cashflow_start_years:'3',cashflow_start_remainder_months:'0',cashflow_end_mode:'custom',cashflow_end_years:'8',cashflow_end_remainder_months:'0'})`, context), 'Contributions: Year 3–Year 8');

const defaultPayload = vm.runInContext(`cashflowPayload({cashflow:'100',cashflow_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan'})`, context);
assert.equal(defaultPayload.cashflow_start_month, 0);
assert(!Object.hasOwn(defaultPayload, 'cashflow_duration_months'));

const customPayload = vm.runInContext(`cashflowPayload({cashflow:'100',cashflow_frequency:'monthly',cashflow_start_years:'3',cashflow_start_remainder_months:'0',cashflow_end_mode:'custom',cashflow_end_years:'8',cashflow_end_remainder_months:'0'})`, context);
assert.equal(customPayload.cashflow_start_month, 36);
assert.equal(customPayload.cashflow_duration_months, 60);

function section(values) {
  const error = {textContent: ''};
  return {
    error,
    querySelectorAll: () => Object.entries(values).map(([name, value]) => ({name, value})),
    querySelector: selector => selector === '.cashflow-window-error' ? error : null,
  };
}
function form(sections, years=10, months=0) {
  return {
    dataset: {mode: sections.length > 1 ? 'multiple' : 'single'},
    elements: {duration_years:{value:String(years)}, duration_months:{value:String(months)}},
    querySelectorAll: selector => selector === '.calculator-stream' ? sections : sections[0].querySelectorAll(),
    querySelector: sections[0].querySelector,
  };
}
const base={cashflow_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan'};
let first=section({...base}), second=section({...base,cashflow_end_mode:'custom',cashflow_end_years:'2',cashflow_end_remainder_months:'0'});
context.testForm=form([first,second]);
assert.equal(vm.runInContext('validateContributionWindows(testForm)', context), true);
assert.equal(first.error.textContent, '');
assert.equal(second.error.textContent, '');

first=section({...base,cashflow_end_mode:'custom',cashflow_start_years:'5',cashflow_end_years:'4',cashflow_end_remainder_months:'0'});
context.testForm=form([first]);
assert.equal(vm.runInContext('validateContributionWindows(testForm)', context), false);
assert(first.error.textContent.includes('after the start'));

first=section({...base,cashflow_frequency:'quarterly',cashflow_start_remainder_months:'1'});
context.testForm=form([first]);
assert.equal(vm.runInContext('validateContributionWindows(testForm)', context), false);
assert(first.error.textContent.includes('quarterly'));

first=section({...base}), second=section({...base,cashflow_end_mode:'custom',cashflow_end_years:'12',cashflow_end_remainder_months:'0'});
context.testForm=form([first,second]);
assert.equal(vm.runInContext('validateContributionWindows(testForm)', context), false);
assert.equal(first.error.textContent, '');
assert(second.error.textContent.includes('overall plan'));

console.log('PASS: Calculator contribution-window defaults, payloads, validation, and stream isolation');
