/* Pure SVG plots: separate metric scales and explicit gaps for missing data. */
const METRICS = [['revenue', 'Revenue'], ['profit_after_tax', 'Profit after tax'], ['free_cash_flow', 'Free cash flow']];

export function financialTrendRows(periods = []) {
  return [...periods].sort((a, b) => a.period_end.localeCompare(b.period_end)).map(row => ({
    ...row, profit_margin: row.revenue != null && Number(row.revenue) > 0 && row.profit_after_tax != null
      ? Number(row.profit_after_tax) / Number(row.revenue) * 100 : null,
  }));
}

export function createFinancialTrendPanel({esc}) {
  function chart(rows, key, label, currency, annual) {
    const points = rows.map(row => row[key] == null ? null : Number(row[key]));
    const valid = points.filter(value => value != null && Number.isFinite(value));
    if (!valid.length) return `<article><h3>${label}</h3><p>No reported values available.</p></article>`;
    const min = Math.min(0, ...valid), max = Math.max(0, ...valid), span = max - min || 1;
    const dates = rows.map(row => Date.parse(row.period_end));
    const dateSpan = dates.at(-1) - dates[0] || 1;
    const x = index => rows.length === 1 ? 255 : 70 + (dates[index] - dates[0]) / dateSpan * 370;
    const y = value => 22 + (max - value) / span * 150;
    let connected = false;
    const path = points.map((value, index) => {
      // Do not bridge a missing quarter or fiscal year.
      if (index && dates[index] - dates[index - 1] > (annual ? 400 : 120) * 86400000) connected = false;
      if (value == null || !Number.isFinite(value)) { connected = false; return ''; }
      const segment = `${connected ? 'L' : 'M'}${x(index)},${y(value)}`;
      connected = true;
      return segment;
    }).join(' ');
    const number = value => key === 'profit_margin' ? `${value.toFixed(1)}%`
      : Intl.NumberFormat('en-SG', {notation: 'compact', maximumFractionDigits: 1}).format(value);
    return `<article><h3>${label}</h3><small>${esc(key === 'profit_margin' ? 'Percent of revenue' : currency || 'Reporting currency unavailable')}</small>
      <svg viewBox="0 0 470 220" role="img" aria-label="${esc(label)} financial trend. Exact values are in the table below.">
        <line class="trend-axis" x1="70" x2="440" y1="${y(0)}" y2="${y(0)}"/>
        <line class="trend-axis" x1="70" x2="70" y1="22" y2="172"/>
        <text x="3" y="30">${esc(number(max))}</text><text x="3" y="172">${esc(number(min))}</text>
        <path class="trend-line" d="${path}"/>
        ${points.map((value, index) => value == null || !Number.isFinite(value) ? '' : `<circle class="trend-point" cx="${x(index)}" cy="${y(value)}" r="4"><title>${esc(rows[index].period_end)}: ${esc(number(value))}</title></circle>`).join('')}
        <text x="70" y="205">${esc(rows[0].period_end)}</text><text x="440" y="205" text-anchor="end">${esc(rows.at(-1).period_end)}</text>
      </svg></article>`;
  }
  return function financialTrendPanel(payload, annualPayload, mode = 'quarterly', error = '') {
    const annual = mode === 'annual';
    const selected = annual ? annualPayload : payload;
    const periods = annual ? selected?.fiscal_years : selected?.quarters;
    if (payload?.supported === false) return '';
    const controls = `<div class="stock-ranges">${['quarterly', 'annual'].map(value => `<button data-action="stock-trend-period" data-value="${value}" aria-pressed="${mode === value}" class="${mode === value ? 'active' : ''}">${value === 'annual' ? 'Annual' : 'Quarterly'}</button>`).join('')}</div>`;
    if (error) return `<section class="panel"><div class="panel-head"><h2>Financial trends</h2>${controls}</div><p>${esc(error)}</p><button class="small-button" data-action="stock-retry">Retry</button></section>`;
    if (!periods?.length) return `<section class="panel"><div class="panel-head"><h2>Financial trends</h2>${controls}</div><p>No ${annual ? 'annual' : 'quarterly'} statements available.</p></section>`;
    const rows = financialTrendRows(periods);
    return `<section class="panel financial-trends"><div class="panel-head"><h2>${annual ? 'Annual' : 'Quarterly'} financial trends</h2>${controls}</div>
      <p class="section-note">Fiscal period end dates · Each chart has its own scale. Missing values and missing periods break the line.${selected.cache?.stale ? ' These results are from a stale cache.' : ''}</p>
      <div class="financial-trend-grid">${[...METRICS, ['profit_margin', 'Profit margin']].map(([key, label]) => chart(rows, key, label, selected.currency, annual)).join('')}</div>
      <details><summary>Exact trend values (${esc(selected.currency || 'reporting currency unavailable')})</summary><div class="table-wrap"><table><thead><tr><th>Period end</th>${METRICS.map(([, label]) => `<th class="num">${label}</th>`).join('')}<th class="num">Profit margin</th></tr></thead><tbody>
        ${rows.map(row => `<tr><td>${esc(row.period_end)}</td>${METRICS.map(([key]) => `<td class="num">${row[key] == null ? 'Unavailable' : esc(row[key])}</td>`).join('')}<td class="num">${row.profit_margin == null ? 'Unavailable' : `${row.profit_margin.toFixed(2)}%`}</td></tr>`).join('')}
      </tbody></table></div></details>
      <p class="section-note">Profit margin = profit after tax ÷ revenue. Fluctuations may reflect seasonality; use the timeline’s YoY comparisons alongside these trends.</p>
    </section>`;
  };
}
