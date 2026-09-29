/* Data backup and restore workflow. */
const backupState={exporting:false,importing:false,preview:null,error:'',status:'',fileName:'',file:null,confirming:false};
function backupPage(){
  const p=backupState.preview;
  return `<section class="panel data-management"><div class="panel-head"><h2 id="backup-page-title" tabindex="-1">Back up all application data</h2><small>OWNER ONLY</small></div><p>Download a readable Excel backup or restore the application from a backup workbook. Restoring replaces the current records exactly. Store workbook files securely.</p><div class="data-actions"><button type="button" data-action="backup-export" ${backupState.exporting?'disabled':''}>${backupState.exporting?'Preparing workbook…':'Export Excel backup'}</button><button type="button" class="secondary" data-action="backup-sample" ${backupState.exporting||backupState.importing||backupState.confirming?'disabled':''}>Download sample workbook</button><label class="file-picker">Choose .xlsx backup<input id="backup-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Choose an Excel backup workbook to validate" ${backupState.importing||backupState.confirming?'disabled':''}></label></div><p class="section-note">The sample workbook contains synthetic example data and can be restored. A backup workbook preserves your application records for exact restoration.</p><p class="backup-file-name" aria-live="polite">${esc(backupState.fileName||'No workbook selected.')}</p><p id="backup-status" class="backup-status" role="status" aria-live="polite" tabindex="-1">${esc(backupState.status)}</p>${backupState.error?`<p id="backup-error" class="error" role="alert" tabindex="-1">${esc(backupState.error)}</p>`:''}</section>
  ${p?backupPreview(p):''}`;
}
function backupPreview(p){
  const tables=['planner_state','instrument_catalog','portfolio_history','stock_cache'];
  const counts=(current,uploaded)=>tables.map(table=>`<tr><th scope="row">${esc(table)}</th><td class="num">${esc(current?.[table]??'-')}</td><td class="num">${esc(uploaded?.[table]??'-')}</td></tr>`).join('');
  return `<section class="panel backup-review" aria-labelledby="backup-review-title"><div class="panel-head"><h2 id="backup-review-title" tabindex="-1">Review workbook before exact restore</h2><small>VALIDATED · VERSION ${esc(p.format_version??p.manifest?.format_version??'-')}</small></div><p><strong>Workbook created:</strong> ${esc(p.exported_at??p.manifest?.exported_at??'-')}</p><p><strong>Application / schema:</strong> ${esc(p.app_version??p.manifest?.app_version??'-')} / ${esc(p.schema_version??p.manifest?.schema_version??'-')}</p><p class="section-note">This confirmation expires in ${Math.ceil(Number(p.confirmation_expires_in_seconds||600)/60)} minutes.</p><div class="table-wrap backup-count-grid"><table><thead><tr><th scope="col">Table</th><th class="num" scope="col">Current</th><th class="num" scope="col">In workbook</th></tr></thead><tbody>${counts(p.comparison?.current,p.comparison?.uploaded)}</tbody></table></div><h3>Data categories affected</h3><ul>${affectedDataCategories.map(category=>`<li>${esc(category)}</li>`).join('')}</ul>${(p.warnings||[]).length?`<div class="notice"><strong>Warnings</strong><ul>${p.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>`:''}<div class="notice backup-replace-warning"><strong>Restoring this workbook replaces all current application data, even if it is the sample workbook with synthetic example data.</strong><p>Download an Excel backup first if you may need to restore the current data later.</p></div><div class="data-actions"><button type="button" data-action="backup-confirm" ${backupState.confirming?'disabled':''}>${backupState.confirming?'Restoring workbook…':'Restore workbook exactly'}</button><button type="button" class="secondary" data-action="backup-cancel" ${backupState.confirming?'disabled':''}>Cancel</button></div></section>`;
}
const affectedDataCategories=['Accounts and holdings','Transactions and corrections','Credit cards and loans','Instrument catalog','Brokerage portfolio history','Cached stock data'];
function downloadBackup(response,fallbackName='financial-planner-backup.xlsx'){
  const blobPromise=response.blob(),disposition=response.headers.get('content-disposition')||'',match=disposition.match(/filename="?([^";]+)"?/i),name=match?.[1]||fallbackName;
  return blobPromise.then(blob=>{const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.hidden=true;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);return name;});
}
async function exportBackup(){
  backupState.exporting=true;backupState.error='';backupState.status='Preparing a complete export…';renderBackupStatus();
  try{const response=await api('/data-export',{method:'GET'});const name=await downloadBackup(response);backupState.status=`Export downloaded: ${name}`;backupState.lastExport=new Date().toISOString();}
  catch(error){backupState.error=error.message;backupState.status='Export failed.';}
  finally{backupState.exporting=false;renderBackupStatus();}
}
async function downloadBackupTemplate(){
  backupState.exporting=true;backupState.error='';backupState.status='Preparing the sample workbook…';renderBackupStatus();
  try{const response=await api('/data-template',{method:'GET'});const name=await downloadBackup(response,'financial-planner-data-template.xlsx');backupState.status=`Sample workbook downloaded: ${name}`;}
  catch(error){backupState.error=error.message;backupState.status='Sample workbook download failed.';}
  finally{backupState.exporting=false;renderBackupStatus();}
}
async function validateBackup(file){
  backupState.preview=null;backupState.file=file;backupState.fileName=file?.name||'';backupState.error='';backupState.status='';if(!file){render();return;}
  if(!/\.xlsx$/i.test(file.name||'')){backupState.error='Choose an .xlsx Excel workbook.';backupState.status='Validation failed.';render();return;}
  if(file.size>64*1024*1024){backupState.error='This workbook exceeds the 64 MiB upload limit.';backupState.status='Validation failed.';render();return;}
  backupState.importing=true;backupState.status='Validating workbook without changing current data…';renderBackupStatus();
  const form=new FormData();form.append('file',file,file.name);
  try{const preview=await api('/data-import/validate',{method:'POST',body:form});backupState.preview={...preview,categories:affectedDataCategories};backupState.status='Validation passed. Review the workbook contents before confirming exact restore.';}
  catch(error){backupState.error=error.message;backupState.status='Validation failed.';}
  finally{backupState.importing=false;backupState.file=null;render();($('#backup-review-title')||$('#backup-error')||$('#backup-status'))?.focus?.();}
}
function renderBackupStatus(){const page=$('#content');if(page&&route().page==='data-management'){const action=document.activeElement?.dataset?.action;page.innerHTML=backupPage();if(action)page.querySelector(`[data-action="${action}"]`)?.focus?.();}}
function cancelBackupImport(){backupState.preview=null;backupState.file=null;backupState.fileName='';backupState.error='';backupState.status='Import cancelled. Current data was not changed.';const picker=$('#backup-file');if(picker)picker.value='';render();$('#backup-file')?.focus?.();}
async function confirmBackupImport(){
  if(!backupState.preview||backupState.confirming)return;const token=backupState.preview.confirmation_token;backupState.confirming=true;backupState.error='';backupState.status='Replacing application data…';render();
  try{const result=await api('/data-import/commit',{method:'POST',body:JSON.stringify({archive_sha256:backupState.preview.archive_sha256,current_revision:backupState.preview.current_revision,current_etag:backupState.preview.current_etag,confirmation_token:token})});backupState.preview=null;backupState.file=null;backupState.fileName='';backupState.confirming=false;backupState.status='Import completed. Reloading current application data…';await load();location.hash='#overview';if(result.rollback_snapshot?.created_at)toast(`Import completed. A rollback snapshot was saved at ${result.rollback_snapshot.created_at}.`);}
  catch(error){backupState.confirming=false;if(error.status===409){backupState.preview=null;backupState.error='This confirmation expired or current data changed. Select the workbook again and validate it before confirming.';backupState.file=null;backupState.fileName='';backupState.status='Import was not completed.';}else if(error.status==null||error.status>=500){backupState.preview=null;backupState.error=error.message;backupState.status='Import status is unknown. The server may have completed the replacement; reload and verify the current data before trying again.';}else{backupState.error=error.message;backupState.status='Import was not completed.';}render();$('#backup-file')?.focus?.();}
}
