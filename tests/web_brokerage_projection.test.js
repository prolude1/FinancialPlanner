const assert = require('assert');

(async () => {
  const { BROKERAGE_RETURN_SCENARIOS, brokerageProjectionSeries, elapsedCalendarYears } =
    await import('../web/features/dashboard/brokerage-projection.js');

  assert.equal(elapsedCalendarYears('2023-01-01', '2025-01-01'), 2);
  const rows = [
    { date: '2025-01-01' },
    { date: '2026-01-01' },
  ];
  const deposits = [
    { date: '2023-01-01', amount_sgd: '5000' },
    { date: '2026-01-01', amount_sgd: '2000' },
  ];
  const series = brokerageProjectionSeries(rows, deposits);
  assert.equal(series.complete, true);
  assert.deepEqual(BROKERAGE_RETURN_SCENARIOS, [0.05, 0.10, 0.15]);
  assert.equal(series.values[0].scenarios[1], 5000 * 1.1 ** 2);
  assert.equal(series.values[1].scenarios[1], 5000 * 1.1 ** 3 + 2000);
  assert.equal(brokerageProjectionSeries(rows, [{ date: '2023-01-01', amount_sgd: null }]).complete, false);
  console.log('PASS: dated deposits compound from their own dates at 5%, 10%, and 15%');
})().catch(error => { console.error(error); process.exitCode = 1; });
