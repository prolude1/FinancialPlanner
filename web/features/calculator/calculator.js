/* Time-value calculator and cash-flow plan UI. */
let calculatorResult=null;
let calculatorInput={calculation:'future_value',initial_value:'10000',cashflow:'500',cashflow_frequency:'monthly',cashflow_timing:'end',annual_rate:'6',duration_years:'10',duration_months:'0',compounding_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan',cashflow_end_years:'',cashflow_end_remainder_months:''};
let calculatorPlanMode='single';
let calculatorStreams=[
  {name:'Savings',initial_value:'10000',cashflow:'300',cashflow_frequency:'monthly',cashflow_timing:'end',annual_rate:'2.5',compounding_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan',cashflow_end_years:'',cashflow_end_remainder_months:''},
  {name:'Investments',initial_value:'10000',cashflow:'500',cashflow_frequency:'monthly',cashflow_timing:'end',annual_rate:'7',compounding_frequency:'monthly',cashflow_start_years:'0',cashflow_start_remainder_months:'0',cashflow_end_mode:'plan',cashflow_end_years:'',cashflow_end_remainder_months:''}
];
function calculatorPlanSwitch(){return `<div class="calculator-plan-switch"><button type="button" data-action="calculator-plan" data-value="single" class="${calculatorPlanMode==='single'?'active':''}">Single cash flow</button><button type="button" data-action="calculator-plan" data-value="multiple" class="${calculatorPlanMode==='multiple'?'active':''}">Multiple cash flows</button></div>`;}
function contributionWindowModel(values={}){
  const start=Number(values.cashflow_start_years||0)*12+Number(values.cashflow_start_remainder_months||0);
  const legacySet=values.cashflow_duration_years!==undefined&&(values.cashflow_duration_years!==''||values.cashflow_duration_remainder_months!=='');
  const mode=values.cashflow_end_mode||(legacySet?'custom':'plan');
  const legacyEnd=legacySet?start+Number(values.cashflow_duration_years||0)*12+Number(values.cashflow_duration_remainder_months||0):null;
  const end=mode==='custom'?(values.cashflow_end_years!==undefined&&values.cashflow_end_years!==''?Number(values.cashflow_end_years||0)*12+Number(values.cashflow_end_remainder_months||0):legacyEnd):null;
  return {start,mode,end};
}
function monthLabel(month){const years=Math.floor(Number(month||0)/12),months=Number(month||0)%12;return months?`Year ${years}, month ${months}`:`Year ${years}`;}
function contributionWindowSummary(values={}){const w=contributionWindowModel(values);if(w.mode==='plan')return w.start?`Contributions: from ${monthLabel(w.start)} to end of plan`:'Contributions: entire plan';return `Contributions: ${monthLabel(w.start)}–${monthLabel(w.end??w.start)}`;}
function contributionWindowFields(values={}){
  const w=contributionWindowModel(values),custom=w.mode==='custom',end=w.end??0;
  return `<details class="full cashflow-window"><summary><span>Contribution period</span><small data-window-summary>${esc(contributionWindowSummary(values))}</small></summary><div class="cashflow-window-body"><div class="form-grid"><div class="full window-field-label">Start after</div><label>Year<input name="cashflow_start_years" type="number" min="0" step="1" value="${esc(values.cashflow_start_years??'0')}" required></label><label>Month<input name="cashflow_start_remainder_months" type="number" min="0" max="11" step="1" value="${esc(values.cashflow_start_remainder_months??'0')}" required></label><fieldset class="full window-end-choice"><legend>End</legend><label><input type="radio" name="cashflow_end_mode" value="plan" ${custom?'':'checked'}> End of plan</label><label><input type="radio" name="cashflow_end_mode" value="custom" ${custom?'checked':''}> Custom end</label></fieldset><div class="full form-grid custom-window-end" ${custom?'':'hidden'}><label>End year<input name="cashflow_end_years" type="number" min="0" step="1" value="${custom?Math.floor(end/12):esc(values.cashflow_end_years??'')}" ${custom?'required':'disabled'}></label><label>End month<input name="cashflow_end_remainder_months" type="number" min="0" max="11" step="1" value="${custom?end%12:esc(values.cashflow_end_remainder_months??'')}" ${custom?'required':'disabled'}></label></div></div><small>Contributions start at the start boundary and stop at the custom end boundary. For example, Year 3 to Year 8 contributes for five years. The balance keeps earning returns afterward.</small><p class="cashflow-window-error error" role="alert"></p></div></details>`;
}
function streamFields(stream,index,pv){
  return `<section class="calculator-stream" data-stream-index="${index}"><div class="stream-head"><h3>Cash flow ${index+1}</h3>${calculatorStreams.length>1?`<button type="button" class="text-button" data-action="remove-stream" data-index="${index}">Remove</button>`:''}</div><div class="form-grid">
    <label class="full">Name<input name="name" value="${esc(stream.name)}" maxlength="80" required></label>
    <label class="full stream-initial-label">${pv?'Target value':'Initial amount'} (SGD)<input name="initial_value" type="number" min="0" step="0.01" value="${esc(stream.initial_value)}" required></label>
    <label>Recurring cash flow (SGD)<input name="cashflow" type="number" step="0.01" value="${esc(stream.cashflow)}" required><small>Use a negative number for withdrawals.</small></label>
    <label>Cash-flow frequency<select name="cashflow_frequency">${frequencyOptions(stream.cashflow_frequency)}</select></label>
    <label>Expected annual return (%)<input name="annual_rate" type="number" step="0.01" value="${esc(stream.annual_rate)}" required></label>
    <label>Compounding frequency<select name="compounding_frequency">${frequencyOptions(stream.compounding_frequency)}</select></label>
    <label class="full">Cash-flow timing<select name="cashflow_timing"><option value="end" ${stream.cashflow_timing==='end'?'selected':''}>End of each period</option><option value="beginning" ${stream.cashflow_timing==='beginning'?'selected':''}>Beginning of each period</option></select></label>
    ${contributionWindowFields(stream)}
  </div></section>`;
}
function multipleCalculatorPage(){
  const pv=calculatorInput.calculation==='present_value';
  return `${calculatorPlanSwitch()}<div class="calculator-layout"><div class="panel calculator-form-panel"><form id="calculator-form" data-mode="multiple">
    <div class="mode-switch" role="group" aria-label="Calculation type"><label><input type="radio" name="calculation" value="future_value" ${pv?'':'checked'}> How much will I have?</label><label><input type="radio" name="calculation" value="present_value" ${pv?'checked':''}> How much do I need today?</label></div>
    <div class="form-grid calculator-duration"><label>Years<input name="duration_years" type="number" min="0" step="1" value="${esc(calculatorInput.duration_years)}" required></label><label>Additional months<input name="duration_months" type="number" min="0" max="11" step="1" value="${esc(calculatorInput.duration_months)}" required></label></div>
    <div id="calculator-streams">${calculatorStreams.map((stream,index)=>streamFields(stream,index,pv)).join('')}</div>
    <button type="button" class="secondary small-button" data-action="add-stream">+ Add cash flow</button><p id="calculator-error" class="error" role="alert"></p><button class="calculator-submit" type="submit">${pv?'Calculate present value':'Calculate future value'}</button>
  </form></div><div id="calculator-results">${calculatorResult?calculatorResults(calculatorResult):calculatorEmpty()}</div></div>`;
}
function calculatorPage(){
  if(calculatorPlanMode==='multiple')return multipleCalculatorPage();
  const pv=calculatorInput.calculation==='present_value';
  return `${calculatorPlanSwitch()}<div class="calculator-layout"><div class="panel calculator-form-panel"><form id="calculator-form" data-mode="single">
    <div class="mode-switch" role="group" aria-label="Calculation type"><label><input type="radio" name="calculation" value="future_value" ${pv?'':'checked'}> How much will I have?</label><label><input type="radio" name="calculation" value="present_value" ${pv?'checked':''}> How much do I need today?</label></div>
    <div class="form-grid">
      <label class="full" id="calculator-initial-label">${pv?'Desired future amount':'Initial amount'} (SGD)<input name="initial_value" type="number" step="0.01" min="0" value="${esc(calculatorInput.initial_value)}" required></label>
      <label>Recurring cash flow (SGD)<input name="cashflow" type="number" step="0.01" value="${esc(calculatorInput.cashflow)}" required><small>Use a negative number for withdrawals.</small></label>
      <label>Cash-flow frequency<select name="cashflow_frequency">${frequencyOptions(calculatorInput.cashflow_frequency)}</select></label>
      <label>Expected annual return (%)<input name="annual_rate" type="number" step="0.01" value="${esc(calculatorInput.annual_rate)}" required></label>
      <label>Compounding frequency<select name="compounding_frequency">${frequencyOptions(calculatorInput.compounding_frequency)}</select></label>
      <label>Years<input name="duration_years" type="number" min="0" step="1" value="${esc(calculatorInput.duration_years)}" required></label>
      <label>Additional months<input name="duration_months" type="number" min="0" max="11" step="1" value="${esc(calculatorInput.duration_months)}" required></label>
      <label class="full">Cash-flow timing<select name="cashflow_timing"><option value="end" ${calculatorInput.cashflow_timing==='end'?'selected':''}>End of each period</option><option value="beginning" ${calculatorInput.cashflow_timing==='beginning'?'selected':''}>Beginning of each period</option></select></label>
      ${contributionWindowFields(calculatorInput)}
    </div><p id="calculator-error" class="error" role="alert"></p><button class="calculator-submit" type="submit">${pv?'Calculate present value':'Calculate future value'}</button>
  </form></div><div id="calculator-results">${calculatorResult?calculatorResults(calculatorResult):calculatorEmpty()}</div></div>`;
}
function calculatorEmpty(){return `<div class="panel calculator-empty"><span>∿</span><h2>See how money changes over time</h2><p>Enter an amount, recurring cash flow, return, and duration. Your result and year-by-year path will appear here.</p></div>`;}
function calculatorResults(result){
  const a=result.assumptions||{}, isPv=result.calculation==='present_value', multi=Array.isArray(result.streams)&&result.streams.length>0, schedule=multi?aggregateStreamSchedule(result.streams):result.schedule||[];
  const deposits=Number(result.total_contributions), growth=Number(result.total_interest_or_returns);
  const target=multi?result._input?.streams?.reduce((sum,stream)=>sum+Number(stream.initial_value),0):result._input?.initial_value;
  return `<div class="calculator-result-card"><small>${isPv?'REQUIRED TODAY':'PROJECTED FUTURE VALUE'}</small><strong><span>SGD</span> ${fmt(result.result)}</strong><p>${isPv?'To reach your target under these assumptions.':'At the end of the selected duration.'}</p></div>
    <div class="result-breakdown"><div><small>${isPv?'Target value':'Total contributions'}</small><strong>SGD ${fmt(isPv?target:deposits)}</strong></div><div><small>${growth<0?'Interest / returns lost':'Interest / returns earned'}</small><strong class="${growth<0?'negative':''}">SGD ${fmt(growth)}</strong></div><div><small>Effective annual rate</small><strong>${multi?'Varies by cash flow':fmt(result.effective_annual_rate,2)+'%'}</strong></div></div>
    <div class="panel calculator-chart-panel"><div class="panel-head"><h2>Combined balance over time</h2><small>SGD</small></div>${calculatorChart(schedule,multi?{cashflow_frequency:'monthly'}:a)}<p class="section-note">Move or drag the cursor across the graph to inspect the balance at any year.</p></div>
    ${multi?streamResultCards(result.streams):`<div class="panel assumptions"><h2>Assumptions</h2><p>${calculatorAssumptions(a)}</p></div>`}
    <div class="panel"><details><summary>View calculation schedule</summary>${multi?result.streams.map(stream=>`<h3>${esc(stream.name)}</h3>${calculatorSchedule(stream.schedule||[])}`).join(''):calculatorSchedule(schedule)}</details></div>`;
}
function aggregateStreamSchedule(streams){
  const totals=new Map();
  for(const stream of streams){const schedule=stream.schedule||[];if(schedule.length)totals.set(0,(totals.get(0)||0)+Number(schedule[0].opening_balance));for(const row of schedule){const month=Number(row.elapsed_month);totals.set(month,(totals.get(month)||0)+Number(row.closing_balance));}}
  return [...totals.entries()].sort((a,b)=>a[0]-b[0]).map(([month,balance])=>({period:month,elapsed_month:month,closing_balance:String(balance),opening_balance:String(balance)}));
}
function streamResultCards(streams){return `<div class="panel"><div class="panel-head"><h2>Cash-flow breakdown</h2><small>${streams.length} STREAMS</small></div><div class="stream-results">${streams.map(stream=>`<div><span><strong>${esc(stream.name)}</strong><small>${fmt(stream.assumptions?.annual_rate_percent,2)}% return · ${esc(stream.assumptions?.cashflow_frequency)} · ${cashflowWindowText(stream.assumptions)}</small></span><span><small>Result</small><strong>SGD ${fmt(stream.result)}</strong></span></div>`).join('')}</div></div>`;}
function cashflowWindowText(a={}){const start=Number(a.cashflow_start_month||0),duration=Number(a.cashflow_duration_months||0);return `${start?`starts ${fmtYear(start/12)}y in`:'starts immediately'} · ${duration?`runs ${fmtYear(duration/12)}y`:'through plan end'}`;}
function calculatorChart(schedule,assumptions={}){
  if(!schedule.length)return empty('No schedule available','Adjust the duration and calculate again.');
  const start={period:0,closing_balance:schedule[0].opening_balance}, all=Number(schedule[0].period)===0?schedule:[start,...schedule];
  const sampled=all.length>240?all.filter((_,i)=>i%Math.ceil(all.length/240)===0||i===all.length-1):all;
  const values=sampled.map(row=>Number(row.closing_balance)), min=Math.min(0,...values), max=Math.max(...values,1), w=800,h=260,p={l:68,r:18,t:18,b:32};
  const x=i=>p.l+(sampled.length===1?.5:i/(sampled.length-1))*(w-p.l-p.r), y=v=>p.t+(max-v)/(max-min||1)*(h-p.t-p.b);
  const points=values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
  const frequency={monthly:12,quarterly:4,semiannual:2,annual:1}[assumptions.cashflow_frequency]||1, years=schedule.at(-1).period/frequency;
  return `<div class="chart-plot calculator-chart" data-periods-per-year="${frequency}"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Calculated balance by year"><line class="chart-axis" x1="${p.l}" x2="${p.l}" y1="${p.t}" y2="${h-p.b}"/><line class="chart-axis" x1="${p.l}" x2="${w-p.r}" y1="${h-p.b}" y2="${h-p.b}"/><polyline class="chart-line" points="${points}"/><text x="4" y="${p.t+5}">SGD ${fmt(max)}</text><text x="4" y="${h-p.b}">SGD ${fmt(min)}</text><text x="${p.l}" y="${h-7}">Year 0</text><text text-anchor="end" x="${w-p.r}" y="${h-7}">Year ${fmtYear(years)}</text><line class="chart-cursor calculator-cursor" hidden y1="${p.t}" y2="${h-p.b}"/><circle class="chart-point calculator-hover-point" hidden r="4"/><rect class="chart-hit calculator-hit" data-action="calculator-chart-hover" x="${p.l}" y="${p.t}" width="${w-p.l-p.r}" height="${h-p.t-p.b}"/></svg><div class="chart-tooltip calculator-tooltip" hidden></div></div>`;
}
function fmtYear(value){return Number.isInteger(value)?String(value):Number(value).toFixed(2).replace(/0+$/,'').replace(/\.$/,'');}
function calculatorChartHover(event){
  const root=event.target.closest('.calculator-chart'),svg=event.target.closest('svg'),source=calculatorResult?.streams?.length?aggregateStreamSchedule(calculatorResult.streams):calculatorResult?.schedule||[];if(!root||!svg||!source.length)return;
  const rows=calculatorResult?.streams?.length?source:[{period:0,closing_balance:source[0].opening_balance},...source],box=svg.getBoundingClientRect(),plotLeft=70/800*box.width,plotRight=18/800*box.width;
  const ratio=Math.max(0,Math.min(1,(event.clientX-box.left-plotLeft)/(box.width-plotLeft-plotRight))),index=Math.round(ratio*(rows.length-1)),row=rows[index],value=Number(row.closing_balance),frequency=Number(root.dataset.periodsPerYear)||1;
  const values=rows.map(r=>Number(r.closing_balance)),min=Math.min(0,...values),max=Math.max(...values,1),x=70+index/(rows.length-1)*(800-70-18),y=18+(max-value)/(max-min||1)*(260-18-32);
  const cursor=svg.querySelector('.calculator-cursor'),point=svg.querySelector('.calculator-hover-point'),tip=root.querySelector('.calculator-tooltip');
  cursor.removeAttribute('hidden');point.removeAttribute('hidden');cursor.setAttribute('x1',x);cursor.setAttribute('x2',x);point.setAttribute('cx',x);point.setAttribute('cy',y);tip.hidden=false;tip.style.left=`${(x/800)*100}%`;tip.innerHTML=`<strong>Year ${fmtYear(row.period/frequency)}</strong><span>SGD ${fmt(value)}</span>`;
}
function calculatorAssumptions(a){
  const flow=Number(calculatorResult?._input?.cashflow||0), flowText=flow<0?`SGD ${fmt(Math.abs(flow))} withdrawn`:`SGD ${fmt(flow)} contributed`;
  return `${flowText} ${esc(a.cashflow_frequency||'periodically')} at the ${esc(a.cashflow_timing||'end')} of each period; ${cashflowWindowText(a)}. The balance earns a ${fmt(a.annual_rate_percent,2)}% nominal annual rate compounded ${esc(a.compounding_frequency||'periodically')} for the full plan. Values are rounded to cents for display.`;
}
function calculatorSchedule(schedule){
  if(!schedule.length)return '';
  return `<div class="schedule-scroll table-wrap"><table><thead><tr><th>Period</th><th class="num">Opening</th><th class="num">Cash flow</th><th class="num">Interest / return</th><th class="num">Closing</th></tr></thead><tbody>${schedule.map(row=>`<tr><td>${esc(row.period)}</td><td class="num">${fmt(row.opening_balance)}</td><td class="num">${fmt(row.cashflow)}</td><td class="num">${fmt(row.interest)}</td><td class="num">${fmt(row.closing_balance)}</td></tr>`).join('')}</tbody></table></div>`;
}
async function calculateTimeValue(form){
  const button=form.querySelector('button[type="submit"]'), values=Object.fromEntries(new FormData(form));$('#calculator-error').textContent='';
  if(!validateContributionWindows(form)){form.querySelector('.cashflow-window-error:not(:empty)')?.closest('details')?.setAttribute('open','');return;}
  button.disabled=true;
  calculatorInput={...calculatorInput,calculation:values.calculation,duration_years:values.duration_years,duration_months:values.duration_months};
  let payload;
  if(form.dataset.mode==='multiple'){calculatorStreams=readStreamForms(form);payload={calculation:values.calculation,duration_years:Number(values.duration_years),duration_months:Number(values.duration_months),include_schedule:true,streams:calculatorStreams.map(cashflowPayload)};}
  else{calculatorInput={...values};payload={...cashflowPayload(values),calculation:values.calculation,duration_years:Number(values.duration_years),duration_months:Number(values.duration_months),include_schedule:true};}
  try{const result=await api('/calculators/time-value',{method:'POST',body:JSON.stringify(payload)});calculatorResult={...result,_input:payload};render();}
  catch(error){$('#calculator-error').textContent=error.message;button.disabled=false;}
}
function fieldValues(root){return Object.fromEntries([...root.querySelectorAll('input,select')].filter(field=>field.type!=='radio'||field.checked).map(field=>[field.name,field.value]));}
function readStreamForms(form){return [...form.querySelectorAll('.calculator-stream')].map(fieldValues);}
function cashflowPayload(values){
  const {cashflow_start_years,cashflow_start_remainder_months,cashflow_end_mode,cashflow_end_years,cashflow_end_remainder_months,cashflow_duration_years,cashflow_duration_remainder_months,...payload}=values;
  const window=contributionWindowModel(values);payload.cashflow_start_month=window.start;
  if(window.mode==='custom')payload.cashflow_duration_months=window.end-window.start;
  return payload;
}
function validateContributionWindows(form){
  const plan=Number(form.elements.duration_years.value||0)*12+Number(form.elements.duration_months.value||0);
  const sections=form.dataset.mode==='multiple'?[...form.querySelectorAll('.calculator-stream')]:[form];let valid=true;
  for(const section of sections){
    const fields=fieldValues(section),window=contributionWindowModel(fields),period={monthly:1,quarterly:3,semiannual:6,annual:12}[fields.cashflow_frequency]||1,error=section.querySelector('.cashflow-window-error');let message='';
    if(window.start>=plan)message='Start must be before the end of the overall plan.';
    else if(window.start%period)message=`Start must align with the ${fields.cashflow_frequency} cash-flow frequency.`;
    else if(window.mode==='custom'&&(window.end==null||window.end<=window.start))message='Custom end must be after the start.';
    else if(window.mode==='custom'&&window.end>plan)message='Custom end must be within the overall plan duration.';
    else if(window.mode==='custom'&&window.end%period)message=`Custom end must align with the ${fields.cashflow_frequency} cash-flow frequency.`;
    error.textContent=message;if(message)valid=false;
  }
  return valid;
}
