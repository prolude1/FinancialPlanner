const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const context = vm.createContext({console, URLSearchParams, AbortController, queueMicrotask: () => {}});
const sharedSource = fs.readFileSync('web/app.js', 'utf8').split("document.addEventListener('click'")[0];
vm.runInContext(fs.readFileSync('web/stocks.js', 'utf8'), context);
vm.runInContext(sharedSource, context);

vm.runInContext(`stockState={query:'ABC',exchange:'',results:[
  {security_id:'NASDAQ:ABC',symbol:'ABC',exchange:'NASDAQ',name:'One',currency:'USD'},
  {security_id:'SGX:ABC',symbol:'ABC',exchange:'SGX',name:'Two',currency:'SGD'}
],searching:false,error:'',request:0,selected:'',range:'1y',overview:null,candles:null,financials:null,
highlight:1,suggestionsOpen:true,candleIndex:null}`, context);
const search = vm.runInContext('stockSearchResults()', context);
assert(search.includes('stock-option-0'));
assert(search.includes('stock-option-1'));
assert(search.includes('aria-selected="true"'));

const fundamentals = vm.runInContext(`fundamentalsTable({currency:'USD',coverage:{
  complete:false,returned_years:1,requested_years:5},fiscal_years:[{fiscal_year:2025,
  period_end:'2025-12-31',revenue:null,free_cash_flow:'100',profit_after_tax:null,
  ebitda:'50',ebita:null}]})`, context);
assert(fundamentals.includes('>-</td>'));
assert(!fundamentals.includes('EBITA</th>'));

const chart = vm.runInContext(`candlestickChart({security_id:'NASDAQ:ABC',currency:'USD',
  as_of:'2026-09-23',candles:[{date:'2026-09-22',open:'10',high:'12',low:'9',close:'11',volume:100},
  {date:'2026-09-23',open:'11',high:'13',low:'10',close:'12',volume:120}]})`, context);
assert(chart.includes('Daily candlestick chart'));
assert(chart.includes('Completed daily sessions only'));
assert(chart.includes('stock-candle-hover'));
assert(chart.includes('aria-live="polite"'));
assert(fs.readFileSync('web/stocks.js','utf8').includes('candle-tooltip-close'));
const stockCss=fs.readFileSync('web/stocks.css','utf8');
const compactStockCss=stockCss.replace(/\s/g,'');
assert(compactStockCss.includes('width:260px;max-width:calc(100%-20px)'));
assert(compactStockCss.includes('font-size:16px'));
assert(compactStockCss.includes('width:calc(100vw-80px);max-width:280px'));

// Responsive coordinate mapping snaps to actual array entries, not calendar interpolation.
assert.equal(vm.runInContext('nearestCandleIndex(100,100,600,3)', context), 0);
assert.equal(vm.runInContext('nearestCandleIndex(400,100,600,3)', context), 1);
assert.equal(vm.runInContext('nearestCandleIndex(700,100,600,3)', context), 2);
const missingDates = [{date:'2026-09-18'},{date:'2026-09-21'},{date:'2026-09-24'}];
assert.equal(missingDates[vm.runInContext('nearestCandleIndex(400,100,600,3)', context)].date, '2026-09-21');
assert.equal(vm.runInContext('clampTooltipLeft(2,500)', context), 92);
assert.equal(vm.runInContext('clampTooltipLeft(498,500)', context), 408);
assert.equal(vm.runInContext(`nextCandleIndex(null,'ArrowRight',3)`, context), 0);
assert.equal(vm.runInContext(`nextCandleIndex(1,'ArrowRight',3)`, context), 2);
assert.equal(vm.runInContext(`nextCandleIndex(0,'ArrowLeft',3)`, context), 0);

// Search result updates replace only the list, preserving the live input and its caret.
const searchInput={value:'AA',selectionStart:2,selectionEnd:2,attrs:{},setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]}};
const exchange={value:''}, searchResults={innerHTML:''};
context.document={querySelector(selector){return selector==='#stock-search-input'?searchInput:selector==='#stock-exchange'?exchange:selector==='#stock-search-results'?searchResults:null}};
const searchPending=[];
context.searchDeferred=path=>new Promise(resolve=>searchPending.push({path,resolve}));
vm.runInContext(`api=(path)=>searchDeferred(path);stockState.query='AA';stockState.results=[];stockState.request=0`, context);
const searchOne=vm.runInContext('searchStocks()', context);
searchInput.value='AAPL';searchInput.selectionStart=4;searchInput.selectionEnd=4;
const searchTwo=vm.runInContext('searchStocks()', context);
searchPending[1].resolve({results:[{security_id:'NASDAQ:AAPL',symbol:'AAPL',exchange:'NASDAQ',name:'Apple',currency:'USD'}]});

const pending = [];
context.makeDeferred = path => new Promise(resolve => pending.push({path, resolve}));
vm.runInContext(`render=()=>{};api=(path)=>makeDeferred(path)`, context);
(async () => {
  await searchTwo;
  assert.strictEqual(context.document.querySelector('#stock-search-input'), searchInput);
  assert.equal(searchInput.selectionStart, 4);
  assert.equal(searchInput.selectionEnd, 4);
  searchPending[0].resolve({results:[{security_id:'NYSE:AA',symbol:'AA',exchange:'NYSE',name:'Old',currency:'USD'}]});
  await searchOne;
  assert.equal(vm.runInContext('stockState.results[0].security_id', context), 'NASDAQ:AAPL');

  const first = vm.runInContext(`loadStock('NASDAQ:OLD')`, context);
  const second = vm.runInContext(`loadStock('SGX:NEW')`, context);
  for (const item of pending.filter(item => item.path.includes('SGX%3ANEW'))) {
    item.resolve(item.path.includes('/overview') ? {security:{security_id:'SGX:NEW'},latest_price:{}} :
      item.path.includes('/candles') ? {candles:[]} : {fiscal_years:[]});
  }
  await second;
  assert.equal(vm.runInContext('stockState.overview.security.security_id', context), 'SGX:NEW');
  for (const item of pending.filter(item => item.path.includes('NASDAQ%3AOLD'))) {
    item.resolve(item.path.includes('/overview') ? {security:{security_id:'NASDAQ:OLD'},latest_price:{}} :
      item.path.includes('/candles') ? {candles:[]} : {fiscal_years:[]});
  }
  await first;
  assert.equal(vm.runInContext('stockState.selected', context), 'SGX:NEW');
  assert.equal(vm.runInContext('stockState.overview.security.security_id', context), 'SGX:NEW');
  console.log('PASS: Stocks UI rendering and stale-response protection');
})().catch(error => { console.error(error); process.exitCode = 1; });
