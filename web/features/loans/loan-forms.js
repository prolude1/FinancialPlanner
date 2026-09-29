/** Loan command forms; page state and shell actions are injected by the dashboard. */
export function createLoanForms(dependencies) {
  const { $, esc, fmt, input, openModal, formFooter, submitCommand } = dependencies;
  let data = null;

  function setData(snapshot) {
    data = snapshot;
  }

  function addLoan(){
    openModal('Add an HDB loan',`<form id="loan-form"><p class="form-note">Enter your end-of-day statement balance. Interest is charged on the first of each following month; enter any already-accrued interest separately. Existing loans do not create cash.</p><div class="form-grid">${input('name','Loan name','text','HDB housing loan','maxlength="100"')}${input('as_of','Opening balance date','date',data.as_of,`max="${data.as_of}"`)}${input('principal','Outstanding principal (SGD)','number','','min="0.01" step="0.01"')}${input('opening_interest','Already-accrued unpaid interest (SGD)','number','0','min="0" step="0.01"')}${input('rate','Annual interest rate (%)','number','2.6','min="0" max="100" step="0.0001"')}${input('installment','Monthly installment (SGD)','number','','min="0.01" step="0.01"')}${input('next_due','Next payment date after opening','date','','')}<label>New loan proceeds (optional)<select name="disburse_to"><option value="">Existing loan — no deposit</option>${data.accounts.filter(a=>!a.archived&&a.type!=='cpf').map(a=>`<option value="${esc(a.id)}">Deposit into ${esc(a.name)}</option>`).join('')}</select></label></div><p class="section-note">Payments settle interest first, then principal. Extra payments shorten the projection. No late fees or automatic payments are assumed. Verify the rate against your statement.</p>${formFooter('Create loan')}</form>`);
    $('#loan-form').onsubmit=e=>{e.preventDefault();submitCommand('loan_add',Object.fromEntries(new FormData(e.target)),e.target);};
  }
  function allocationRow(value={}){
    const accounts=data.accounts.filter(a=>!a.archived&&(a.type!=='cpf'||a.cpf_type==='OA'));
    return `<div class="allocation-row"><select name="source" aria-label="Funding account" required>${accounts.map(a=>`<option value="${esc(a.id)}" ${value.account===a.id?'selected':''}>${esc(a.name)} · SGD ${fmt(a.cash.find(c=>c.currency==='SGD')?.amount || '0')}</option>`).join('')}</select><input name="allocation" aria-label="Amount from this account in SGD" type="number" min="0.01" step="0.01" value="${esc(value.amount || '')}" required><button type="button" data-action="remove-allocation" aria-label="Remove funding account">×</button></div>`;
  }
  function repay(id, transaction){
    const loan=data.loans.find(l=>l.id===id), original=data.history.find(e=>e.id===transaction);
    openModal(transaction?'Correct repayment':'Record a repayment',`<form id="payment-form"><p class="form-note">${esc(loan.name)} · Outstanding SGD ${fmt(loan.outstanding)}. All funding entries are saved together. This records a payment you made; it does not send money.</p><div class="form-grid">${input('amount','Payment amount (SGD)','number',original?.data.amount || loan.installment,'min="0.01" step="0.01"')}${input('date','Payment date','date',original?.date || data.as_of,`max="${data.as_of}"`)}</div><h3>Fund this payment from</h3><div id="allocations">${(original?.data.allocations || [{}]).map(allocationRow).join('')}</div><button type="button" class="secondary small-button" data-action="add-allocation">+ Another account</button><p class="section-note" style="margin-top:14px">Amounts must add up to the payment total. Use SGD balances; convert other currencies before recording a repayment.</p>${formFooter(transaction?'Save correction':'Record repayment')}</form>`);
    $('#payment-form').onsubmit=e=>{e.preventDefault();const f=e.target;const amount=f.elements.amount.value,allocations=[...f.querySelectorAll('.allocation-row')].map(r=>({account:r.querySelector('select').value,amount:r.querySelector('input').value})),date=f.elements.date.value;if(transaction){const changes={amount,allocations};if(date!==original.date)changes.date=date;f.dataset.transactionManagement='true';f.dataset.transactionId=transaction;submitCommand('correct',{transaction,changes},f);}else submitCommand('repayment',{loan:id,amount,date,allocations},f);};
  }
  function rate(id){
    openModal('Record an interest-rate change',`<form id="rate-form"><p class="form-note">Use the first day of the month when the new annual rate applies. Past changes recalculate interest and must not invalidate recorded repayments.</p>${input('date','Effective date','date')}${input('rate','Annual rate (%)','number','','min="0" max="100" step="0.0001"')}${formFooter('Save rate')}</form>`);
    $('#rate-form').onsubmit=e=>{e.preventDefault();submitCommand('loan_rate',{loan:id,...Object.fromEntries(new FormData(e.target))},e.target);};
  }

  return { setData, addLoan, allocationRow, repay, rate };
}
