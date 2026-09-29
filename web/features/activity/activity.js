/* Searchable activity history and browser-based transaction corrections. */
let historyFilters={query:'',account:'',kind:'',status:'',from:'',to:''};

const transactionKindLabels={opening_cash:'Opening cash',deposit:'Deposit',withdraw:'Withdrawal',buy:'Buy',sell:'Sell',opening_holding:'Opening holding',transfer:'Transfer',cpf_set:'CPF balance update',split:'Stock split',repayment:'Loan repayment',credit_set:'Card balance update',credit_purchase:'Card purchase',credit_refund:'Card refund',credit_payment:'Card payment',loan_disbursement:'Loan proceeds'};
const editableTransactionKinds=new Set(['opening_cash','deposit','withdraw','buy','sell','opening_holding','transfer','cpf_set','split','repayment','credit_purchase','credit_refund','credit_payment']);

function transactionSummary(e){
  const p=e.data;
  const card=data.credit_accounts?.find(a=>a.id===p.credit_account)?.cards?.find(c=>c.id===p.card);
  const cardName=card?.name||p.card||'';
  if(e.kind==='credit_purchase')return `${cardName} · ${p.description} · SGD ${fmt(p.amount)} purchase`;
  if(e.kind==='credit_refund')return `${cardName} · ${p.description} · SGD ${fmt(p.amount)} refund`;
  if(e.kind==='credit_payment')return `SGD ${fmt(p.amount)} paid from ${accountName(p.funding_account)}`;
  if(['deposit','withdraw'].includes(e.kind))return `${p.description?p.description+' · ':''}${p.currency||'SGD'} ${fmt(p.amount)}`;
  if(e.kind==='credit_set')return `Combined balance set to SGD ${fmt(p.amount)}`;
  if(e.kind==='repayment')return `SGD ${fmt(p.amount)} · ${(p.allocations||[]).map(a=>`${accountName(a.account)}: ${fmt(a.amount)}`).join(' + ')}`;
  if(['buy','sell','opening_holding'].includes(e.kind))return `${p.quantity} ${p.symbol} · ${p.currency} ${p.price==='unknown'?'cost unknown':fmt(p.price)}`;
  if(e.kind==='split')return `${p.symbol} · ${p.ratio} new per old share`;
  if(e.kind==='transfer')return `${p.currency} ${fmt(p.amount)} → ${p.to_currency} ${fmt(p.received)} · ${accountName(p.destination)}`;
  return `${p.currency||'SGD'} ${fmt(p.amount)}`;
}

function transactionAccountLabel(e){
  const p=e.data;
  if(p.account)return accountName(p.account);
  if(p.credit_account)return creditAccountName(p.credit_account);
  if(p.funding_account)return accountName(p.funding_account);
  if(p.allocations?.length)return `Loan repayment · ${[...new Set(p.allocations.map(a=>accountName(a.account)))].join(', ')}`;
  if(p.loan)return data.loans?.find(loan=>loan.id===p.loan)?.name||'Loan repayment';
  return '—';
}

function transactionRelatedAccounts(e){
  const p=e.data||{};
  return [p.account,p.destination,p.funding_account,...(p.allocations||[]).map(a=>a.account)].filter(Boolean);
}

function latestTransactionsFirst(events){
  return [...events].sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))||Number(b.order??0)-Number(a.order??0)||String(b.id||'').localeCompare(String(a.id||'')));
}

function transactionRestriction(e,action){
  if(e.status!=='active')return `This entry is ${e.status}; only active entries can be ${action}.`;
  if(e.kind==='loan_disbursement')return 'Loan opening proceeds are linked to the loan and cannot be changed independently.';
  if(e.kind==='credit_set')return 'This legacy combined balance entry is read-only; correct the underlying purchase, refund, or payment instead.';
  const archivedAccount=transactionRelatedAccounts(e).map(id=>data.accounts.find(a=>a.id===id)).find(a=>a?.archived);
  if(archivedAccount)return `This entry uses archived account “${archivedAccount.name}” and cannot be changed.`;
  const archivedCredit=data.credit_accounts?.find(a=>a.id===e.data.credit_account&&a.archived);
  if(archivedCredit)return `This entry uses archived account “${archivedCredit.name}” and cannot be changed.`;
  if(action==='edited'&&e.can_edit!==true)return e.edit_reason||'This transaction is locked against editing by the ledger.';
  if(action==='canceled'&&e.can_void!==true)return e.void_reason||'This transaction is locked against cancellation by the ledger.';
  if(action==='edited'&&!editableTransactionKinds.has(e.kind))return 'Editing this transaction type is not supported in the web app.';
  return '';
}

function historyAccountOptions(){
  return [...(data.accounts||[]).map(a=>[a.id,a.name]),...(data.credit_accounts||[]).map(a=>[a.id,a.name])]
    .map(([id,name])=>`<option value="${esc(id)}" ${historyFilters.account===id?'selected':''}>${esc(name)}</option>`).join('');
}

function filteredHistory(events){
  const query=historyFilters.query.trim().toLocaleLowerCase();
  const filtered=events.filter(e=>{
    const accountIds=[e.data.account,e.data.destination,e.data.funding_account,e.data.credit_account,...(e.data.allocations||[]).map(a=>a.account)].filter(Boolean);
    const searchText=[e.kind,transactionKindLabels[e.kind],e.status,e.date,e.id,transactionSummary(e),transactionAccountLabel(e),e.actor].join(' ').toLocaleLowerCase();
    return (!query||searchText.includes(query))&&(!historyFilters.account||accountIds.includes(historyFilters.account))&&(!historyFilters.kind||e.kind===historyFilters.kind)&&(!historyFilters.status||e.status===historyFilters.status)&&(!historyFilters.from||e.date>=historyFilters.from)&&(!historyFilters.to||e.date<=historyFilters.to);
  });
  return latestTransactionsFirst(filtered);
}

function historyRelation(e,events){
  if(e.replaces)return `<small class="history-relation">Correction for <button type="button" class="text-button" data-action="history-related" data-id="${esc(e.replaces)}">earlier transaction</button></small>`;
  const replacement=events.find(item=>item.id===(e.replaced_by||'')||item.replaces===e.id);
  if(replacement)return `<small class="history-relation">Corrected by <button type="button" class="text-button" data-action="history-related" data-id="${esc(replacement.id)}">later correction</button></small>`;
  if(e.status==='void')return `<small class="history-relation">Canceled · <button type="button" class="text-button" data-action="history-related" data-id="${esc(e.id)}">view retained entry</button></small>`;
  return '';
}

function historyRows(events,allEvents,manage){
  if(!events.length)return `<tr><td colspan="${manage?6:5}">${empty('No matching transactions','Try changing or clearing the filters.')}</td></tr>`;
  return events.map(e=>{
    const editReason=transactionRestriction(e,'edited'),cancelReason=transactionRestriction(e,'canceled');
    const reasons=[editReason?`Edit unavailable: ${editReason}`:'',cancelReason?`Cancel unavailable: ${cancelReason}`:''].filter(Boolean);
    const actions=manage?`<td class="history-actions"><button type="button" class="text-button" data-action="history-edit" data-id="${esc(e.id)}" ${editReason?`disabled title="${esc(editReason)}"`:''}>Edit</button><button type="button" class="text-button danger-text" data-action="history-cancel" data-id="${esc(e.id)}" ${cancelReason?`disabled title="${esc(cancelReason)}"`:''}>Cancel</button>${reasons.map(reason=>`<small class="action-reason">${esc(reason)}</small>`).join('')}</td>`:'';
    const statusLabel=e.status==='void'?'canceled':e.status;
    return `<tr id="transaction-${esc(e.id)}" class="status-${esc(e.status)}" tabindex="-1"><td>${badge(transactionKindLabels[e.kind]||e.kind.replaceAll('_',' '))}<small class="code">Reference ${esc(e.id)}</small>${historyRelation(e,allEvents)}</td><td>${esc(transactionAccountLabel(e))}</td><td>${esc(transactionSummary(e))}${e.adjustment!=null?`<small>Adjustment: ${esc(e.adjustment)}</small>`:''}</td><td>${esc(e.date)}</td><td>${esc(statusLabel)}</td>${actions}</tr>`;
  }).join('');
}

function historyResults(events,manage){
  const filtered=manage?filteredHistory(events):events;
  return `<div id="history-results" aria-live="polite"><p class="history-result-count">${filtered.length} ${filtered.length===1?'transaction':'transactions'}</p><div class="table-wrap"><table><thead><tr><th>Transaction</th><th>Account</th><th>Details</th><th>Date</th><th>Status</th>${manage?'<th>Actions</th>':''}</tr></thead><tbody>${historyRows(filtered,events,manage)}</tbody></table></div></div>`;
}

function historyTable(events,options={}){
  if(!options.manage){const latest=latestTransactionsFirst(events);return latest.length?`<div class="table-wrap"><table><thead><tr><th>Transaction</th><th>Account</th><th>Details</th><th>Date</th><th>Status</th></tr></thead><tbody>${historyRows(latest,latest,false)}</tbody></table></div>`:empty('No transactions yet','Your recorded activity will appear here.');}
  const kinds=[...new Set(events.map(e=>e.kind))].sort();
  return `<section class="activity-history"><div class="activity-filters" aria-label="Filter transaction history"><label>Search<input id="history-query" type="search" data-history-filter="query" value="${esc(historyFilters.query)}" placeholder="Description, amount, account…"></label><label>Account<select data-history-filter="account"><option value="">All accounts</option>${historyAccountOptions()}</select></label><label>Transaction type<select data-history-filter="kind"><option value="">All types</option>${kinds.map(kind=>`<option value="${esc(kind)}" ${historyFilters.kind===kind?'selected':''}>${esc(transactionKindLabels[kind]||kind.replaceAll('_',' '))}</option>`).join('')}</select></label><label>Status<select data-history-filter="status"><option value="">All statuses</option>${['active','superseded','void'].map(status=>`<option value="${status}" ${historyFilters.status===status?'selected':''}>${status==='void'?'canceled':status}</option>`).join('')}</select></label><label>From<input type="date" data-history-filter="from" value="${esc(historyFilters.from)}"></label><label>To<input type="date" data-history-filter="to" value="${esc(historyFilters.to)}"></label><button type="button" class="secondary small-button" data-action="history-reset">Clear filters</button></div>${historyResults(events,true)}</section>`;
}

function activityPage(){
  return `${bankCashflowPage()}<div class="panel"><div class="panel-head"><h2>Transaction history</h2><small>NEW ENTRIES VIA TELEGRAM · EXISTING ENTRIES MANAGED HERE</small></div>${historyTable(data.history,{manage:true})}</div>`;
}

function renderHistoryResults(events){
  const results=$('#history-results');if(results)results.outerHTML=historyResults(events,true);
}

function resetHistoryFilters(){
  historyFilters={query:'',account:'',kind:'',status:'',from:'',to:''};
  document.querySelectorAll('[data-history-filter]').forEach(field=>field.value='');
  renderHistoryResults(data.history);
}

function focusRelatedTransaction(id){
  resetHistoryFilters();
  const row=document.getElementById(`transaction-${id}`);row?.focus();row?.scrollIntoView({behavior:'smooth',block:'center'});
}

function editField(name,label,type,value,extra='',required=true){
  return `<label>${label}<input name="${name}" type="${type}" value="${esc(value??'')}" ${extra} ${required?'required':''}></label>`;
}

function editSelect(name,label,options,value){
  return `<label>${label}<select name="${name}" required>${options.map(([key,text])=>`<option value="${esc(key)}" ${String(key)===String(value)?'selected':''}>${esc(text)}</option>`).join('')}</select></label>`;
}

function selectableAccounts(type,value){
  return data.accounts.filter(a=>!a.archived&&(!type||a.type===type)).map(a=>[a.id,a.name+(a.currency?` · ${a.currency}`:'')]);
}

function transactionEditFields(e){
  const p=e.data,account=selectableAccounts(null,p.account),currency=editField('currency','Currency','text',p.currency||'SGD','maxlength="3" pattern="[A-Za-z]{3}"');
  if(['opening_cash','deposit','withdraw','cpf_set'].includes(e.kind))return `${editSelect('account','Account',selectableAccounts(e.kind==='cpf_set'?'cpf':null,p.account),p.account)}${currency}${editField('amount','Amount','number',p.amount,'step="any"')}${e.kind==='deposit'||e.kind==='withdraw'?editField('description','Description','text',p.description,'maxlength="160"',false):''}${editField('date','Date','date',e.date)}`;
  if(['buy','sell','opening_holding'].includes(e.kind))return `${editSelect('account','Brokerage account',selectableAccounts('brokerage',p.account),p.account)}${editField('exchange','Exchange','text',p.exchange,'maxlength="8" pattern="[A-Za-z]{3,8}"')}${editField('symbol','Ticker','text',p.symbol,'maxlength="20"')}${editSelect('asset_class','Asset class',[['equity','Equity'],['etf','ETF']],p.asset_class)}${editField('currency','Trading currency','text',p.currency,'maxlength="3" pattern="[A-Za-z]{3}"')}${editField('quantity','Quantity','number',p.quantity,'min="0.00000001" step="any"')}${editField('price',e.kind==='opening_holding'?'Unit cost (or unknown)':'Unit price',e.kind==='opening_holding'?'text':'number',p.price,e.kind==='opening_holding'?'':'min="0.00000001" step="any"')}${editField('date','Date','date',e.date)}`;
  if(e.kind==='split')return `${editSelect('account','Brokerage account',selectableAccounts('brokerage',p.account),p.account)}${editField('exchange','Exchange','text',p.exchange,'maxlength="8"')}${editField('symbol','Ticker','text',p.symbol,'maxlength="20"')}${editSelect('asset_class','Asset class',[['equity','Equity'],['etf','ETF']],p.asset_class)}${editField('currency','Trading currency','text',p.currency,'maxlength="3" pattern="[A-Za-z]{3}"')}${editField('ratio','New shares per old share','number',p.ratio,'min="0.00000001" step="any"')}${editField('date','Date','date',e.date)}`;
  if(e.kind==='transfer')return `${editSelect('account','From account',account,p.account)}${editSelect('destination','To account',selectableAccounts(null,p.destination),p.destination)}${editField('currency','From currency','text',p.currency,'maxlength="3" pattern="[A-Za-z]{3}"')}${editField('amount','Amount sent','number',p.amount,'min="0.00000001" step="any"')}${editField('to_currency','To currency','text',p.to_currency||p.currency,'maxlength="3" pattern="[A-Za-z]{3}"')}${editField('received','Amount received','number',p.received||p.amount,'min="0.00000001" step="any"')}${editField('date','Date','date',e.date)}`;
  if(['credit_purchase','credit_refund'].includes(e.kind)){
    const credit=data.credit_accounts.find(a=>a.id===p.credit_account);
    return `${editSelect('credit_account','Credit-card account',(data.credit_accounts||[]).map(a=>[a.id,a.name]),p.credit_account)}${editSelect('card','Card',(credit?.cards||[]).map(card=>[card.id,card.name]),p.card)}${editField('description','Description','text',p.description,'maxlength="160"')}${editField('amount','Amount (SGD)','number',p.amount,'min="0.01" step="0.01"')}${editField('date','Date','date',e.date)}`;
  }
  if(e.kind==='credit_payment')return `${editSelect('credit_account','Credit-card account',(data.credit_accounts||[]).map(a=>[a.id,a.name]),p.credit_account)}${editSelect('funding_account','Paid from',selectableAccounts(null,p.funding_account),p.funding_account)}${editField('amount','Payment (SGD)','number',p.amount,'min="0.01" step="0.01"')}${editField('date','Date','date',e.date)}`;
  return '';
}

function openTransactionEdit(id){
  const event=data.history.find(item=>item.id===id);if(!event)return;
  const reason=transactionRestriction(event,'edited');if(reason){toast(reason);return;}
  if(event.kind==='repayment'){repay(event.data.loan,event.id);return;}
  const fields=transactionEditFields(event);
  if(!fields){toast('Editing this transaction type is not supported in the web app.');return;}
  openModal(`Correct ${transactionKindLabels[event.kind]||'transaction'}`,`<form id="transaction-edit-form"><p class="form-note">Save a correction to this transaction. The original stays in history and the corrected entry is revalidated against your current accounts and balances.</p><p class="section-note">${esc(transactionAccountLabel(event))} · ${esc(event.date)} · ${esc(transactionSummary(event))}</p><div class="form-grid">${fields}</div>${formFooter('Save correction')}</form>`);
  const form=$('#transaction-edit-form');form.dataset.transactionManagement='true';form.dataset.transactionId=event.id;
  form.onsubmit=e=>{e.preventDefault();const submitted=Object.fromEntries(new FormData(form)),editable=new Set(event.editable_fields||[]),changes={};for(const [key,value] of Object.entries(submitted)){if(editable.has(key)&&String(value)!==String(event.data[key]??''))changes[key]=value;}if(changes.description==='')changes.description=null;if(!Object.keys(changes).length){$('#form-error').textContent='Change at least one field before saving.';return;}submitCommand('correct',{transaction:event.id,changes},form);};
}

function confirmTransactionCancel(id){
  const event=data.history.find(item=>item.id===id);if(!event)return;
  const reason=transactionRestriction(event,'canceled');if(reason){toast(reason);return;}
  openModal('Cancel this transaction?',`<form id="transaction-cancel-form"><p class="form-note"><strong>${esc(transactionKindLabels[event.kind]||event.kind)}</strong> · ${esc(transactionAccountLabel(event))} · ${esc(event.date)}<br>${esc(transactionSummary(event))}</p><div class="notice"><strong>This will reverse the transaction from calculated balances.</strong><p>The original record will remain in history marked as canceled. This action does not delete the audit record.</p></div><p id="form-error" class="error" role="alert"></p><div class="form-actions"><button type="button" class="secondary" data-action="close">Keep transaction</button><button type="submit">Cancel transaction</button></div></form>`);
  const form=$('#transaction-cancel-form');form.dataset.transactionManagement='true';form.dataset.transactionId=event.id;
  form.onsubmit=e=>{e.preventDefault();submitCommand('void',{transaction:event.id},form);};
}

function updateHistoryFilter(field){
  const name=field.dataset.historyFilter;if(!Object.hasOwn(historyFilters,name))return;
  historyFilters[name]=field.value;renderHistoryResults(data.history);
}

function updateTransactionCardOptions(form){
  const account=form.querySelector('[name="credit_account"]'),cardSelect=form.querySelector('[name="card"]');
  if(!account||!cardSelect)return;
  const prior=cardSelect.value,cards=data.credit_accounts.find(item=>item.id===account.value)?.cards||[];
  cardSelect.innerHTML=cards.map(card=>`<option value="${esc(card.id)}">${esc(card.name)}</option>`).join('');
  if(cards.some(card=>card.id===prior))cardSelect.value=prior;
}
