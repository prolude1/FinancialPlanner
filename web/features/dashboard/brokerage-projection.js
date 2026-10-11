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

/** Project only date-stamped deposit contributions; other ledger activity is ignored. */
export function brokerageProjectionSeries(rows, deposits, rates = BROKERAGE_RETURN_SCENARIOS) {
  if (!rows.length || !Array.isArray(deposits) || deposits.some(deposit => deposit.amount_sgd == null)) {
    return { complete: false, values: [] };
  }

  const contributions = deposits
    .filter(deposit => Number(deposit.amount_sgd) > 0)
    .map(deposit => ({ date: deposit.date, amount: Number(deposit.amount_sgd) }));

  const values = rows.map(row => ({
    date: row.date,
    scenarios: rates.map(rate => contributions.reduce((total, deposit) => {
      const years = elapsedCalendarYears(deposit.date, row.date);
      return years == null ? total : total + deposit.amount * (1 + rate) ** years;
    }, 0)),
  }));

  return { complete: true, values };
}
