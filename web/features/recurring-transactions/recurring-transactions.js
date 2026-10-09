const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

function amountText(value, currency = 'SGD') {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return `${escapeHtml(currency)} ${escapeHtml(value)}`;
  return `${escapeHtml(currency)} ${amount.toLocaleString('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function monthName(value) {
  const month = Number(value);
  return Number.isInteger(month) && month >= 1 && month <= 12
    ? new Date(Date.UTC(2020, month - 1, 1)).toLocaleDateString('en-SG', { month: 'long', timeZone: 'UTC' })
    : String(value ?? '');
}

export function recurringScheduleFrequency(schedule) {
  const day = escapeHtml(schedule.day_of_month ?? schedule.dayOfMonth);
  const cadence = schedule.cadence || schedule.frequency;
  if (cadence === 'monthly') return `Monthly · day ${day}`;
  if (cadence === 'annual') return `Annually · ${escapeHtml(monthName(schedule.month_of_year ?? schedule.month))} ${day}`;
  return escapeHtml(schedule.frequencyLabel || cadence || 'Recurring');
}

function recurringScheduleRow(schedule) {
  const name = escapeHtml(schedule.description || schedule.name || 'Scheduled transaction');
  const accountName = schedule.account_name || schedule.accountLabel;
  const account = accountName ? `<small>${escapeHtml(accountName)}</small>` : '';
  const nextDueDate = schedule.next_due_date || schedule.nextDueDate;
  const nextDue = nextDueDate ? `<small>Next posting · ${escapeHtml(nextDueDate)}</small>` : '';
  const status = schedule.status && schedule.status !== 'active' ? `<small class="recurring-schedule-status">${escapeHtml(schedule.status)}</small>` : '';
  const actions = schedule.status === 'stopped' ? '' : `<div class="recurring-schedule-actions"><button type="button" class="secondary small-button" data-action="recurring-edit-amount" data-id="${escapeHtml(schedule.id)}">Edit amount</button><button type="button" class="secondary small-button" data-action="recurring-delete" data-id="${escapeHtml(schedule.id)}">Delete</button></div>`;
  return `<article class="recurring-schedule-row"><div class="recurring-schedule-main"><strong>${name}</strong>${account}<span class="recurring-schedule-frequency">${recurringScheduleFrequency(schedule)}</span>${nextDue}${status}</div><strong class="recurring-schedule-amount">${amountText(schedule.amount, schedule.currency)}</strong>${actions}</article>`;
}

/** Render the active-schedule list from the frontend's normalized view model. */
export function recurringSchedulesPanel({ schedules = [], loading = false, error = '' } = {}) {
  let content;
  if (loading) {
    content = '<p class="recurring-schedules-status" role="status">Loading active schedules…</p>';
  } else if (error) {
    content = `<p class="recurring-schedules-error" role="alert">${escapeHtml(error)}</p><button type="button" class="secondary small-button" data-action="recurring-retry">Try again</button>`;
  } else if (!schedules.length) {
    content = '<div class="empty"><strong>No recurring schedules</strong><p>Monthly and annual ledger entries you schedule in Telegram will appear here.</p></div>';
  } else {
    content = `<div class="recurring-schedule-list">${schedules.map(recurringScheduleRow).join('')}</div>`;
  }
  return `<section class="panel recurring-schedules" aria-labelledby="recurring-schedules-title"><div class="panel-head"><div><h2 id="recurring-schedules-title">Recurring transactions</h2><p class="section-note">Due schedules add transactions to your planner ledger. They do not initiate bank payments.</p></div><small>MONTHLY &amp; ANNUAL</small></div>${content}</section>`;
}

/** Shared modal body; callers bind the submit event and perform the API update. */
export function recurringAmountForm(schedule) {
  return `<form id="recurring-amount-form" data-schedule-id="${escapeHtml(schedule.id)}"><p class="form-note">Changing this amount affects future postings only. Transactions already posted to your ledger will not change.</p><label>Amount (${escapeHtml(schedule.currency || 'SGD')})<input name="amount" type="number" step="any" value="${escapeHtml(schedule.amount)}" required></label><p id="recurring-amount-error" class="error" role="alert"></p><div class="form-actions"><button type="button" class="secondary" data-action="close">Cancel</button><button type="submit">Save future amount</button></div></form>`;
}
