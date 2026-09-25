const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const context=vm.createContext({console,URLSearchParams,AbortController,queueMicrotask:()=>{},crypto:{randomUUID:()=> 'idempotency-test'}});
vm.runInContext(fs.readFileSync('web/activity.js','utf8'),context);
const shared=fs.readFileSync('web/app.js','utf8').split("document.addEventListener('click'")[0];
vm.runInContext(shared,context);

const fixture={
  accounts:[
    {id:'bank-1',name:'Everyday account',type:'bank',currency:'SGD',archived:false,cash:[]},
    {id:'brokerage-1',name:'Investing account',type:'brokerage',currency:'USD',archived:false,cash:[]},
    {id:'cpf-1',name:'CPF OA',type:'cpf',currency:'SGD',archived:false,cash:[]},
    {id:'archived-1',name:'Old account',type:'bank',currency:'SGD',archived:true,cash:[]},
  ],
  credit_accounts:[{id:'card-1',name:'Main card',cards:[{id:'visa-1',name:'Visa'}]}],
  loans:[{id:'loan-1',name:'Home loan'}],
  history:[
    {id:'tx-cash',kind:'opening_cash',date:'2026-01-01',order:1,status:'active',data:{account:'bank-1',currency:'SGD',amount:'1000.00',date:'2026-01-01'},can_edit:true,can_void:true,editable_fields:['account','amount','currency','date']},
    {id:'tx-transfer',kind:'transfer',date:'2026-02-02',order:2,status:'active',data:{account:'bank-1',destination:'brokerage-1',currency:'SGD',to_currency:'USD',amount:'100.00',received:'75.00',rate:'0.75',rate_direction:'USD per SGD',date:'2026-02-02'},can_edit:true,can_void:true,editable_fields:['account','destination','currency','to_currency','amount','received','date']},
    {id:'tx-buy',kind:'buy',date:'2026-03-03',order:3,status:'active',data:{account:'brokerage-1',exchange:'NASDAQ',symbol:'ABC',asset_class:'equity',currency:'USD',quantity:'2',price:'10.00',date:'2026-03-03'},can_edit:true,can_void:true,editable_fields:['account','exchange','symbol','asset_class','currency','quantity','price','date']},
    {id:'tx-correction',kind:'deposit',date:'2026-04-04',order:4,status:'active',replaces:'tx-old',data:{account:'bank-1',currency:'SGD',amount:'20.00',description:'Corrected deposit',date:'2026-04-04'},can_edit:true,can_void:true,editable_fields:['account','amount','currency','date','description']},
    {id:'tx-old',kind:'deposit',date:'2026-04-03',order:3,status:'superseded',replaced_by:'tx-correction',data:{account:'bank-1',currency:'SGD',amount:'2.00',description:'Deposit',date:'2026-04-03'},can_edit:false,can_void:false,editable_fields:[]},
    {id:'tx-cancelled',kind:'withdraw',date:'2026-04-02',order:2,status:'void',data:{account:'bank-1',currency:'SGD',amount:'3.00',date:'2026-04-02'},can_edit:false,can_void:false,editable_fields:[]},
    {id:'tx-archived',kind:'deposit',date:'2026-04-01',order:1,status:'active',data:{account:'archived-1',currency:'SGD',amount:'4.00',date:'2026-04-01'},can_edit:false,can_void:false,editable_fields:[]},
    {id:'tx-loan-open',kind:'loan_disbursement',date:'2026-01-01',order:0,status:'active',data:{account:'bank-1',amount:'50.00',loan:'loan-1',currency:'SGD'},can_edit:false,can_void:false,editable_fields:[]},
  ],
};
context.testData=fixture;
vm.runInContext('data=testData',context);
vm.runInContext(fs.readFileSync('web/cashflow.js','utf8'),context);

const table=vm.runInContext('historyTable(data.history,{manage:true})',context);
for(const label of ['Search','Account','Transaction type','Status','From','To','Clear filters','Edit','Cancel'])assert(table.includes(label));
assert(table.includes('Everyday account'));
assert(table.includes('Investing account'));
assert(table.includes('later correction'));
assert(table.includes('earlier transaction'));
assert(table.includes('view retained entry'));
assert(table.includes('Loan opening proceeds are linked to the loan'));
assert(table.includes('uses archived account'));
assert(table.includes('only active entries can be edited'));
assert(table.includes('transaction-tx-cash'));
const orderedIds=Array.from(vm.runInContext('filteredHistory(data.history).map(event=>event.id)',context));
assert.deepEqual(orderedIds.slice(0,4),['tx-correction','tx-old','tx-cancelled','tx-archived'],'transaction history is ordered newest date first');
const compactHistory=vm.runInContext('historyTable(data.history)',context);
assert(compactHistory.indexOf('Reference tx-correction')<compactHistory.indexOf('Reference tx-old'),'read-only history tables also put the latest transaction first');

vm.runInContext(`historyFilters={query:'USD 75.00',account:'',kind:'',status:'',from:'',to:''}`,context);
assert.deepEqual(Array.from(vm.runInContext('filteredHistory(data.history).map(event=>event.id)',context)),['tx-transfer']);
vm.runInContext(`historyFilters={query:'',account:'bank-1',kind:'deposit',status:'active',from:'2026-04-01',to:'2026-04-30'}`,context);
assert.deepEqual(Array.from(vm.runInContext('filteredHistory(data.history).map(event=>event.id)',context)),['tx-correction']);
vm.runInContext(`historyFilters={query:'',account:'',kind:'',status:'void',from:'',to:''}`,context);
assert.deepEqual(Array.from(vm.runInContext('filteredHistory(data.history).map(event=>event.id)',context)),['tx-cancelled']);

for(const kind of ['opening_cash','deposit','withdraw','cpf_set','buy','sell','opening_holding','split','transfer','credit_purchase','credit_refund','credit_payment']){
  const source=kind==='buy'?fixture.history.find(event=>event.id==='tx-buy'):{id:`kind-${kind}`,kind,date:'2026-01-01',status:'active',data:{account:kind==='cpf_set'?'cpf-1':kind==='buy'||kind==='sell'||kind==='opening_holding'||kind==='split'?'brokerage-1':'bank-1',credit_account:'card-1',funding_account:'bank-1',loan:'loan-1',destination:'brokerage-1',currency:'SGD',to_currency:'USD',amount:'1.00',quantity:'1',price:kind==='opening_holding'?'unknown':'1.00',exchange:'NASDAQ',symbol:'ABC',asset_class:'equity',ratio:'2',received:'1.00',card:'visa-1',description:'Example',date:'2026-01-01',allocations:[{account:'bank-1',amount:'1.00'}]},can_edit:true,can_void:true,editable_fields:[]};
  context.testEvent=source;
  const rendered=vm.runInContext('transactionEditFields(testEvent)',context);
  assert(rendered.length,`${kind} has an editor`);
}

const transferFields=vm.runInContext('transactionEditFields(data.history.find(event=>event.id===\'tx-transfer\'))',context);
assert(transferFields.includes('name="amount"'));
assert(transferFields.includes('name="received"'));
assert(!transferFields.includes('rate_direction'));
const creditPurchase={id:'tx-card',kind:'credit_purchase',date:'2026-06-01',status:'active',data:{credit_account:'card-1',card:'visa-1',description:'Lunch',amount:'10.00',date:'2026-06-01'}};
context.creditPurchase=creditPurchase;
const creditFields=vm.runInContext('transactionEditFields(creditPurchase)',context);
assert(creditFields.includes('Main card'));
assert(creditFields.includes('name="card"'));
assert(creditFields.includes('name="credit_account"'));
assert.equal(vm.runInContext("transactionRestriction({...creditPurchase,can_edit:false},'edited')",context),'This transaction is locked against editing by the ledger.');
assert.equal(vm.runInContext("transactionRestriction({kind:'credit_set',status:'active',data:{},can_edit:true},'edited')",context),'This legacy combined balance entry is read-only; correct the underlying purchase, refund, or payment instead.');
assert(fs.readFileSync('web/index.html','utf8').includes('/activity.js'));
assert(fs.readFileSync('web/index.html','utf8').includes('/cashflow.js'));
const dockerfile=fs.readFileSync('web/Dockerfile','utf8');
assert(dockerfile.includes('activity.js'));
assert(dockerfile.includes('activity.css'));
assert(dockerfile.includes('cashflow.js'));

const cashflowFixture={currency:'SGD',months:[{month:'2026-09',inflow:'1200.00',outflow:'350.00',net:'850.00',internal_transfer_in:'500.00',internal_transfer_out:'500.00',by_kind:[{kind:'deposit',inflow:'1200.00',outflow:'0',internal_transfer_in:'0',internal_transfer_out:'0',transactions:[{id:'tx-correction',date:'2026-09-02',account:'bank-1',account_name:'Everyday account',amount:'1200.00',description:'Salary'}]},{kind:'transfer',inflow:'0',outflow:'0',internal_transfer_in:'500.00',internal_transfer_out:'500.00',transactions:[{id:'tx-transfer',date:'2026-09-03',account:'bank-1',account_name:'Everyday account',amount:'500.00',direction:'out'},{id:'tx-transfer',date:'2026-09-03',account:'bank-2',account_name:'Savings',amount:'500.00',direction:'in'}]}],by_account:[{account:'bank-1',account_name:'Everyday account',inflow:'1200.00',outflow:'350.00',internal_transfer_in:'0',internal_transfer_out:'500.00',transactions:[{id:'tx-correction',kind:'deposit',date:'2026-09-02',amount:'1200.00'},{id:'tx-transfer',kind:'transfer',date:'2026-09-03',amount:'500.00',direction:'out'}]}]}]};
context.cashflowTestData=cashflowFixture;
const cashflowHtml=vm.runInContext("bankCashflowCurrencyCard(cashflowTestData,'2026-09',true,'2026-09-25',true)",context);
assert(cashflowHtml.includes('SGD 1,200.00'));
assert(cashflowHtml.includes('SGD 850.00'));
assert(cashflowHtml.includes('Current month · partial'));
assert(cashflowHtml.includes('Monthly overview · SGD'));
assert(cashflowHtml.includes('data-action="cashflow-month" data-month="2026-09"'));
assert(cashflowHtml.includes('Internal bank transfers are excluded from net cash movement'));
assert(cashflowHtml.includes('data-action="cashflow-event" data-id="tx-transfer"'));
assert(vm.runInContext('bankCashflowPage()',context).includes('Brokerage and CPF activity are excluded'));
assert.deepEqual(Array.from(vm.runInContext("cashflowAvailableMonths({as_of:'2026-09-25',currencies:{SGD:cashflowTestData}})",context)),['2026-09']);
fixture.as_of='2026-09-25';
const usdFixture={currency:'USD',months:[{month:'2026-09',inflow:'4.25',outflow:'0',net:'4.25',internal_transfer_in:'0',internal_transfer_out:'0',by_kind:[],by_account:[]}]};
context.cashflowPayload={as_of:'2026-09-25',selection:{type:'last_12_months',year:null,start_month:'2025-10',end_month:'2026-09',current_month_partial:true},currencies:{SGD:cashflowFixture,USD:usdFixture}};
const cashflowHost={innerHTML:''};
context.document={querySelector(selector){return selector==='#bank-cashflow-content'?cashflowHost:null;}};
vm.runInContext("bankCashflow=cashflowPayload;bankCashflowYear='last12';bankCashflowMonth='2026-09';renderBankCashflow()",context);
assert(cashflowHost.innerHTML.includes('Monthly overview · SGD'));
assert(cashflowHost.innerHTML.includes('Monthly overview · USD'));
assert(cashflowHost.innerHTML.includes('USD 4.25'));

class FormValues{constructor(form){return Object.entries(form.submitted)[Symbol.iterator]();}}
context.FormData=FormValues;
context.openModal=()=>{};
const transferEvent=fixture.history.find(event=>event.id==='tx-transfer');
const editorForm={dataset:{},submitted:{amount:'120.00',received:'90.00',date:transferEvent.date,rate:'0.75',rate_direction:'USD per SGD'}};
context.document={querySelector(selector){return selector==='#transaction-edit-form'?editorForm:null;}};
let submittedCommand;
const actualSubmitCommand=vm.runInContext('submitCommand',context);
context.submitCommand=(command,payload,form)=>{submittedCommand={command,payload,form};};
vm.runInContext(`openTransactionEdit('tx-transfer')`,context);
editorForm.onsubmit({preventDefault(){},target:editorForm});
assert.equal(submittedCommand.command,'correct');
assert.deepEqual(JSON.parse(JSON.stringify(submittedCommand.payload)),{transaction:'tx-transfer',changes:{amount:'120.00',received:'90.00'}});
assert.equal(editorForm.dataset.transactionManagement,'true');

fixture.history.push({id:'tx-repayment',kind:'repayment',date:'2026-05-01',status:'active',data:{loan:'loan-1',amount:'50.00',date:'2026-05-01',allocations:[{account:'bank-1',amount:'50.00'}]},can_edit:true,can_void:true,editable_fields:['loan','amount','date','allocations']});
const allocationRow={querySelector(selector){return selector==='select'?{value:'bank-1'}:{value:'55.00'};}};
const paymentForm={dataset:{},elements:{amount:{value:'55.00'},date:{value:'2026-05-02'}},querySelectorAll(){return [allocationRow];}};
context.document={querySelector(selector){return selector==='#payment-form'?paymentForm:null;}};
  vm.runInContext(`repay('loan-1','tx-repayment')`,context);
  paymentForm.onsubmit({preventDefault(){},target:paymentForm});
  assert.equal(submittedCommand.command,'correct');
  assert.deepEqual(JSON.parse(JSON.stringify(submittedCommand.payload)),{transaction:'tx-repayment',changes:{amount:'55.00',allocations:[{account:'bank-1',amount:'55.00'}],date:'2026-05-02'}});
  context.submitCommand=actualSubmitCommand;

(async()=>{
  const formError={textContent:''},modal={close(){}},toast={hidden:true,textContent:''},row={focus(){this.focused=true;},scrollIntoView(){}};
  context.document={querySelector(selector){if(selector==='#form-error')return formError;if(selector==='#modal')return modal;if(selector==='#toast')return toast;return null;},getElementById(){return row;}};
  context.rejectCommand=async()=>{throw Object.assign(Error('Transaction is no longer active; refresh history and retry'),{status:409});};
  context.loadCount=0;
  vm.runInContext(`api=rejectCommand;load=async()=>{loadCount+=1}`,context);
  assert.equal(vm.runInContext('api===rejectCommand',context),true);
  const conflictForm={dataset:{transactionManagement:'true',transactionId:'tx-transfer'},querySelector(){return {disabled:false};}};
  context.conflictForm=conflictForm;
  await vm.runInContext(`submitCommand('correct',{transaction:'tx-transfer',changes:{amount:'120.00'}},conflictForm)`,context);
  assert(formError.textContent.includes('changed elsewhere'));
  assert.equal(context.loadCount,1,'a stale correction refreshes the dashboard history');
  console.log('PASS: Transaction-history filters and editing, bank cash-flow breakdowns, currency separation, and stale conflicts');
})().catch(error=>{console.error(error);process.exitCode=1;});
