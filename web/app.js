/* Read-only asset screens and shared command-backed loan forms. No client ledger. */
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (v, digits=2) => v == null ? 'Unavailable' : Number(v).toLocaleString('en-SG', {minimumFractionDigits:digits, maximumFractionDigits:digits});
const labels = {cash:'Cash', equity:'Equities', etf:'ETFs', cpf:'CPF'};
const colors = {cash:'#b9d291', equity:'#1f6152', etf:'#78a58d', cpf:'#e4d7ad'};
let data, revision=-1, dataDate='', polling=false, lastLoaded=0, me=null;
const chartState={};
const PlannerAuth=globalThis.PlannerAuth||{configured:false,authenticated:false,request:async()=>{throw new Error('Keycloak sign-in is not configured.');},init:async()=>false,login:async()=>{},logout:async()=>{}};
let dashboardPages, loanForms;

let apiClientPromise;
async function api(path, options={}) {
  apiClientPromise ||= import('./core/api-client.js').then(({AuthenticatedApiClient}) =>
    new AuthenticatedApiClient({authenticatedFetch:(...args)=>PlannerAuth.request(...args)}));
  const apiClient=await apiClientPromise;
  return apiClient.request(path, options);
}
function showLogin(message=''){
  data=null;me=null;revision=-1;dataDate='';bankCashflow=null;
  $('#shell').hidden=true;$('#login').hidden=false;$('#content').replaceChildren();$('#notice').replaceChildren();
  $('#modal').close();$('#modal-body').replaceChildren();
  $('#claim-owner').hidden=true;$('#owner-claim-state').hidden=true;
  backupState.preview=null;backupState.file=null;backupState.fileName='';backupState.confirming=false;
  $('#keycloak-sign-in').disabled=!PlannerAuth.configured;
  $('#keycloak-sign-in').textContent=PlannerAuth.configured?'Sign in with Keycloak ↗':'Keycloak sign-in is not configured';
  $('#login-error').textContent=message;
  $('#login-status').textContent=PlannerAuth.configured?'Use your Keycloak account to open your personal workspace.':'An administrator must set the public Keycloak issuer, realm, and SPA client ID.';
}
PlannerAuth.onUnauthorized=()=>showLogin('Your Keycloak session expired. Sign in again to continue.');
function toast(message){ $('#toast').textContent=message; $('#toast').hidden=false; setTimeout(()=>$('#toast').hidden=true,4500); }
function route(){ const [page,id] = location.hash.slice(1).split('/'); return {page:page || 'overview',id}; }
function badge(text, warning=false){ return `<span class="badge ${warning?'warning':''}">${esc(text)}</span>`; }
function empty(title, text){return `<div class="empty"><strong>${title}</strong><p>${text}</p></div>`;}
function accountName(id){ return data.accounts.find(a=>a.id===id)?.name || id; }
function creditAccountName(id){ return data.credit_accounts?.find(a=>a.id===id)?.name || id; }
async function initializeFeatures(){
  const [{createDashboardPages},{createLoanForms}]=await Promise.all([
    import('./features/dashboard/dashboard-pages.js'),
    import('./features/loans/loan-forms.js'),
  ]);
  dashboardPages=createDashboardPages({$,esc,fmt,labels,colors,chartState,badge,empty,historyTable,latestTransactionsFirst});
  loanForms=createLoanForms({$,esc,fmt,input,openModal,formFooter,submitCommand});
}
function render(){
  if(!data||!dashboardPages||!loanForms)return;
  dashboardPages.setData(data);loanForms.setData(data);
  const {page,id}=route();
  const focusedSearch=document.activeElement?.id==='stock-search-input',selection=focusedSearch?{start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd}:null;
  const titles={overview:['THE BIG PICTURE','Your financial overview','Every account. One place.'],stocks:['MARKET RESEARCH','Stocks','Completed-session prices and reported fundamentals.'],'credit-cards':['YOUR CREDIT CARDS','Credit-card dashboard','Balances, purchases, refunds, and payments in SGD.'],accounts:['YOUR ACCOUNTS','A home for every account','Balances and holdings, in their original currencies.'],loans:['YOUR COMMITMENTS','Loans & repayments','See what is outstanding and what comes next.'],calculator:['PLAN AHEAD','Present & Future Value','Explore contributions, withdrawals, returns, and time.'],'data-management':['YOUR DATA','Backup and restore','Export or replace all application data.'],activity:['YOUR FINANCIAL RECORD','Transaction history','Search, review, correct, or cancel existing transactions. New transactions are still recorded in Telegram.']};
  const t=titles[page] || titles.overview;
  $('#page-eyebrow').textContent=t[0]; $('#page-title').textContent=t[1]; $('#page-subtitle').textContent=t[2];
  $('#crumb').textContent=page==='credit-cards'?'Credit cards':page==='activity'?'Transaction history':page.charAt(0).toUpperCase()+page.slice(1);
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===page));
  $('#today').textContent=new Date(data.as_of+'T12:00:00').toLocaleDateString('en-SG',{day:'numeric',month:'short',year:'numeric'});
  const failures=Object.entries(data.provider_status).filter(([,v])=>!v.ok).map(([k,v])=>`${k}: ${v.message}`);
  const warnings=['credit-cards','calculator','stocks'].includes(page)?[]:[...data.missing,...data.warnings,...failures];
  $('#notice').innerHTML=warnings.length?`<div class="notice"><strong>${data.complete?'Some valuations need attention':'Totals are incomplete'}</strong><ul>${warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>`:'';
  $('#content').innerHTML=page==='stocks'?stocksPage(id):page==='credit-cards'?dashboardPages.creditCardsPage(id):page==='accounts'?dashboardPages.accountsPage(id):page==='loans'?dashboardPages.loansPage():page==='calculator'?calculatorPage():page==='data-management'?backupPage():page==='activity'?activityPage():dashboardPages.overview();
  if(page==='activity'){if(bankCashflow)renderBankCashflow();else loadBankCashflow(bankCashflowYear);}
  document.querySelectorAll('.brokerage-chart').forEach(chart=>dashboardPages.drawBrokerageChart(chart.dataset.account));
  if(focusedSearch){const input=$('#stock-search-input');input?.focus({preventScroll:true});if(input&&selection)input.setSelectionRange(selection.start,selection.end);}
}
async function load(){
  data=await api('/dashboard'); revision=data.revision; dataDate=data.as_of; lastLoaded=Date.now();bankCashflow=null;
  $('#login').hidden=true; $('#shell').hidden=false;
  $('#connection').textContent='● Connected · updated '+new Date().toLocaleTimeString('en-SG',{hour:'2-digit',minute:'2-digit'});
  render();
}
async function loadIdentity(){
  me=await api('/me');
  const claim=me?.legacy_claim;
  if(!claim||typeof claim.available!=='boolean'||typeof claim.completed!=='boolean')throw new Error('The account status response is incomplete. Refresh or contact your administrator.');
  $('#claim-owner').hidden=!claim.available;
  $('#owner-claim-state').hidden=!claim.completed;
  $('#owner-claim-state').textContent=claim.completed?'Existing ledger claimed for this account.':'';
}
function openOwnerClaim(){
  if(!me?.legacy_claim?.available)return;
  openModal('Claim the existing ledger',`<form id="owner-claim-form"><p class="form-note">Enter the one-time code provided for the existing ledger. The code is checked by the server and can only be used once.</p><label>One-time owner-claim code<input name="code" type="password" autocomplete="off" required maxlength="256"></label><p id="owner-claim-error" class="error" role="alert"></p><div class="form-actions"><button type="button" class="secondary" data-action="close">Cancel</button><button type="submit">Claim ledger</button></div></form>`);
  $('#owner-claim-form').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type="submit"]'),code=new FormData(form).get('code').trim();
    if(!code)return;button.disabled=true;$('#owner-claim-error').textContent='';
    try{
      const result=await api('/owner-claim',{method:'POST',body:JSON.stringify({code})});
      if(result?.status!=='claimed')throw new Error('The server did not confirm the ledger claim.');
    }catch(error){$('#owner-claim-error').textContent=error.status===400?'This claim code is invalid, expired, or already used.':error.message;button.disabled=false;return;}
    $('#modal').close();
    try{await loadIdentity();await load();toast('Existing ledger claimed for this Keycloak account.');}
    catch(error){toast(`Ledger claim succeeded, but the dashboard could not refresh: ${error.message}`);}
    finally{button.disabled=false;}
  };
}
function input(name,label,type='text',value='',extra=''){return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra} required></label>`;}
function openModal(title,html){$('#modal-title').textContent=title;$('#modal-body').innerHTML=html;$('#modal').showModal();}
function formFooter(label){return `<p id="form-error" class="error" role="alert"></p><div class="form-actions"><button type="button" class="secondary" data-action="close">Cancel</button><button type="submit">${label}</button></div>`;}
async function submitCommand(command,payload,form){
  const button=form.querySelector('[type=submit]'); button.disabled=true;
  // Keep this key on network retries; create a new key only if the payload changes.
  const signature=JSON.stringify(payload);
  if(form.dataset.signature!==signature){form.dataset.signature=signature;form.dataset.key=crypto.randomUUID();}
  try {await api('/commands/'+command,{method:'POST',headers:{'Idempotency-Key':form.dataset.key},body:signature});$('#modal').close();toast('Saved. Your balances are up to date.');await load();if(form.dataset.transactionManagement==='true'&&form.dataset.transactionId){const row=document.getElementById(`transaction-${form.dataset.transactionId}`);row?.focus();row?.scrollIntoView({behavior:'smooth',block:'center'});}}
  catch(error){$('#form-error').textContent=error.status===409&&form.dataset.transactionManagement==='true'?'This transaction changed elsewhere. History has been refreshed; review the latest entry before trying again.':error.message;if(error.status===409&&form.dataset.transactionManagement==='true'){try{await load();}catch{}}}
  finally{button.disabled=false;}
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-action]');if(!b)return;
  const action=b.dataset.action;
  if(action==='chart-period'){const root=b.closest('.brokerage-chart'),rows=data.portfolio_history[root.dataset.account]||[],state=chartState[root.dataset.account];state.period=b.dataset.value;[state.start,state.end]=dashboardPages.historyRange(rows,state.period);dashboardPages.drawBrokerageChart(root.dataset.account);}
  if(action==='chart-metric'){const root=b.closest('.brokerage-chart');chartState[root.dataset.account].metric=b.dataset.value;dashboardPages.drawBrokerageChart(root.dataset.account);}
  if(action==='stock-search')searchStocks();
  if(action==='backup-export')exportBackup();
  if(action==='backup-sample')downloadBackupTemplate();
  if(action==='history-edit')openTransactionEdit(b.dataset.id);
  if(action==='history-cancel')confirmTransactionCancel(b.dataset.id);
  if(action==='history-related')focusRelatedTransaction(b.dataset.id);
  if(action==='cashflow-event')focusRelatedTransaction(b.dataset.id);
  if(action==='cashflow-month')changeBankCashflowMonth(b.dataset.month);
  if(action==='cashflow-retry')loadBankCashflow(bankCashflowYear);
  if(action==='history-reset')resetHistoryFilters();
  if(action==='backup-confirm')confirmBackupImport();
  if(action==='backup-cancel')cancelBackupImport();
  if(action==='stock-option')chooseStockResult(Number(b.dataset.index));
  if(action==='stock-retry'&&stockState.selected)loadStock(stockState.selected,true);
  if(action==='stock-range'){stockState.range=b.dataset.value;if(stockState.selected)loadStock(stockState.selected,true);}
  if(action==='calculator-plan'){calculatorPlanMode=b.dataset.value;calculatorResult=null;render();}
  if(action==='add-stream'){const form=b.closest('#calculator-form');calculatorStreams=readStreamForms(form);calculatorStreams.push({name:`Cash flow ${calculatorStreams.length+1}`,initial_value:'0',cashflow:'0',cashflow_frequency:'monthly',cashflow_timing:'end',annual_rate:'5',compounding_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan',cashflow_end_years:'',cashflow_end_remainder_months:''});render();}
  if(action==='remove-stream'){const form=b.closest('#calculator-form');calculatorStreams=readStreamForms(form);calculatorStreams.splice(Number(b.dataset.index),1);calculatorResult=null;render();}
  if(action==='add-loan')loanForms.addLoan();
  if(action==='repay')loanForms.repay(b.dataset.id);
  if(action==='correct-payment')loanForms.repay(b.dataset.loan,b.dataset.id);
  if(action==='rate')loanForms.rate(b.dataset.id);
  if(action==='close')$('#modal').close();
  if(action==='add-allocation')$('#allocations').insertAdjacentHTML('beforeend',loanForms.allocationRow());
  if(action==='remove-allocation')b.closest('.allocation-row').remove();
  if(action==='void-payment'){
    openModal('Cancel this repayment?',`<form id="void-form"><p>The payment will be reversed, its funding returned to the recorded accounts, and the loan balance recalculated. History is retained.</p>${formFooter('Cancel repayment')}</form>`);
    $('#void-form').onsubmit=e=>{e.preventDefault();submitCommand('void',{transaction:b.dataset.id},e.target);};
  }
  if(action==='claim-owner')openOwnerClaim();
});
document.addEventListener('change',e=>{
  if(e.target.name==='credit_account'&&e.target.closest?.('#transaction-edit-form')){updateTransactionCardOptions(e.target.closest('#transaction-edit-form'));return;}
  if(e.target.matches?.('[data-history-filter]')){updateHistoryFilter(e.target);return;}
  if(e.target.id==='cashflow-year'){changeBankCashflowYear(e.target.value);return;}
  if(e.target.id==='cashflow-month'){changeBankCashflowMonth(e.target.value);return;}
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
document.addEventListener('input',e=>{if(e.target.matches?.('[data-history-filter]')){updateHistoryFilter(e.target);return;}if(e.target.id==='stock-search-input'){stockState.query=e.target.value;stockState.suggestionsOpen=true;stockState.highlight=-1;clearTimeout(stockSearchTimer);stockSearchTimer=setTimeout(searchStocks,300);e.target.setAttribute('aria-expanded','true');e.target.removeAttribute('aria-activedescendant');}const window=e.target.closest?.('.cashflow-window'),form=e.target.closest?.('#calculator-form');if(window)updateContributionWindow(window);if(form&&(window||e.target.name==='duration_years'||e.target.name==='duration_months'))validateContributionWindows(form);});
document.addEventListener('keydown',e=>{
  if(e.target.id==='stock-search-input'){
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();stockState.suggestionsOpen=true;const count=stockState.results.length;if(count)stockState.highlight=e.key==='ArrowDown'?Math.min(count-1,stockState.highlight+1):stockState.highlight<0?count-1:Math.max(0,stockState.highlight-1);renderStockSearch();return;}
    if(e.key==='Escape'){e.preventDefault();stockState.suggestionsOpen=false;stockState.highlight=-1;renderStockSearch();return;}
    if(e.key==='Enter'){e.preventDefault();clearTimeout(stockSearchTimer);if(stockState.highlight>=0)chooseStockResult(stockState.highlight);else if(stockState.results.length===1)chooseStockResult(0);else if(!stockState.searching&&!stockState.results.length)searchStocks();return;}
  }
  const chart=e.target.closest?.('.candle-chart');if(chart&&(e.key==='ArrowLeft'||e.key==='ArrowRight')){e.preventDefault();const count=stockState.candles?.candles?.length||0,saved=chart.dataset.candleIndex!==undefined&&chart.dataset.candleIndex!==''?Number(chart.dataset.candleIndex):stockState.candleIndex,index=nextCandleIndex(saved,e.key,count);if(index!=null)selectCandle(chart,index,true);}else if(chart&&e.key==='Escape'){clearCandle(chart);chart.dataset.pinned='false';}
});
document.addEventListener('submit',e=>{if(e.target.id==='calculator-form'){e.preventDefault();calculateTimeValue(e.target);}});
document.addEventListener('pointermove',e=>{if(e.target.matches('[data-action="chart-hover"]'))dashboardPages.chartHover(e);if(e.target.matches('[data-action="calculator-chart-hover"]'))calculatorChartHover(e);if(e.target.matches('[data-action="stock-candle-hover"]'))candleFromPointer(e,e.pointerType==='touch');});
document.addEventListener('pointerdown',e=>{if(e.target.matches('[data-action="calculator-chart-hover"]')){e.target.setPointerCapture?.(e.pointerId);calculatorChartHover(e);}if(e.target.matches('[data-action="stock-candle-hover"]')){if(e.pointerType==='touch')e.target.setPointerCapture?.(e.pointerId);candleFromPointer(e,true);}});
document.addEventListener('pointerout',e=>{const chart=e.target.closest?.('.candle-chart');if(chart&&!chart.contains(e.relatedTarget)&&chart.dataset.pinned!=='true')clearCandle(chart);});
document.addEventListener('click',e=>{if(!e.target.closest('.stock-search')&&stockState.suggestionsOpen){stockState.suggestionsOpen=false;stockState.highlight=-1;renderStockSearch();}});
document.addEventListener('wheel',e=>{const root=e.target.closest?.('.brokerage-chart');if(!root)return;e.preventDefault();const rows=data.portfolio_history[root.dataset.account]||[],state=chartState[root.dataset.account],span=state.end-state.start+1;if(rows.length<2)return;const factor=(e.deltaY<0)?0.8:1.25,next=Math.min(rows.length,Math.max(2,Math.round(span*factor))),rect=root.querySelector('svg').getBoundingClientRect(),focus=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width)),center=state.start+Math.round(focus*(span-1));state.start=Math.max(0,Math.min(rows.length-next,center-Math.round(focus*(next-1))));state.end=state.start+next-1;state.period='custom';dashboardPages.drawBrokerageChart(root.dataset.account);},{passive:false});
$('#close-modal').onclick=()=>$('#modal').close();
$('#keycloak-sign-in').onclick=async()=>{const button=$('#keycloak-sign-in');button.disabled=true;$('#login-error').textContent='';try{await PlannerAuth.login();}catch(error){$('#login-error').textContent=error.message||'Unable to start Keycloak sign-in.';button.disabled=false;}};
$('#logout').onclick=async()=>{showLogin();try{await PlannerAuth.logout();}catch(error){$('#login-error').textContent=error.message||'Unable to sign out of Keycloak.';}};
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0);});
async function poll(force=false){
  if(!PlannerAuth.authenticated||polling||document.hidden)return;
  polling=true;
  try{const r=await api('/revision');const current=route(), brokerageOpen=current.page==='accounts'&&data?.accounts.some(a=>a.id===current.id&&a.type==='brokerage');if(force||r.revision!==revision||r.date!==dataDate||(brokerageOpen&&Date.now()-lastLoaded>=30000))await load();else $('#connection').textContent='● Connected · checked '+new Date().toLocaleTimeString('en-SG',{hour:'2-digit',minute:'2-digit'});}
  catch(error){$('#connection').textContent='○ Offline · showing last received values';}
  finally{polling=false;}
}
window.addEventListener('focus',()=>poll(true));setInterval(poll,5000);
(async()=>{
  try{await initializeFeatures();}
  catch(error){showLogin(error.message||'Unable to load dashboard features.');return;}
  if(!PlannerAuth.configured){showLogin();return;}
  $('#login-status').textContent='Checking your Keycloak session…';
  try{
    const authenticated=await PlannerAuth.init();
    if(!authenticated){showLogin();return;}
    await loadIdentity();await load();
  }catch(error){showLogin(error.message||'Unable to load your personal workspace.');}
})();
