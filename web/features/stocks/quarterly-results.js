/* Quarterly results rendering is independent of search and price-chart state. */
export function createQuarterlyResultsPanel({esc}) {
  const metrics = [['revenue', 'Revenue'], ['profit_after_tax', 'Profit after tax'], ['free_cash_flow', 'Free cash flow']];
  function value(amount, currency) {
    return amount == null ? 'Unavailable' : `${esc(currency || 'Currency unavailable')} ${Number(amount).toLocaleString('en-SG', {maximumFractionDigits: 0})}`;
  }
  function comparison(change) {
    if (change?.percent == null) return change?.reason === 'non_positive_base'
      ? 'YoY unavailable: prior value was zero or negative' : 'YoY unavailable: missing comparable value';
    const percent = Number(change.percent);
    return `${percent > 0 ? '+' : ''}${percent.toFixed(2)}% YoY`;
  }
  return function quarterlyResultsPanel(payload, error) {
    const heading = '<h2>Quarterly results timeline</h2>';
    if (error) return `<section class="panel">${heading}<p>${esc(error)}</p><button class="small-button" data-action="stock-retry">Retry</button></section>`;
    if (payload?.supported === false) return `<section class="panel">${heading}<p>Corporate quarterly results do not apply to ETFs.</p></section>`;
    const rows = payload?.quarters || [];
    if (!rows.length) return `<section class="panel">${heading}<p>No quarterly statements are available from the provider.</p></section>`;
    const coverage = payload.coverage;
    return `<section class="panel quarterly-results">${heading}
      <p class="section-note">Reported values · ${esc(payload.currency || 'Reporting currency unavailable')} · ${esc(payload.source?.name || '')}. Fiscal period ends are shown; these are not announcement dates.</p>
      ${payload.cache?.stale ? `<p class="notice">Showing stale quarterly results fetched ${esc(payload.cache.fetched_at)}. <button class="small-button" data-action="stock-retry">Retry</button></p>` : ''}
      ${coverage && !coverage.complete ? `<p class="section-note">${esc(coverage.returned_quarters)} of ${esc(coverage.requested_quarters)} requested quarters available.</p>` : ''}
      <ol class="quarterly-timeline">${rows.map(row => `<li><h3>Period ending ${esc(row.period_end)}</h3>
        <small>Report date: ${esc(row.report_date || 'Unavailable')}${row.comparison_period_end ? ` · Compared with ${esc(row.comparison_period_end)}` : ''}</small>
        <dl>${metrics.map(([key, label]) => `<div><dt>${label}</dt><dd>${value(row[key], payload.currency)}<small>${comparison(row.year_over_year?.[key])}</small></dd></div>`).join('')}</dl>
      </li>`).join('')}</ol>
      <p class="section-note">Free cash flow uses the provider’s reported field. YoY is (current − prior) ÷ prior; it requires a positive prior value and a comparable period roughly one year earlier. Missing values are not zero.</p>
    </section>`;
  };
}
