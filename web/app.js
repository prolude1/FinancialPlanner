/* Read-only asset screens and shared command-backed loan forms. No client ledger. */
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (v, digits=2) => v == null ? 'Unavailable' : Number(v).toLocaleString('en-SG', {minimumFractionDigits:digits, maximumFractionDigits:digits});
const labels = {cash:'Cash', equity:'Equities', etf:'ETFs', cpf:'CPF'};
const colors = {cash:'#b9d291', equity:'#1f6152', etf:'#78a58d', cpf:'#e4d7ad'};
let data, csrf='', revision=-1, dataDate='', polling=false, lastLoaded=0;
const chartState={};

async function api(path, options={}) {
  const headers={'X-CSRF-Token':csrf,...options.headers};if(!(options.body instanceof FormData))headers['Content-Type']='application/json';
  const response = await fetch('/api'+path, {...options, headers});
  const contentType=response.headers.get('content-type')||'',body=contentType.includes('json')?await response.json():null;
  if (!response.ok) {
    if(response.status===401) showLogin();
    throw Object.assign(new Error(typeof body?.detail === 'string' ? body.detail : body?.detail?.message || body?.message || 'Request failed. Check the fields and try again.'),{status:response.status,code:body?.detail?.code||body?.code});
  }
  return contentType.includes('json')?body:response;
}
function showLogin(){ $('#shell').hidden=true; $('#login').hidden=false; csrf=''; }
function toast(message){ $('#toast').textContent=message; $('#toast').hidden=false; setTimeout(()=>$('#toast').hidden=true,4500); }
function route(){ const [page,id] = location.hash.slice(1).split('/'); return {page:page || 'overview',id}; }
function badge(text, warning=false){ return `<span class="badge ${warning?'warning':''}">${esc(text)}</span>`; }
function empty(title, text){return `<div class="empty"><strong>${title}</strong><p>${text}</p></div>`;}
function accountName(id){ return data.accounts.find(a=>a.id===id)?.name || id; }
function creditAccountName(id){ return data.credit_accounts?.find(a=>a.id===id)?.name || id; }
function transactionSummary(e){
  const p=e.data;
  const cardName=data.credit_accounts?.find(a=>a.id===p.credit_account)?.cards.find(c=>c.id===p.card)?.name || p.card || '';
  if(e.kind==='credit_purchase') return `${cardName} · ${p.description} · SGD ${fmt(p.amount)} purchase`;
  if(e.kind==='credit_refund') return `${cardName} · ${p.description} · SGD ${fmt(p.amount)} refund`;
  if(e.kind==='credit_payment') return `SGD ${fmt(p.amount)} paid from ${accountName(p.funding_account)}`;
  if(['deposit','withdraw'].includes(e.kind)) return `${p.description ? p.description+' · ' : ''}${p.currency || 'SGD'} ${fmt(p.amount)}`;
  if(e.kind==='credit_set') return `Combined balance set to SGD ${fmt(p.amount)}`;
  if(e.kind==='repayment') return `SGD ${fmt(p.amount)} · ${p.allocations.map(a=>`${accountName(a.account)}: ${fmt(a.amount)}`).join(' + ')}`;
  if(['buy','sell','opening_holding'].includes(e.kind)) return `${p.quantity} ${p.symbol} · ${p.currency} ${p.price==='unknown'?'cost unknown':fmt(p.price)}`;
  if(e.kind==='split') return `${p.symbol} · ${p.ratio} new per old share`;
  if(e.kind==='transfer') return `${p.currency} ${fmt(p.amount)} → ${p.to_currency} ${fmt(p.received)} · ${accountName(p.destination)}`;
  return `${p.currency || 'SGD'} ${fmt(p.amount)}`;
}
function historyTable(events){
  if(!events.length) return empty('No transactions yet','Your recorded activity will appear here.');
  return `<div class="table-wrap"><table><thead><tr><th>Transaction</th><th>Account</th><th>Details</th><th>Date</th><th>Status</th></tr></thead><tbody>${events.map(e=>`<tr class="status-${esc(e.status)}"><td>${badge(e.kind.replaceAll('_',' '))}<small class="code">${esc(e.id)}</small></td><td>${esc(e.data.account?accountName(e.data.account):e.data.credit_account?creditAccountName(e.data.credit_account):'Loan repayment')}</td><td>${esc(transactionSummary(e))}${e.adjustment!=null?`<small>Adjustment: ${esc(e.adjustment)}</small>`:''}</td><td>${esc(e.date)}</td><td>${esc(e.status)}</td></tr>`).join('')}</tbody></table></div>`;
}
function accountRow(a){return `<a class="account-item" href="#accounts/${esc(a.id)}"><span class="account-icon">${a.type==='cpf'?'◈':a.type==='brokerage'?'↗':'▤'}</span><span class="account-meta"><span class="account-name">${esc(a.name)}</span><small>${a.type==='cpf'?'CPF · '+esc(a.cpf_type):esc(a.type)}${a.archived?' · Archived':''}${a.cpf_stale?' · Update overdue':''}</small></span><span class="account-amount">SGD ${fmt(a.sgd)}${a.complete?'':' *'}<small>${Object.entries(a.native).map(([c,v])=>`${esc(c)} ${fmt(v)}`).join(' · ') || 'No balances'}</small></span><span class="arrow">›</span></a>`;}
const accountTypeOrder=['bank','brokerage','cpf'];
const accountTypeLabels={bank:'Bank accounts',brokerage:'Brokerage accounts',cpf:'CPF accounts'};
function accountGroups(accounts){
  return accountTypeOrder.map(type=>{
    const members=accounts.filter(a=>a.type===type).sort((a,b)=>a.name.localeCompare(b.name,'en-SG',{sensitivity:'base'}));
    return members.length?`<section class="account-group"><h3>${accountTypeLabels[type]} <span>${members.length}</span></h3>${members.map(accountRow).join('')}</section>`:'';
  }).join('');
}
function historyRange(rows,period){
  if(!rows.length)return [0,0];
  const days={"1m":31,"3m":92,"1y":366,all:Infinity}[period]||92;
  if(!Number.isFinite(days))return [0,rows.length-1];
  const cutoff=new Date(rows.at(-1).date+'T12:00:00');cutoff.setDate(cutoff.getDate()-days);
  return [Math.max(0,rows.findIndex(row=>new Date(row.date+'T12:00:00')>=cutoff)),rows.length-1];
}
function brokerageChart(account){
  const rows=data.portfolio_history?.[account.id]||[];
  if(!rows.length)return `<div class="history-chart panel-inset">${empty('Historical chart is being prepared','The market worker will backfill daily prices and FX. Refresh after it finishes.')}</div>`;
  if(!chartState[account.id]){const [start,end]=historyRange(rows,'3m');chartState[account.id]={period:'3m',metric:'value_sgd',start,end};}
  return `<div class="brokerage-chart" data-account="${esc(account.id)}"><p class="section-note">${esc(account.name)} only · Cash and holdings valued in SGD · ${rows.length} daily observations</p><div class="chart-toolbar"><div><button data-action="chart-metric" data-value="value_sgd">Total value</button><button data-action="chart-metric" data-value="gross_change_sgd">Daily movement</button><button data-action="chart-metric" data-value="adjusted_change_sgd">Investment movement</button></div><div><button data-action="chart-period" data-value="1m">1M</button><button data-action="chart-period" data-value="3m">3M</button><button data-action="chart-period" data-value="1y">1Y</button><button data-action="chart-period" data-value="all">All</button></div></div><div class="chart-plot"></div><p class="section-note">Daily movement includes deposits and withdrawals. Investment movement removes recorded external cash flows. Scroll over the graph to zoom; move the pointer to inspect a date.</p></div>`;
}
function drawBrokerageChart(accountId){
  const root=document.querySelector(`.brokerage-chart[data-account="${CSS.escape(accountId)}"]`);if(!root)return;
  const rows=data.portfolio_history?.[accountId]||[],state=chartState[accountId];if(!rows.length||!state)return;
  state.start=Math.max(0,Math.min(state.start,rows.length-1));state.end=Math.max(state.start,Math.min(state.end,rows.length-1));
  const shown=rows.slice(state.start,state.end+1),values=shown.map(r=>Number(r[state.metric]??0));
  let min=Math.min(...values),max=Math.max(...values);if(min===max){const pad=Math.max(Math.abs(max)*.02,1);min-=pad;max+=pad;}
  if(state.metric!=='value_sgd'){min=Math.min(min,0);max=Math.max(max,0);}
  const w=800,h=280,p={l:70,r:18,t:18,b:34},x=i=>p.l+(shown.length===1?.5:(Date.parse(shown[i].date)-Date.parse(shown[0].date))/(Date.parse(shown.at(-1).date)-Date.parse(shown[0].date)))*(w-p.l-p.r),y=v=>p.t+(max-v)/(max-min)*(h-p.t-p.b);
  const points=values.map((v,i)=>`${x(i)},${y(v)}`).join(' '),zero=state.metric==='value_sgd'?'':`<line class="chart-zero" x1="${p.l}" x2="${w-p.r}" y1="${y(0)}" y2="${y(0)}"/>`;
  root.querySelector('.chart-plot').innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Brokerage history"><line class="chart-axis" x1="${p.l}" x2="${p.l}" y1="${p.t}" y2="${h-p.b}"/>${zero}<polyline class="chart-line" points="${points}"/>${values.map((v,i)=>`<circle class="chart-point" cx="${x(i)}" cy="${y(v)}" r="3"/>`).join('')}<text x="${p.l}" y="${h-8}">${esc(shown[0].date)}</text><text text-anchor="end" x="${w-p.r}" y="${h-8}">${esc(shown.at(-1).date)}</text><text x="4" y="${p.t+5}">SGD ${fmt(max)}</text><text x="4" y="${h-p.b}">SGD ${fmt(min)}</text><line class="chart-cursor" hidden y1="${p.t}" y2="${h-p.b}"/><circle class="chart-point chart-hover-point" hidden r="4"/><rect class="chart-hit" data-action="chart-hover" x="${p.l}" y="${p.t}" width="${w-p.l-p.r}" height="${h-p.t-p.b}"/></svg><div class="chart-tooltip" hidden></div>`;
  root.querySelectorAll('[data-action="chart-period"]').forEach(b=>b.classList.toggle('active',b.dataset.value===state.period));
  root.querySelectorAll('[data-action="chart-metric"]').forEach(b=>b.classList.toggle('active',b.dataset.value===state.metric));
}
function chartHover(event){
  const root=event.target.closest('.brokerage-chart'),svg=event.target.closest('svg');if(!root||!svg)return;
  const rows=data.portfolio_history[root.dataset.account]||[],state=chartState[root.dataset.account],shown=rows.slice(state.start,state.end+1),box=svg.getBoundingClientRect();
  const ratio=Math.max(0,Math.min(1,((event.clientX-box.left)/box.width*800-70)/(800-70-18))),first=Date.parse(shown[0].date),last=Date.parse(shown.at(-1).date),target=first+ratio*(last-first),index=shown.reduce((best,row,i)=>Math.abs(Date.parse(row.date)-target)<Math.abs(Date.parse(shown[best].date)-target)?i:best,0),row=shown[index],value=Number(row[state.metric]??0);
  const x=70+(shown.length===1?.5:(Date.parse(row.date)-first)/(last-first))*(800-70-18);
  let vals=shown.map(r=>Number(r[state.metric]??0)),min=Math.min(...vals),max=Math.max(...vals);if(min===max){const pad=Math.max(Math.abs(max)*.02,1);min-=pad;max+=pad;}if(state.metric!=='value_sgd'){min=Math.min(min,0);max=Math.max(max,0);}const y=18+(max-value)/(max-min)*(280-18-34);
  const cursor=svg.querySelector('.chart-cursor'),point=svg.querySelector('.chart-hover-point'),tip=root.querySelector('.chart-tooltip');cursor.removeAttribute('hidden');point.removeAttribute('hidden');cursor.setAttribute('x1',x);cursor.setAttribute('x2',x);point.setAttribute('cx',x);point.setAttribute('cy',y);tip.hidden=false;tip.style.left=`${ratio*100}%`;tip.innerHTML=`<strong>${esc(row.date)}</strong><span>SGD ${fmt(value)}</span>`;
}
function summary(){return `<div class="summary-grid"><div class="summary-card primary"><div class="summary-label">Net worth <span>↗</span></div><div class="summary-value"><small>SGD</small>${fmt(data.net_worth)}</div><div class="summary-foot">${data.complete?'Assets less outstanding liabilities':'Partial value · missing valuations'}</div></div><div class="summary-card"><div class="summary-label">Total assets <span>◫</span></div><div class="summary-value"><small>SGD</small>${fmt(data.assets)}</div><div class="summary-foot">Across ${data.accounts.filter(a=>!a.archived).length} accounts${data.complete?'':' · incomplete'}</div></div><div class="summary-card"><div class="summary-label">Total liabilities <span>↙</span></div><div class="summary-value"><small>SGD</small>${fmt(data.liabilities)}</div><div class="summary-foot">Principal + accrued unpaid interest</div></div></div>`;}
function allocation(){
  let accumulated=0; const total=Number(data.assets);
  const slices=Object.entries(data.allocation).map(([key,value])=>{let start=accumulated; accumulated+=total?Number(value)/total*100:0; return `${colors[key]} ${start}% ${accumulated}%`;});
  const valued=Object.entries(data.allocation).filter(([,value])=>Number(value)!==0);
  return `<div class="panel"><div class="panel-head"><h2>Asset allocation</h2><small>BY ASSET CLASS</small></div>${total?`<div class="donut-wrap"><div class="donut" role="img" aria-label="Asset allocation; percentages listed alongside" style="background:conic-gradient(${slices.join(',')})"><div class="donut-hole"><strong>${valued.length}</strong>asset classes</div></div><div class="legend">${valued.map(([k,v])=>`<div class="legend-row"><span><i class="swatch" style="background:${colors[k]}"></i>${labels[k]}</span><strong>${(Number(v)/total*100).toFixed(1)}%</strong></div>`).join('')}</div></div><div class="allocation-values">${valued.map(([k,v])=>`<div><span><i class="swatch" style="background:${colors[k]}"></i>${labels[k]}</span><strong><small>SGD</small> ${fmt(v)}</strong></div>`).join('')}<div class="allocation-total"><span>Total assets</span><strong><small>SGD</small> ${fmt(data.assets)}</strong></div></div>`:empty('Your allocation starts here','Add accounts and opening balances through Telegram.')}<p class="section-note" style="margin-top:18px;margin-bottom:0">${data.complete?'Valued in SGD. CPF is shown separately from cash.':'Chart includes priced assets only. Missing values are excluded.'}</p></div>`;
}
function creditCards(accounts=data.credit_accounts||[]){
  if(!accounts.length) return '';
  return `<div class="panel"><div class="panel-head"><h2>Credit cards</h2><a href="#credit-cards">Credit-card dashboard ↗</a></div>${accounts.map(account=>`<section class="credit-account"><div class="credit-account-head"><div><strong>${esc(account.name)}</strong><small class="code">${esc(account.id)}</small></div><div><small>${Number(account.balance)<0?'Account credit':'Combined outstanding'}</small><strong>SGD ${fmt(Math.abs(Number(account.balance)))}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Card</th><th class="num">Purchases less refunds</th></tr></thead><tbody>${account.cards.length?account.cards.map(card=>`<tr><td>${esc(card.name)}<small class="code">${esc(card.id)}</small></td><td class="num">SGD ${fmt(card.activity)}</td></tr>`).join(''):`<tr><td colspan="2">No cards have been added.</td></tr>`}</tbody></table></div></section>`).join('')}<p class="section-note">Per-card figures show purchases less refunds. Payments and manual balance updates apply only to the authoritative combined account balance.</p></div>`;
}
function creditCardsPage(id){
  const accounts=data.credit_accounts||[], selected=id?accounts.find(a=>a.id===id):null;
  if(id&&!selected)return empty('Credit-card account not found','Choose an account from the Credit cards dashboard.');
  const visible=selected?[selected]:accounts;
  const events=data.history.filter(e=>e.data.credit_account&&visible.some(a=>a.id===e.data.credit_account));
  const month=data.as_of.slice(0,7), active=events.filter(e=>e.status==='active'&&e.date.slice(0,7)===month);
  const total=kind=>active.filter(e=>e.kind===kind).reduce((sum,e)=>sum+Number(e.data.amount),0);
  const outstanding=visible.reduce((sum,a)=>sum+Math.max(0,Number(a.balance)),0);
  const credit=visible.reduce((sum,a)=>sum+Math.max(0,-Number(a.balance)),0);
  const monthLabel=new Date(data.as_of+'T12:00:00').toLocaleDateString('en-SG',{month:'long',year:'numeric'});
  return `<div class="credit-filter"><a class="${!id?'active':''}" href="#credit-cards">All accounts</a>${accounts.map(a=>`<a class="${id===a.id?'active':''}" href="#credit-cards/${esc(a.id)}">${esc(a.name)}</a>`).join('')}</div>
    <div class="summary-grid"><div class="summary-card primary"><div class="summary-label">Outstanding balance</div><div class="summary-value"><small>SGD</small>${fmt(outstanding)}</div><div class="summary-foot">${visible.length} account${visible.length===1?'':'s'} · Account credits: SGD ${fmt(credit)}</div></div>
    <div class="summary-card"><div class="summary-label">Purchases less refunds</div><div class="summary-value"><small>SGD</small>${fmt(total('credit_purchase')-total('credit_refund'))}</div><div class="summary-foot">${esc(monthLabel)} · Refunds: SGD ${fmt(total('credit_refund'))}</div></div>
    <div class="summary-card"><div class="summary-label">Payments recorded</div><div class="summary-value"><small>SGD</small>${fmt(total('credit_payment'))}</div><div class="summary-foot">${esc(monthLabel)}</div></div></div>
    ${visible.length?creditCards(visible):`<div class="panel">${empty('No credit-card accounts yet','Use <code>/credit_account_add</code> in Telegram, then <code>/credit_card_add</code> to add your cards.')}</div>`}
    <p class="section-note">Record purchases, refunds, payments, and balance updates through your private Telegram bot. Monthly totals include active entries only.</p>
    <div class="panel"><div class="panel-head"><h2>Payment history</h2></div>${historyTable(events.filter(e=>e.kind==='credit_payment'))}</div>
    <div class="panel"><div class="panel-head"><h2>Card activity & balance updates</h2></div>${historyTable(events.filter(e=>e.kind!=='credit_payment'))}</div>`;
}
function overview(){
  const accounts=data.accounts.filter(a=>!a.archived);
  return summary()+`<div class="main-grid">${allocation()}<div class="panel"><div class="panel-head"><h2>Your accounts</h2><a href="#accounts">View all ↗</a></div>${accounts.length?accountGroups(accounts):empty('Bring your accounts together','Send <code>/account_add</code> to your private bot, then record your opening balances.')}</div></div>${creditCards()}<div class="panel"><div class="panel-head"><h2>Recent activity</h2><a href="#activity">View history ↗</a></div>${historyTable(data.history.slice(0,6))}</div>`;
}
function accountsPage(id){
  if(!id) return `<div class="notice soft">Account changes are recorded through Telegram for now. Use /account_add to create an account.</div><div class="panel">${data.accounts.length?accountGroups(data.accounts):empty('No accounts yet','Start with <code>/account_add</code> in Telegram.')}</div>`;
  const a=data.accounts.find(x=>x.id===id);
  if(!a) return empty('Account not found','Choose an account from the account list.');
  $('#page-title').textContent=a.name;
  $('#page-subtitle').textContent=`${a.type==='cpf'?'CPF '+a.cpf_type:a.type} · Account ${a.id}`;
  let html=`<a class="back-link" href="#accounts">← All accounts</a>`;
  if(a.type==='cpf') html+=`<div class="notice ${a.cpf_stale?'':'soft'}">CPF is manually maintained and may be inaccurate. Last reconciliation: ${esc(a.last_reconciled || 'never')}.${a.cpf_stale?' Please check your statement and update with /cpf_set.':''}</div>`;
  html+=`<div class="panel"><div class="panel-head"><h2>Account value</h2><strong>SGD ${fmt(a.sgd)}${a.complete?'':' (partial)'}</strong></div><div class="cash-grid">${a.cash.map(c=>`<div class="cash-balance"><small>${esc(c.currency)} CASH</small><strong>${fmt(c.amount)}</strong></div>`).join('') || '<p class="muted">No cash balances recorded.</p>'}</div>`;
  if(a.type==='brokerage') html+=`<h3>Value history</h3>${brokerageChart(a)}<h3>Holdings</h3>${a.holdings.length?`<div class="table-wrap"><table><thead><tr><th>Instrument</th><th>Class</th><th class="num">Quantity</th><th class="num">Close</th><th class="num">High/low midpoint</th><th class="num">Market value</th><th class="num">SGD value</th></tr></thead><tbody>${a.holdings.map(h=>`<tr><td>${esc(h.symbol)}<small>${esc(h.exchange)} · ${esc(h.currency)}</small></td><td>${badge(h.asset_class)}</td><td class="num">${fmt(h.quantity)}</td><td class="num">${fmt(h.quote?.close)}<small>${esc(h.quote?.date || 'Awaiting data')}</small></td><td class="num">${fmt(h.quote?.midpoint)}</td><td class="num">${fmt(h.market_value)}</td><td class="num">${fmt(h.sgd)}</td></tr>`).join('')}</tbody></table></div>`:empty('No holdings recorded','Use /opening_holding for existing investments, or /buy for new trades.')}<p class="section-note">Daily close from Yahoo Finance; exchange-certified finality is unavailable. Price refresh waits 30 minutes after session close. Stock splits must be recorded using /split.</p>`;
  html+='</div><div class="panel"><div class="panel-head"><h2>Account history</h2></div>'+historyTable(data.history.filter(e=>e.data.account===id || e.data.destination===id || e.data.allocations?.some(x=>x.account===id)))+'</div>';
  return html;
}
function loansPage(){
  return `<div class="panel-head"><span class="muted">HDB housing loans · SGD</span><button data-action="add-loan">+ Add loan</button></div><div class="notice soft">Projections use monthly-rest interest and your entered rates. Compare against your HDB statement. Property values are excluded from assets. Scheduled payments do not move money.</div>${data.loans.length?data.loans.map(l=>`<div class="panel"><div class="panel-head"><h2>${esc(l.name)}</h2><div class="toolbar"><button class="secondary small-button" data-action="rate" data-id="${esc(l.id)}">Add rate change</button><button class="small-button" data-action="repay" data-id="${esc(l.id)}">Record repayment</button></div></div><div class="loan-stats"><div><small>Outstanding principal</small><strong>SGD ${fmt(l.outstanding_principal)}</strong></div><div><small>Accrued unpaid interest</small><strong>SGD ${fmt(l.accrued_interest)}</strong></div><div><small>Monthly installment</small><strong>SGD ${fmt(l.installment)}</strong></div></div><p class="section-note">Opening balance as of ${esc(l.as_of)}. ${l.schedule_complete?`Projected ${l.schedule.length} remaining payments, ending ${esc(l.schedule.at(-1)?.date || 'now')}.`:'Payment does not clear the loan within 50 years; review the installment.'}</p><details><summary>Projected repayment schedule</summary><div class="schedule-scroll table-wrap"><table><thead><tr><th>Due date</th><th class="num">Payment</th><th class="num">Interest</th><th class="num">Principal</th><th class="num">Remaining debt</th></tr></thead><tbody>${l.schedule.map(r=>`<tr><td>${esc(r.date)}</td><td class="num">${fmt(r.payment)}</td><td class="num">${fmt(r.interest)}</td><td class="num">${fmt(r.principal)}</td><td class="num">${fmt(r.remaining)}</td></tr>`).join('')}</tbody></table></div></details><details><summary>Interest rates and payment history</summary><p class="section-note">${l.rates.map(r=>`${esc(r.rate)}% from ${esc(r.date)}`).join(' · ')}</p><div class="table-wrap"><table><thead><tr><th>Date</th><th>Type</th><th class="num">Amount</th><th>Action</th></tr></thead><tbody>${l.history.map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.kind)}</td><td class="num">${fmt(r.amount)}</td><td>${r.id?`<button class="text-button" data-action="void-payment" data-id="${esc(r.id)}">Cancel payment</button> · <button class="text-button" data-action="correct-payment" data-id="${esc(r.id)}" data-loan="${esc(l.id)}">Correct</button>`:''}</td></tr>`).join('')}</tbody></table></div></details></div>`).join(''): `<div class="panel">${empty('Plan your repayments','Add your existing HDB loan with its outstanding balance, rate, and monthly installment.')}</div>`}`;
}
function frequencyOptions(selected){
  return ['monthly','quarterly','semiannual','annual'].map(value=>`<option value="${value}" ${selected===value?'selected':''}>${value.charAt(0).toUpperCase()+value.slice(1)}</option>`).join('');
}
function render(){
  if(!data)return;
  const {page,id}=route();
  const focusedSearch=document.activeElement?.id==='stock-search-input',selection=focusedSearch?{start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd}:null;
  const titles={overview:['THE BIG PICTURE','Your financial overview','Every account. One place.'],stocks:['MARKET RESEARCH','Stocks','Completed-session prices and reported fundamentals.'],'credit-cards':['YOUR CREDIT CARDS','Credit-card dashboard','Balances, purchases, refunds, and payments in SGD.'],accounts:['YOUR ACCOUNTS','A home for every account','Balances and holdings, in their original currencies.'],loans:['YOUR COMMITMENTS','Loans & repayments','See what is outstanding and what comes next.'],calculator:['PLAN AHEAD','Present & Future Value','Explore contributions, withdrawals, returns, and time.'],'data-management':['YOUR DATA','Backup and restore','Export or replace all application data.'],activity:['YOUR FINANCIAL RECORD','Transaction history','A complete record, including corrections and cancellations.']};
  const t=titles[page] || titles.overview;
  $('#page-eyebrow').textContent=t[0]; $('#page-title').textContent=t[1]; $('#page-subtitle').textContent=t[2];
  $('#crumb').textContent=page==='credit-cards'?'Credit cards':page.charAt(0).toUpperCase()+page.slice(1);
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===page));
  $('#today').textContent=new Date(data.as_of+'T12:00:00').toLocaleDateString('en-SG',{day:'numeric',month:'short',year:'numeric'});
  const failures=Object.entries(data.provider_status).filter(([,v])=>!v.ok).map(([k,v])=>`${k}: ${v.message}`);
  const warnings=['credit-cards','calculator','stocks'].includes(page)?[]:[...data.missing,...data.warnings,...failures];
  $('#notice').innerHTML=warnings.length?`<div class="notice"><strong>${data.complete?'Some valuations need attention':'Totals are incomplete'}</strong><ul>${warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>`:'';
  $('#content').innerHTML=page==='stocks'?stocksPage(id):page==='credit-cards'?creditCardsPage(id):page==='accounts'?accountsPage(id):page==='loans'?loansPage():page==='calculator'?calculatorPage():page==='data-management'?backupPage():page==='activity'?`<div class="panel">${historyTable(data.history)}</div>`:overview();
  document.querySelectorAll('.brokerage-chart').forEach(chart=>drawBrokerageChart(chart.dataset.account));
  if(focusedSearch){const input=$('#stock-search-input');input?.focus({preventScroll:true});if(input&&selection)input.setSelectionRange(selection.start,selection.end);}
}
async function load(){
  data=await api('/dashboard'); revision=data.revision; dataDate=data.as_of; lastLoaded=Date.now();
  $('#login').hidden=true; $('#shell').hidden=false;
  $('#connection').textContent='● Connected · updated '+new Date().toLocaleTimeString('en-SG',{hour:'2-digit',minute:'2-digit'});
  render();
}
function input(name,label,type='text',value='',extra=''){return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra} required></label>`;}
function openModal(title,html){$('#modal-title').textContent=title;$('#modal-body').innerHTML=html;$('#modal').showModal();}
function formFooter(label){return `<p id="form-error" class="error" role="alert"></p><div class="form-actions"><button type="button" class="secondary" data-action="close">Cancel</button><button type="submit">${label}</button></div>`;}
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
  $('#payment-form').onsubmit=e=>{e.preventDefault();const f=e.target;const p={loan:id,amount:f.elements.amount.value,date:f.elements.date.value,allocations:[...f.querySelectorAll('.allocation-row')].map(r=>({account:r.querySelector('select').value,amount:r.querySelector('input').value}))}; submitCommand(transaction?'correct':'repayment',transaction?{transaction,changes:p}:p,f);};
}
function rate(id){
  openModal('Record an interest-rate change',`<form id="rate-form"><p class="form-note">Use the first day of the month when the new annual rate applies. Past changes recalculate interest and must not invalidate recorded repayments.</p>${input('date','Effective date','date')}${input('rate','Annual rate (%)','number','','min="0" max="100" step="0.0001"')}${formFooter('Save rate')}</form>`);
  $('#rate-form').onsubmit=e=>{e.preventDefault();submitCommand('loan_rate',{loan:id,...Object.fromEntries(new FormData(e.target))},e.target);};
}
async function submitCommand(command,payload,form){
  const button=form.querySelector('[type=submit]'); button.disabled=true;
  // Keep this key on network retries; create a new key only if the payload changes.
  const signature=JSON.stringify(payload);
  if(form.dataset.signature!==signature){form.dataset.signature=signature;form.dataset.key=crypto.randomUUID();}
  try {await api('/commands/'+command,{method:'POST',headers:{'Idempotency-Key':form.dataset.key},body:signature});$('#modal').close();toast('Saved. Your balances are up to date.');await load();}
  catch(error){$('#form-error').textContent=error.message;}
  finally{button.disabled=false;}
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-action]');if(!b)return;
  const action=b.dataset.action;
  if(action==='chart-period'){const root=b.closest('.brokerage-chart'),rows=data.portfolio_history[root.dataset.account]||[],state=chartState[root.dataset.account];state.period=b.dataset.value;[state.start,state.end]=historyRange(rows,state.period);drawBrokerageChart(root.dataset.account);}
  if(action==='chart-metric'){const root=b.closest('.brokerage-chart');chartState[root.dataset.account].metric=b.dataset.value;drawBrokerageChart(root.dataset.account);}
  if(action==='stock-search')searchStocks();
  if(action==='backup-export')exportBackup();
  if(action==='backup-sample')downloadBackupTemplate();
  if(action==='backup-confirm')confirmBackupImport();
  if(action==='backup-cancel')cancelBackupImport();
  if(action==='stock-option')chooseStockResult(Number(b.dataset.index));
  if(action==='stock-retry'&&stockState.selected)loadStock(stockState.selected,true);
  if(action==='stock-range'){stockState.range=b.dataset.value;if(stockState.selected)loadStock(stockState.selected,true);}
  if(action==='calculator-plan'){calculatorPlanMode=b.dataset.value;calculatorResult=null;render();}
  if(action==='add-stream'){const form=b.closest('#calculator-form');calculatorStreams=readStreamForms(form);calculatorStreams.push({name:`Cash flow ${calculatorStreams.length+1}`,initial_value:'0',cashflow:'0',cashflow_frequency:'monthly',cashflow_timing:'end',annual_rate:'5',compounding_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan',cashflow_end_years:'',cashflow_end_remainder_months:''});render();}
  if(action==='remove-stream'){const form=b.closest('#calculator-form');calculatorStreams=readStreamForms(form);calculatorStreams.splice(Number(b.dataset.index),1);calculatorResult=null;render();}
  if(action==='add-loan')addLoan();
  if(action==='repay')repay(b.dataset.id);
  if(action==='correct-payment')repay(b.dataset.loan,b.dataset.id);
  if(action==='rate')rate(b.dataset.id);
  if(action==='close')$('#modal').close();
  if(action==='add-allocation')$('#allocations').insertAdjacentHTML('beforeend',allocationRow());
  if(action==='remove-allocation')b.closest('.allocation-row').remove();
  if(action==='void-payment'){
    openModal('Cancel this repayment?',`<form id="void-form"><p>The payment will be reversed, its funding returned to the recorded accounts, and the loan balance recalculated. History is retained.</p>${formFooter('Cancel repayment')}</form>`);
    $('#void-form').onsubmit=e=>{e.preventDefault();submitCommand('void',{transaction:b.dataset.id},e.target);};
  }
});
document.addEventListener('change',e=>{
  if(e.target.id==='stock-exchange'){clearTimeout(stockSearchTimer);searchStocks();return;}
  if(e.target.id==='backup-file'){validateBackup(e.target.files?.[0]||null);return;}
  if(e.target.name==='cashflow_end_mode'){const root=e.target.closest('.cashflow-window'),custom=e.target.value==='custom',fields=root.querySelector('.custom-window-end');fields.hidden=!custom;fields.querySelectorAll('input').forEach(input=>input.disabled=!custom);updateContributionWindow(root);return;}
  if(e.target.name!=='calculation'||!e.target.closest('#calculator-form'))return;
  const pv=e.target.value==='present_value', form=e.target.form;
  const single=form.querySelector('#calculator-initial-label');if(single)single.firstChild.textContent=pv?'Desired future amount (SGD)':'Initial amount (SGD)';
  form.querySelectorAll('.stream-initial-label').forEach(label=>label.firstChild.textContent=pv?'Target value (SGD)':'Initial amount (SGD)');
  form.querySelector('.calculator-submit').textContent=pv?'Calculate present value':'Calculate future value';
});
function updateContributionWindow(root){const section=root.closest('.calculator-stream')||root.closest('form'),values=fieldValues(section);root.querySelector('[data-window-summary]').textContent=contributionWindowSummary(values);root.querySelector('.cashflow-window-error').textContent='';}
document.addEventListener('input',e=>{if(e.target.id==='stock-search-input'){stockState.query=e.target.value;stockState.suggestionsOpen=true;stockState.highlight=-1;clearTimeout(stockSearchTimer);stockSearchTimer=setTimeout(searchStocks,300);e.target.setAttribute('aria-expanded','true');e.target.removeAttribute('aria-activedescendant');}const window=e.target.closest?.('.cashflow-window'),form=e.target.closest?.('#calculator-form');if(window)updateContributionWindow(window);if(form&&(window||e.target.name==='duration_years'||e.target.name==='duration_months'))validateContributionWindows(form);});
document.addEventListener('keydown',e=>{
  if(e.target.id==='stock-search-input'){
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();stockState.suggestionsOpen=true;const count=stockState.results.length;if(count)stockState.highlight=e.key==='ArrowDown'?Math.min(count-1,stockState.highlight+1):stockState.highlight<0?count-1:Math.max(0,stockState.highlight-1);renderStockSearch();return;}
    if(e.key==='Escape'){e.preventDefault();stockState.suggestionsOpen=false;stockState.highlight=-1;renderStockSearch();return;}
    if(e.key==='Enter'){e.preventDefault();clearTimeout(stockSearchTimer);if(stockState.highlight>=0)chooseStockResult(stockState.highlight);else if(stockState.results.length===1)chooseStockResult(0);else if(!stockState.searching&&!stockState.results.length)searchStocks();return;}
  }
  const chart=e.target.closest?.('.candle-chart');if(chart&&(e.key==='ArrowLeft'||e.key==='ArrowRight')){e.preventDefault();const count=stockState.candles?.candles?.length||0,saved=chart.dataset.candleIndex!==undefined&&chart.dataset.candleIndex!==''?Number(chart.dataset.candleIndex):stockState.candleIndex,index=nextCandleIndex(saved,e.key,count);if(index!=null)selectCandle(chart,index,true);}else if(chart&&e.key==='Escape'){clearCandle(chart);chart.dataset.pinned='false';}
});
document.addEventListener('submit',e=>{if(e.target.id==='calculator-form'){e.preventDefault();calculateTimeValue(e.target);}});
document.addEventListener('pointermove',e=>{if(e.target.matches('[data-action="chart-hover"]'))chartHover(e);if(e.target.matches('[data-action="calculator-chart-hover"]'))calculatorChartHover(e);if(e.target.matches('[data-action="stock-candle-hover"]'))candleFromPointer(e,e.pointerType==='touch');});
document.addEventListener('pointerdown',e=>{if(e.target.matches('[data-action="calculator-chart-hover"]')){e.target.setPointerCapture?.(e.pointerId);calculatorChartHover(e);}if(e.target.matches('[data-action="stock-candle-hover"]')){if(e.pointerType==='touch')e.target.setPointerCapture?.(e.pointerId);candleFromPointer(e,true);}});
document.addEventListener('pointerout',e=>{const chart=e.target.closest?.('.candle-chart');if(chart&&!chart.contains(e.relatedTarget)&&chart.dataset.pinned!=='true')clearCandle(chart);});
document.addEventListener('click',e=>{if(!e.target.closest('.stock-search')&&stockState.suggestionsOpen){stockState.suggestionsOpen=false;stockState.highlight=-1;renderStockSearch();}});
document.addEventListener('wheel',e=>{const root=e.target.closest?.('.brokerage-chart');if(!root)return;e.preventDefault();const rows=data.portfolio_history[root.dataset.account]||[],state=chartState[root.dataset.account],span=state.end-state.start+1;if(rows.length<2)return;const factor=(e.deltaY<0)?0.8:1.25,next=Math.min(rows.length,Math.max(2,Math.round(span*factor))),rect=root.querySelector('svg').getBoundingClientRect(),focus=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width)),center=state.start+Math.round(focus*(span-1));state.start=Math.max(0,Math.min(rows.length-next,center-Math.round(focus*(next-1))));state.end=state.start+next-1;state.period='custom';drawBrokerageChart(root.dataset.account);},{passive:false});
$('#close-modal').onclick=()=>$('#modal').close();
$('#login-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('#login-error').textContent='';try{const s=await api('/login',{method:'POST',body:JSON.stringify({password:e.target.elements.password.value})});csrf=s.csrf;e.target.reset();await load();}catch(error){$('#login-error').textContent=error.message;}finally{b.disabled=false;}};
$('#logout').onclick=async()=>{try{await api('/logout',{method:'POST'});showLogin();}catch(error){toast(error.message);}};
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0);});
async function poll(force=false){
  if(!csrf||polling||document.hidden)return;
  polling=true;
  try{const r=await api('/revision');const current=route(), brokerageOpen=current.page==='accounts'&&data?.accounts.some(a=>a.id===current.id&&a.type==='brokerage');if(force||r.revision!==revision||r.date!==dataDate||(brokerageOpen&&Date.now()-lastLoaded>=30000))await load();else $('#connection').textContent='● Connected · checked '+new Date().toLocaleTimeString('en-SG',{hour:'2-digit',minute:'2-digit'});}
  catch(error){$('#connection').textContent='○ Offline · showing last received values';}
  finally{polling=false;}
}
window.addEventListener('focus',()=>poll(true));setInterval(poll,5000);
(async()=>{try{const session=await api('/session');csrf=session.csrf;await load();}catch{showLogin();}})();
