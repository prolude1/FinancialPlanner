const assert = require('node:assert/strict');
(async () => {
  const {createQuarterlyResultsPanel} = await import('../web/features/stocks/quarterly-results.js');
  const esc = value => String(value ?? '').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const panel = createQuarterlyResultsPanel({esc});
  const html = panel({currency:'USD',coverage:{complete:false,returned_quarters:1,requested_quarters:8},
    cache:{stale:true,fetched_at:'2026-10-12'},quarters:[{period_end:'2025-06-30',revenue:'120',
      profit_after_tax:null,free_cash_flow:'0',report_date:null,
      year_over_year:{revenue:{percent:'20'},free_cash_flow:{reason:'non_positive_base'}}}]});
  assert(html.includes('+20.00% YoY'));
  assert(html.includes('USD 0'));
  assert(html.includes('1 of 8'));
  assert(html.includes('Report date: Unavailable'));
  assert(html.includes('stale quarterly'));
  assert(html.includes('zero or negative'));
  assert(panel(null,'<error>').includes('&lt;error&gt;'));
  assert(panel({supported:false}).includes('do not apply to ETFs'));
  console.log('PASS: quarterly results rendering');
})().catch(error => {console.error(error);process.exitCode=1;});
