const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class MockFormData { constructor(){this.rows=[];} append(...row){this.rows.push(row);} }
const content={innerHTML:''}, reviewTitle={focus(){this.focused=true;}}, fileInput={value:'',focus(){this.focused=true;}};
const downloads=[];
const deferred=[];
const context=vm.createContext({
  console, URLSearchParams, AbortController, FormData:MockFormData,
  setTimeout(callback, delay){deferred.push({callback,delay});},
  URL:{createObjectURL:()=> 'blob:backup-test',revokeObjectURL:url=>downloads.push(['revoke',url])},
  document:{
    querySelector(selector){if(selector==='#content')return content;if(selector==='#backup-review-title')return reviewTitle;if(selector==='#backup-file')return fileInput;return null;},
    createElement(){return {click(){downloads.push(['download',this.download]);},remove(){}}},
    body:{append(){}},
  },
  location:{hash:'#data-management'},
});
const sharedSource=fs.readFileSync('web/app.js','utf8').split("document.addEventListener('click'")[0];
vm.runInContext(fs.readFileSync('web/data-management.js','utf8'),context);
vm.runInContext(sharedSource,context);
vm.runInContext(`render=()=>{};load=async()=>{};api=async()=>({})`,context);

const counts={planner_state:1,instrument_catalog:120,portfolio_history:4,stock_cache:10};
const preview={archive_sha256:'abc123',current_revision:7,current_etag:'etag456',confirmation_token:'one-time-secret',
  exported_at:'2026-09-25T01:02:03Z',format_version:1,app_version:'1.0',schema_version:2,
  comparison:{current:counts,uploaded:{planner_state:1,instrument_catalog:121,portfolio_history:4,stock_cache:10}},warnings:['Older schema version']};
(async()=>{
  let calls=[];
  const managementPage=vm.runInContext('backupPage()',context);
  assert(managementPage.includes('Export Excel backup'));
  assert(managementPage.includes('Download sample workbook'));
  assert(managementPage.includes('synthetic example data and can be restored'));
  assert(managementPage.includes('accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"'));
  assert(managementPage.includes('restore the application from a backup workbook'));
  assert(!/\.zip|JSON/i.test(managementPage),'management UI contains no ZIP or JSON file guidance');
  const managementCss=fs.readFileSync('web/data-management.css','utf8');
  assert(managementCss.includes('.data-actions button,.data-actions .file-picker'));
  assert(managementCss.includes('height:46px'));
  assert(managementCss.includes('padding:0 18px'));
  assert(managementCss.includes('font-size:14px'));
  const pickerRule=managementCss.split('\n.file-picker{')[1].split('.file-picker input{')[0];
  assert(!pickerRule.includes('padding:'));
  assert(!pickerRule.includes('font-size:'));

  context.api=async(path,options={})=>{calls.push([path,options]);if(path.endsWith('/validate'))return preview;if(path.endsWith('/commit'))return {ok:true};if(path==='/data-export')return {headers:{get:key=>key==='content-disposition'?'attachment; filename="financial-planner-export-20260925-120000.xlsx"':null},blob:async()=>({})};if(path==='/data-template')return {headers:{get:key=>key==='content-disposition'?'attachment; filename="financial-planner-data-template.xlsx"':null},blob:async()=>({})};throw Error(path);};

  await vm.runInContext(`validateBackup({name:'too-large.xlsx',size:64*1024*1024+1})`,context);
  assert.equal(calls.length,0,'oversize archives are rejected before network upload');
  await vm.runInContext(`validateBackup({name:'not-a-workbook.zip',size:100})`,context);
  assert.equal(calls.length,0,'non-XLSX files are rejected before upload');

  let imports=0;
  const originalApi=context.api;
  context.api=async(path,options={})=>{calls.push([path,options]);if(path.endsWith('/validate'))return preview;if(path.endsWith('/commit')){imports++;return {ok:true};}return originalApi(path,options);};
  await vm.runInContext(`validateBackup({name:'personal-backup.xlsx',size:1024})`,context);
  assert.equal(imports,0,'file selection and validation never commits an import');
  assert.equal(calls.at(-1)[0],'/data-import/validate');
  assert(calls.at(-1)[1].body instanceof MockFormData);
  let review=vm.runInContext('backupPreview(backupState.preview)',context);
  for(const table of Object.keys(counts))assert(review.includes(table),`review includes ${table}`);
  assert(review.includes('120')&&review.includes('121'));
  assert(review.includes('Older schema version'));
  assert(review.includes('replaces all current application data, even if it is the sample workbook with synthetic example data'));
  assert(review.includes('Credit cards and loans'));
  assert(review.includes('This confirmation expires in 10 minutes'));

  context.api=async()=>{throw Error('Network unavailable')};
  await vm.runInContext('exportBackup()',context);
  assert(vm.runInContext('backupState.error',context).includes('Network unavailable'));
  await vm.runInContext(`validateBackup({name:'offline.xlsx',size:100})`,context);
  assert(vm.runInContext('backupState.error',context).includes('Network unavailable'));

  context.api=async()=>{throw Object.assign(Error('Workbook is invalid'),{status:422});};
  await vm.runInContext(`validateBackup({name:'invalid.xlsx',size:100})`,context);
  assert.equal(vm.runInContext('backupState.preview',context),null);
  assert.equal(imports,0);
  assert(vm.runInContext('backupState.error',context).includes('Workbook is invalid'));

  await vm.runInContext('cancelBackupImport()',context);
  assert.equal(imports,0,'cancel leaves server data unchanged');
  assert.equal(vm.runInContext('backupState.preview',context),null);

  context.api=async(path,options={})=>{calls.push([path,options]);if(path.endsWith('/validate'))return preview;if(path.endsWith('/commit')){imports++;return {ok:true};}return originalApi(path,options);};
  await vm.runInContext(`validateBackup({name:'personal-backup.xlsx',size:1024})`,context);
  await vm.runInContext('confirmBackupImport()',context);
  assert.equal(imports,1);
  const body=JSON.parse(calls.filter(([path])=>path.endsWith('/commit')).at(-1)[1].body);
  assert.deepEqual(body,{archive_sha256:'abc123',current_revision:7,current_etag:'etag456',confirmation_token:'one-time-secret'});
  assert.equal(vm.runInContext('backupState.preview',context),null);

  await vm.runInContext(`validateBackup({name:'ambiguous.xlsx',size:20})`,context);
  context.api=async path=>{if(path.endsWith('/commit'))throw Error('Connection lost');if(path.endsWith('/validate'))return preview;};
  await vm.runInContext('confirmBackupImport()',context);
  assert(vm.runInContext('backupState.status',context).includes('status is unknown'));
  assert(vm.runInContext('backupState.status',context).includes('reload and verify'));
  assert.equal(vm.runInContext('backupState.preview',context),null,'ambiguous commit cannot be retried with the old token');

  await vm.runInContext(`validateBackup({name:'stale.xlsx',size:20})`,context);
  context.api=async path=>{if(path.endsWith('/commit'))throw Object.assign(Error('stale'),{status:409});if(path.endsWith('/validate'))return preview;};
  await vm.runInContext('confirmBackupImport()',context);
  assert.equal(vm.runInContext('backupState.preview',context),null);
  assert(vm.runInContext('backupState.error',context).includes('expired or current data changed'));

  context.api=async path=>{calls.push([path,{}]);return {headers:{get:key=>key==='content-disposition'?(path==='/data-template'?'attachment; filename="financial-planner-data-template.xlsx"':'attachment; filename="financial-planner-export-20260925-120000.xlsx"'):null},blob:async()=>({})};};
  await vm.runInContext('exportBackup()',context);
  assert(downloads.some(row=>row[0]==='download'&&row[1]==='financial-planner-export-20260925-120000.xlsx'));
  assert(!downloads.some(row=>row[0]==='revoke'),'object URL remains available immediately after triggering download');
  assert.equal(deferred.at(-1).delay,1000,'object URL cleanup is bounded');
  deferred.at(-1).callback();
  assert(downloads.some(row=>row[0]==='revoke'));
  assert(vm.runInContext('backupState.status',context).includes('financial-planner-export-20260925-120000.xlsx'));

  await vm.runInContext('downloadBackupTemplate()',context);
  assert(calls.some(([path])=>path==='/data-template'));
  assert(downloads.some(row=>row[0]==='download'&&row[1]==='financial-planner-data-template.xlsx'));
  assert(vm.runInContext('backupState.status',context).includes('Sample workbook downloaded'));

  console.log('PASS: Backup export, validation preview, cancel, confirm, stale token, and sensitive-file flow');
})().catch(error=>{console.error(error);process.exitCode=1;});
