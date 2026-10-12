const assert = require('assert');
const fs = require('fs');

async function main() {
  const { recurringSchedulesPanel, recurringAmountForm } = await import('../web/features/recurring-transactions/recurring-transactions.js');
  const html = recurringSchedulesPanel({ schedules: [
    { id: 'monthly-1', cadence: 'monthly', day_of_month: 15, month_of_year: null,
      account_name: 'Everyday account', amount: '123.45', currency: 'SGD',
      next_due_date: '2026-10-15', status: 'active', description: 'Rent' },
    { id: 'annual-1', cadence: 'annual', day_of_month: 7, month_of_year: 9,
      account_name: 'Bills account', amount: '80', currency: 'USD',
      next_due_date: '2027-09-07', status: 'paused', description: 'Domain renewal' },
  ] });
  for (const text of ['Recurring transactions', 'Monthly · day 15', 'Annually · September 7',
    'Everyday account', 'Bills account', 'SGD 123.45', 'USD 80.00', 'Next posting · 2026-10-15',
    'paused', 'do not initiate bank payments']) assert(html.includes(text), `missing rendered text: ${text}`);
  assert(recurringSchedulesPanel({ schedules: [] }).includes('No recurring schedules'));
  assert(html.includes('data-action="recurring-edit-amount"'));
  assert(html.includes('data-action="recurring-delete"'));
  assert(!recurringSchedulesPanel({ schedules: [{ id: 'stopped', cadence: 'monthly', day_of_month: 1, amount: '1', status: 'stopped' }] }).includes('data-action="recurring-edit-amount"'));
  assert(recurringAmountForm({ id: 'monthly-1', amount: '123.45', currency: 'SGD' }).includes('affects future postings only'));
  assert(recurringSchedulesPanel({ loading: true }).includes('Loading active schedules'));
  assert(recurringSchedulesPanel({ error: '<script>' }).includes('&lt;script&gt;'));

  const app = fs.readFileSync('web/app.js', 'utf8');
  assert(app.includes("api('/recurring-transactions')"), 'recurring page loads from authenticated API client');
  assert(app.includes("method:'DELETE'"), 'recurring schedule deletion uses the authenticated API client');
  assert(app.includes('Transactions already posted will remain in your history.'), 'deletion explains posted transactions are preserved');
  assert(app.includes("page==='recurring'"), 'recurring page is routed in the application');
  const index = fs.readFileSync('web/index.html', 'utf8');
  assert(index.includes('href="#recurring" data-nav="recurring"'), 'recurring page is in primary navigation');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
