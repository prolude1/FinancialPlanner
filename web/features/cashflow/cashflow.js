/* Read-only monthly bank cash-flow view backed by the ledger summary API. */
let bankCashflow=null;
let bankCashflowRequest=0;
let bankCashflowYear='last12';
let bankCashflowMonth='';

function cashflowYears(){
  const dates=(data?.history||[]).map(event=>event.date).filter(Boolean).sort();
  const first=dates.length?Number(dates[0].slice(0,4)):Number(data.as_of.slice(0,4));
  const last=Number(data.as_of.slice(0,4));
  return Array.from({length:Math.max(1,last-first+1)},(_,index)=>last-index);
}

function bankCashflowPage(){
  return `<section class="bank-cashflow panel" aria-labelledby="bank-cashflow-title"><div class="panel-head"><div><h2 id="bank-cashflow-title">Bank account cash flow</h2><p class="section-note">Bank accounts only. Brokerage and CPF activity are excluded. Deposits and loan proceeds are tracked inflows; withdrawals, repayments, and card payments are outflows. Deposits are not necessarily income.</p></div><small>ACTUAL TRACKED MOVEMENTS</small></div><div id="bank-cashflow-content" aria-live="polite">${empty('Loading bank cash flow','Fetching the latest 12 calendar months…')}</div></section>`;
}

function cashflowMonthLabel(month){
  return new Date(`${month}-01T12:00:00`).toLocaleDateString('en-SG',{month:'long',year:'numeric'});
}

function cashflowAmount(value,currency){return `${esc(currency)} ${fmt(value)}`;}

function cashflowRangeMonths(payload){
  const months=[];let [year,month]=payload.selection.start_month.split('-').map(Number);const end=payload.selection.end_month;
  while(`${year}-${String(month).padStart(2,'0')}`<=end&&`${year}-${String(month).padStart(2,'0')}`<=payload.as_of.slice(0,7)){
    months.push(`${year}-${String(month).padStart(2,'0')}`);
    if(month===12){year+=1;month=1;}else month+=1;
  }
  return months;
}

function cashflowAvailableMonths(payload){
  const asOfMonth=payload.as_of.slice(0,7);
  const months=[...new Set(Object.values(payload.currencies||{}).flatMap(series=>series.months||[]).map(row=>row.month).filter(month=>month<=asOfMonth))].sort();
  return months.length?months:cashflowRangeMonths(payload);
}

function cashflowDefaultMonth(payload){
  const months=cashflowAvailableMonths(payload);
  return months.at(-1)||payload.selection.end_month;
}

function cashflowTransactionButtons(rows){
  if(!rows?.length)return '<span class="muted">No contributing transactions</span>';
  const latest=[...rows].sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))||String(b.id||'').localeCompare(String(a.id||'')));
  return `<ul class="cashflow-transaction-list">${latest.map(row=>`<li><button type="button" class="text-button" data-action="cashflow-event" data-id="${esc(row.id)}">${esc(row.date)} · ${esc(transactionKindLabels[row.kind]||row.kind||'Transaction')} · ${cashflowAmount(row.amount,row.currency||'')}</button>${row.description?`<small>${esc(row.description)}</small>`:''}</li>`).join('')}</ul>`;
}

function cashflowBreakdownRows(items,fieldLabels,currency){
  if(!items?.length)return `<tr><td colspan="3">${empty('No tracked movements this month','There are no active bank cash movements in this currency for the selected month.')}</td></tr>`;
  return items.map(item=>{
    const title=item.account_name||transactionKindLabels[item.kind]||item.kind;
    const inAmt=Number(item.inflow||0),outAmt=Number(item.outflow||0),internalIn=Number(item.internal_transfer_in||0),internalOut=Number(item.internal_transfer_out||0);
    const main=fieldLabels==='kind'?`<strong>${esc(title)}</strong>`:`<strong>${esc(title)}</strong><small class="code">${esc(item.account)}</small>`;
    const totals=`${inAmt?`In ${cashflowAmount(item.inflow,currency)} `:''}${outAmt?`Out ${cashflowAmount(item.outflow,currency)} `:''}${internalIn?`Internal in ${cashflowAmount(item.internal_transfer_in,currency)} `:''}${internalOut?`Internal out ${cashflowAmount(item.internal_transfer_out,currency)}`:''}`||'—';
    return `<tr><td>${main}</td><td>${totals}</td><td><details><summary>${item.transactions?.length||0} transaction${item.transactions?.length===1?'':'s'}</summary>${cashflowTransactionButtons((item.transactions||[]).map(tx=>({...tx,kind:tx.kind||item.kind,currency})))}</details></td></tr>`;
  }).join('');
}

function cashflowMonthlyOverview(series,selectedMonth,asOf,selectPartial){
  return `<section class="cashflow-monthly-overview" aria-label="Monthly ${esc(series.currency)} cash flow"><h4>Monthly overview · ${esc(series.currency)}</h4><div class="table-wrap"><table><thead><tr><th>Month</th><th class="num">Inflow</th><th class="num">Outflow</th><th class="num">Net</th></tr></thead><tbody>${series.months.filter(row=>row.month<=asOf.slice(0,7)).slice().sort((a,b)=>b.month.localeCompare(a.month)).map(row=>`<tr class="${row.month===selectedMonth?'cashflow-month-selected':''}"><th scope="row"><button type="button" class="text-button" data-action="cashflow-month" data-month="${esc(row.month)}" aria-pressed="${row.month===selectedMonth}">${esc(cashflowMonthLabel(row.month))}${selectPartial&&row.month===asOf.slice(0,7)?' · partial':''}</button></th><td class="num">${cashflowAmount(row.inflow,series.currency)}</td><td class="num">${cashflowAmount(row.outflow,series.currency)}</td><td class="num ${Number(row.net)<0?'negative':''}">${cashflowAmount(row.net,series.currency)}</td></tr>`).join('')}</tbody></table></div></section>`;
}

function bankCashflowCurrencyCard(series,month,isPartial,asOf,selectPartial){
  const selected=series.months.find(row=>row.month===month);
  if(!selected)return '';
  const nonzero=Number(selected.inflow)||Number(selected.outflow)||Number(selected.internal_transfer_in)||Number(selected.internal_transfer_out);
  const transactions=(selected.by_kind||[]).flatMap(kind=>kind.transactions||[]);
  return `<article class="cashflow-currency" aria-labelledby="cashflow-${esc(series.currency)}-title"><div class="cashflow-currency-head"><h3 id="cashflow-${esc(series.currency)}-title">${esc(series.currency)}</h3><span>${isPartial?'Current month · partial':'Selected month'}</span></div>${cashflowMonthlyOverview(series,month,asOf,selectPartial)}<div class="cashflow-totals"><div><small>Inflow</small><strong>${cashflowAmount(selected.inflow,series.currency)}</strong></div><div><small>Outflow</small><strong>${cashflowAmount(selected.outflow,series.currency)}</strong></div><div><small>Net cash movement</small><strong class="${Number(selected.net)<0?'negative':''}">${cashflowAmount(selected.net,series.currency)}</strong></div></div><p class="cashflow-scope-note">Internal bank transfers are excluded from net cash movement${selected.internal_transfer_in||selected.internal_transfer_out?` · in ${cashflowAmount(selected.internal_transfer_in,series.currency)} · out ${cashflowAmount(selected.internal_transfer_out,series.currency)}`:''}.</p>${!nonzero?empty('No bank movements in this month',`No active tracked cash movement was recorded for ${esc(series.currency)} in ${esc(cashflowMonthLabel(month))}.`):''}<div class="cashflow-breakdowns"><section><h4>By transaction type</h4><div class="table-wrap"><table><thead><tr><th>Type</th><th>Cash movement</th><th>Contributing transactions</th></tr></thead><tbody>${cashflowBreakdownRows(selected.by_kind||[],'kind',series.currency)}</tbody></table></div></section><section><h4>By bank account</h4><div class="table-wrap"><table><thead><tr><th>Account</th><th>Cash movement</th><th>Contributing transactions</th></tr></thead><tbody>${cashflowBreakdownRows(selected.by_account||[],'account',series.currency)}</tbody></table></div></section></div>${transactions.length?`<p class="section-note">${transactions.length} contributing bank-side transaction leg${transactions.length===1?'':'s'} · all amounts in ${esc(series.currency)}.</p>`:''}</article>`;
}

function renderBankCashflow(){
  const host=document.querySelector('#bank-cashflow-content');if(!host)return;
  const payload=bankCashflow;
  if(!payload){host.innerHTML=empty('No monthly cash flow loaded','Try reloading the page.');return;}
  const selection=payload.selection,uniqueMonths=cashflowAvailableMonths(payload);
  if(!uniqueMonths.length){host.innerHTML=empty('No months available','Monthly bank cash flow will appear here when a date range is available.');return;}
  if(!uniqueMonths.includes(bankCashflowMonth))bankCashflowMonth=cashflowDefaultMonth(payload);
  const years=cashflowYears(),yearValue=bankCashflowYear==='last12'?'last12':String(bankCashflowYear);
  const options=`<option value="last12" ${yearValue==='last12'?'selected':''}>Latest 12 months</option>${years.map(year=>`<option value="${year}" ${yearValue===String(year)?'selected':''}>${year}</option>`).join('')}`;
  const monthOptions=uniqueMonths.slice().reverse().map(month=>`<option value="${esc(month)}" ${month===bankCashflowMonth?'selected':''}>${esc(cashflowMonthLabel(month))}</option>`).join('');
  const index=uniqueMonths.indexOf(bankCashflowMonth),previous=index>0?uniqueMonths[index-1]:'',next=index>=0&&index<uniqueMonths.length-1?uniqueMonths[index+1]:'';
  const currencies=Object.values(payload.currencies||{});
  const currencyContent=currencies.length?currencies.map(series=>bankCashflowCurrencyCard(series,bankCashflowMonth,bankCashflowMonth===payload.as_of.slice(0,7),payload.as_of,selection.current_month_partial||selection.end_month>=payload.as_of.slice(0,7))).join(''):empty('No bank cash flow in this range',`No active tracked bank cash movement is available for ${esc(cashflowMonthLabel(bankCashflowMonth))}. Brokerage and CPF events are excluded.`);
  host.innerHTML=`<div class="cashflow-controls"><label>Range<select id="cashflow-year" aria-label="Choose cash-flow range">${options}</select></label><div class="cashflow-month-controls"><button type="button" class="secondary small-button" data-action="cashflow-month" data-month="${esc(previous)}" ${previous?'':'disabled'} aria-label="Previous month">←</button><label>Month<select id="cashflow-month" aria-label="Choose cash-flow month">${monthOptions}</select></label><button type="button" class="secondary small-button" data-action="cashflow-month" data-month="${esc(next)}" ${next?'':'disabled'} aria-label="Next month">→</button></div></div><p class="cashflow-as-of">Data through ${esc(payload.as_of)} · ${selection.type==='last_12_months'?'latest 12 calendar months':`calendar year ${esc(selection.year)}`}</p>${currencyContent}`;
}

async function loadBankCashflow(year){
  const host=document.querySelector('#bank-cashflow-content');if(!host)return;
  const request=++bankCashflowRequest;
  host.innerHTML=empty('Loading bank cash flow','Fetching monthly totals from the ledger…');
  try{
    const query=year&&year!=='last12'?`?year=${encodeURIComponent(year)}`:'';
    const payload=await api(`/cashflow${query}`);
    if(request!==bankCashflowRequest||route().page!=='activity')return;
    bankCashflow=payload;
    bankCashflowYear=year||'last12';
    if(!bankCashflowMonth||!cashflowAvailableMonths(payload).includes(bankCashflowMonth))bankCashflowMonth=cashflowDefaultMonth(payload);
    renderBankCashflow();
  }catch(error){
    if(request!==bankCashflowRequest||route().page!=='activity')return;
    host.innerHTML=`<div class="empty"><strong>Bank cash flow is unavailable</strong><p>${esc(error.message||'The monthly summary could not be loaded.')}</p><button type="button" class="secondary small-button" data-action="cashflow-retry">Try again</button></div>`;
  }
}

function changeBankCashflowYear(year){
  bankCashflowMonth='';
  loadBankCashflow(year);
}

function changeBankCashflowMonth(month){
  if(!month)return;
  bankCashflowMonth=month;
  renderBankCashflow();
}
