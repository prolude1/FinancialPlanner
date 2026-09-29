/** Dashboard rendering functions with explicit access to shared shell helpers. */
export function createDashboardPages(dependencies) {
  const { $, esc, fmt, labels, colors, chartState, badge, empty, historyTable, latestTransactionsFirst } = dependencies;
  let data = null;

  function setData(snapshot) {
    data = snapshot;
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
      <p class="section-note">Record new purchases, refunds, and payments through your private Telegram bot. Review and edit existing transactions in Transaction history. Monthly totals include active entries only.</p>
      <div class="panel"><div class="panel-head"><h2>Payment history</h2></div>${historyTable(events.filter(e=>e.kind==='credit_payment'))}</div>
      <div class="panel"><div class="panel-head"><h2>Card activity & balance updates</h2></div>${historyTable(events.filter(e=>e.kind!=='credit_payment'))}</div>`;
  }
  function overview(){
    const accounts=data.accounts.filter(a=>!a.archived);
    return summary()+`<div class="main-grid">${allocation()}<div class="panel"><div class="panel-head"><h2>Your accounts</h2><a href="#accounts">View all ↗</a></div>${accounts.length?accountGroups(accounts):empty('Bring your accounts together','Send <code>/account_add</code> to your private bot, then record your opening balances.')}</div></div>${creditCards()}<div class="panel"><div class="panel-head"><h2>Recent transactions</h2><a href="#activity">View transaction history ↗</a></div>${historyTable(latestTransactionsFirst(data.history).slice(0,6))}</div>`;
  }
  function accountsPage(id){
    if(!id) return `<div class="notice soft">Create new accounts through Telegram. Existing account transactions can be reviewed and managed in Transaction history.</div><div class="panel">${data.accounts.length?accountGroups(data.accounts):empty('No accounts yet','Start with <code>/account_add</code> in Telegram.')}</div>`;
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

  return {
    setData,
    historyRange,
    drawBrokerageChart,
    chartHover,
    overview,
    creditCardsPage,
    accountsPage,
    loansPage,
  };
}
