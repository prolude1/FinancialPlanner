export const BROKERAGE_RETURN_SCENARIOS = [0.05, 0.10, 0.15];

function dateParts(value) {
  const [year, month, day] = String(value).split('-').map(Number);
  return { year, month, day };
}

function anniversary(date, yearOffset) {
  const { year, month, day } = dateParts(date);
  const targetYear = year + yearOffset;
  const lastDay = new Date(Date.UTC(targetYear, month, 0)).getUTCDate();
  return Date.UTC(targetYear, month - 1, Math.min(day, lastDay));
}

/** Return annualized elapsed years, with exact year anniversaries at whole years. */
export function elapsedCalendarYears(startDate, endDate) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;

  let wholeYears = dateParts(endDate).year - dateParts(startDate).year;
  if (end < anniversary(startDate, wholeYears)) wholeYears -= 1;
  const completedAnniversary = anniversary(startDate, wholeYears);
  const nextAnniversary = anniversary(startDate, wholeYears + 1);
  const fraction = (end - completedAnniversary) / (nextAnniversary - completedAnniversary);
  return wholeYears + fraction;
}

/**
 * Add projection-only deposit dates to valuation dates. Deposits after the final
 * valuation can extend the chart timeline without implying an account value.
 */
export function brokerageProjectionDates(rows, deposits, { extendAfterLast = false, throughDate = null } = {}) {
  if (!Array.isArray(deposits)) return [];
  if (!rows.length) {
    const dates = new Set(deposits.map(deposit => deposit.date)
      .filter(date => date && (!throughDate || date <= throughDate)));
    if (!dates.size) return [];
    const firstDate = [...dates].sort()[0];
    if (throughDate && throughDate >= firstDate) dates.add(throughDate);
    return [...dates].sort().map(date => ({ date }));
  }
  const firstDate = rows[0].date;
  const lastDate = rows.at(-1).date;
  const dates = new Set(rows.map(row => row.date));
  for (const deposit of deposits) {
    if (deposit.date >= firstDate && (!throughDate || deposit.date <= throughDate) &&
        (deposit.date <= lastDate || extendAfterLast)) {
      dates.add(deposit.date);
    }
  }
  if (extendAfterLast && throughDate && throughDate > lastDate) dates.add(throughDate);
  return [...dates].sort().map(date => ({ date }));
}

/** Project converted deposit contributions; unconverted FX amounts are excluded and disclosed. */
export function brokerageProjectionSeries(rows, deposits, rates = BROKERAGE_RETURN_SCENARIOS) {
  if (!rows.length || !Array.isArray(deposits)) {
    return { available: false, complete: false, missingFxCount: 0, missingFxDates: [], values: [] };
  }

  let missingFxCount = 0;
  const missingFxDates = new Set();
  const contributions = [];
  for (const deposit of deposits) {
    const amount = deposit.amount_sgd == null ? null : Number(deposit.amount_sgd);
    const missingCount = Number.isFinite(Number(deposit.missing_fx_count))
      ? Math.max(0, Math.floor(Number(deposit.missing_fx_count)))
      : (deposit.missing_fx || amount == null ? 1 : 0);
    if (missingCount) {
      missingFxCount += missingCount;
      missingFxDates.add(deposit.date);
    }
    if (amount == null) continue;
    if (!Number.isFinite(amount)) {
      return { available: false, complete: false, missingFxCount, missingFxDates: [...missingFxDates].sort(), values: [] };
    }
    if (amount > 0) contributions.push({ date: deposit.date, amount });
  }

  const values = rows.map(row => ({
    date: row.date,
    scenarios: rates.map(rate => contributions.reduce((total, deposit) => {
      const years = elapsedCalendarYears(deposit.date, row.date);
      return years == null ? total : total + deposit.amount * (1 + rate) ** years;
    }, 0)),
  }));

  return {
    available: true,
    complete: missingFxCount === 0,
    missingFxCount,
    missingFxDates: [...missingFxDates].sort(),
    values,
  };
}
