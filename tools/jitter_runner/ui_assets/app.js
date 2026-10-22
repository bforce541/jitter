'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = x => esc(JSON.stringify(x, null, 2));
const human = s => String(s || 'not recorded').replaceAll('_', ' ');
const num = (v, digits=2) => Number.isFinite(v) ? v.toLocaleString(undefined, {maximumFractionDigits:digits}) : '—';
const pct = v => Number.isFinite(v) ? `${v>=0?'+':''}${num(v*100,1)}%` : '—';
const ms = v => Number.isFinite(v) ? `${num(v/1e6,3)} ms` : '—';
const seconds = v => Number.isFinite(v) ? `${num(v,2)} s` : '—';
const bytes = v => Number.isFinite(v) ? `${num(v/1048576,2)} MiB` : '—';
const code = x => `<pre class="code">${pretty(x)}</pre>`;
let library, current, tab='overview', selectedTrial=null, selectedRun=null, measurement=null, traceTarget=null;
let generation=0, measureGeneration=0, verifyGeneration=0, measurementLoading=false;
const tones = status => /accepted|recommendation/.test(status) ? 'accepted' : /invalid|failure|failed|violation|rejected/.test(status) ? 'invalid' : /inconclusive|budget|interrupted/.test(status) ? 'inconclusive' : '';
const badge = status => `<span class="badge ${tones(status)}">${esc(human(status))}</span>`;
const config = c => c ? `${esc(c.implementation || 'default')} <span class="quiet">/</span> ${num(c.workers,0)} workers <span class="quiet">/</span> q ${num(c.queue_capacity,0)}` : 'No configuration recorded';
async function api(path) {
  const response = await fetch(path, {cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
  return data;
}
function toast(text) { $('#toast').textContent=text; $('#toast').classList.add('visible'); setTimeout(()=>$('#toast').classList.remove('visible'),5000); }
function showError(error) { $('#main').innerHTML=`<div class="notice error-box">${esc(error.message)}</div>`; }
function label(entry) { const parts=entry.path.split('/'); return parts.length>3 ? parts.slice(-3).join(' / ') : entry.path==='.' ? entry.application : entry.path; }
async function refresh() {
  try {
    library=await api('/api/library'); $('#root').textContent=library.root; $('#root').title=library.root;
    drawLibrary();
    if (current && library.entries.some(x=>x.id===current.id)) await select(current.id);
    if (!current && library.entries.length) {
      const saved=new URLSearchParams(location.hash.slice(1)).get('id');
      const first=library.entries.find(x=>x.id===saved) || library.entries.find(x=>x.kind==='investigation') || library.entries[0];
      await select(first.id);
    }
    if (library.warnings.length) toast(library.warnings.join(' · '));
  } catch(error) { showError(error); }
}
function drawLibrary() {
  if(!library) return;
  const query=$('#search').value.toLowerCase(), filter=$('#filter').value;
  const entries=library.entries.filter(x=>(x.path+' '+x.application+' '+x.strategy+' '+x.status).toLowerCase().includes(query) && (!filter || (filter==='invalid' ? /invalid|failed|failure/.test(x.status) : x.status.includes(filter) || x.kind===filter)));
  $('#library').innerHTML=entries.map(x=>`<button class="library-item ${current?.id===x.id?'active':''}" data-result="${x.id}" title="${esc(x.path)}" aria-current="${current?.id===x.id?'true':'false'}"><div class="name">${esc(label(x))}</div><div class="sub"><span class="status-dot ${tones(x.status)}"></span>${esc(x.strategy || x.kind)}<span class="quiet">· ${esc(human(x.status))}</span></div></button>`).join('') || '<p class="list-empty">No matching local results.<br>Run an investigation with the CLI, then refresh.</p>';
}
async function select(id) {
  const version=++generation; ++measureGeneration; ++verifyGeneration;
  $('#main').innerHTML='<div class="spinner">Reading local evidence…</div>';
  $('#verification').textContent='Stored evidence · unverified'; $('#verification').className=''; $('#verify').disabled=true;
  try {
    const result=await api(`/api/detail?id=${encodeURIComponent(id)}`); if(version!==generation) return;
    current=result; selectedTrial=null; selectedRun=null; measurement=null; measurementLoading=false; traceTarget=null; tab=result.kind==='overhead'?'overhead':'overview';
    history.replaceState(null,'','#id='+id); $('#breadcrumb').textContent=current.kind==='investigation'?'Investigation':human(current.kind); $('#verify').disabled=false;
    drawLibrary(); render();
  } catch(error) { if(version===generation) showError(error); }
}
function comparison() { return selectedTrial===null ? current.report.confirmation?.comparison || current.report.trials?.at(-1)?.comparison || {} : current.report.trials[selectedTrial]?.comparison || {}; }
function activeConfig() { return selectedTrial===null ? current.report.recommendation || current.report.confirmation?.configuration || current.report.best_exploratory_candidate : current.report.trials[selectedTrial]?.configuration; }
const section=(title,body,meta='')=>`<section class="section"><div class="section-heading"><h2>${title}</h2><span class="meta">${meta}</span></div>${body}</section>`;
function render() {
  if(!current) return;
  const r=current.report,s=current.spec;
  const parts=current.path.split('/'); const title=parts.includes('studies')?parts.slice(-3).join(' / '):parts.slice(-1)[0];
  const status=r.decision || r.status || current.state.status || (current.kind==='overhead'?'overhead comparison':'incomplete');
  const tabs=current.kind==='investigation' ? [['overview','Overview'],['search','Search & decisions'],['evidence','Hypotheses'],['experiments','Experiments'],['trace','Dependencies'],['provenance','Provenance']] : current.kind==='overhead' ? [['overhead','Instrumentation overhead'],['experiments','Experiments'],['provenance','Provenance']] : [['experiments','Measurement'],['trace','Dependencies'],['provenance','Provenance']];
  $('#main').innerHTML=`<div class="eyebrow">${esc((s.experiment || s).application?.adapter || 'shared-index-v1')} ${s.search?' / '+esc(s.search.strategy)+' search':''}</div><div class="title-row"><h1>${esc(title)}</h1>${badge(status)}</div><div class="subtitle">${esc(current.path)}</div>${current.kind==='investigation'?decision():''}<nav class="tabs" aria-label="Investigation views">${tabs.map(([id,text])=>`<button data-tab="${id}" class="${tab===id?'selected':''}" aria-pressed="${tab===id}">${text}</button>`).join('')}</nav><div id="view"></div>`;
  const functions={overview,search:searchView,evidence:evidenceView,experiments:experimentsView,trace:traceView,provenance:provenanceView,overhead:overheadView};
  $('#view').innerHTML=functions[tab]();
  if ((tab==='experiments'||tab==='trace') && !measurement && !measurementLoading && current.records.length) loadMeasurement(selectedRun || current.state.diagnostic || current.records[0].path);
}
function decision() {
  const r=current.report,c=r.confirmation?.comparison;
  return `<div class="decision"><span class="decision-icon ${r.recommendation?'':'amber'}">${r.recommendation?'↗':'○'}</span><div><div class="line">${r.recommendation?'Recommend <span class="mono">'+config(r.recommendation)+'</span>':esc(human(r.decision || current.state.status))}</div><div class="detail">${esc(human(r.stop_reason || current.state.stop_reason))}${r.search_stop_reason?' · search: '+esc(human(r.search_stop_reason)):''}${c?' · '+num(c.pairs_valid,0)+' fresh pairs':''} · decisions shown as stored</div></div></div>`;
}
function metric(label,value,note) {return `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></div>`;}
function overview() {
  const r=current.report,c=comparison(),m=c.metrics||{},u=c.uncertainty||{};
  const p99=m.p99?.tail_support?.sufficient!==false;
  const metricStrip=`<div class="metric-strip">${metric('p99 · candidate',p99?ms(m.p99?.candidate?.median):'Insufficient',`Baseline ${p99?ms(m.p99?.baseline?.median):'—'} · run medians`)}${metric('Paired improvement',pct(c.effect?.median_fraction),`${c.confirmation?'Fresh confirmation':'Exploratory · descriptive'} · ${num(c.pairs_valid,0)} pairs`)}${metric('Throughput',num(m.throughput_per_second?.candidate?.median,0)+' /s',`Baseline ${num(m.throughput_per_second?.baseline?.median,0)} /s`)}${metric('CPU / peak memory',num(m.cpu_cores?.candidate?.median)+' <small class="small">cores</small>',`${bytes(m.peak_rss_bytes?.candidate?.median)} RSS · candidate`)}</div>`;
  return metricStrip+`<div class="grid"><div>${section('Paired latency',pairChart(c),'p99 · milliseconds')}${section('Search progression',progressChart(),'exploration → fresh confirmation')}${section('Metric comparison',metricsTable(c),'median of run values')}</div><div>${section('Evidence strength',`<dl class="kv"><dt>Phase</dt><dd>${c.confirmation?'Fresh confirmation':'Exploratory'}</dd><dt>Observed median gain</dt><dd class="green">${pct(c.effect?.median_fraction)}</dd><dt>One-sided lower bound</dt><dd>${pct(u.lower_bound)}</dd><dt>Requested confidence</dt><dd>${num(u.confidence*100,1)}%</dd><dt>Achieved coverage</dt><dd>${num(u.achieved_one_sided_confidence*100,2)}%</dd><dt>Practical gain required</dt><dd>${pct(c.effect?.practical_minimum_fraction)}</dd></dl><p class="note">${esc(u.scope || 'Exploration cannot authorize a recommendation. No confirmatory bound is available.')}</p>${(c.reasons||[]).map(x=>`<div class="notice">${esc(human(x))}</div>`).join('')}`)}${section('Experiment budget',budgets())}${section('Configuration',`<span class="stage">Baseline</span><div class="config">${config(r.baseline)}</div><h3>${r.recommendation?'Accepted configuration':'Best exploratory configuration'}</h3><div class="config">${config(r.recommendation || r.best_exploratory_candidate)}</div><p class="note">${num(r.search_coverage?.attempted_candidates ?? r.trials?.length,0)} candidates tested. ${num(r.search_coverage?.untried_candidates,0)} untried. No global optimum is established.</p>`)}${reproduceSection()}</div></div>`;
}
function metricsTable(c) {
  const labels={p50:'p50 latency',p95:'p95 latency',p99:'p99 latency',p999:'p99.9 latency',throughput_per_second:'Throughput /s',cpu_cores:'CPU cores',peak_rss_bytes:'Peak RSS'};
  return '<div class="comparison-row header"><span>METRIC</span><span>BASELINE</span><span>CANDIDATE</span></div>'+Object.entries(labels).map(([key,label])=>{
    const m=c.metrics?.[key],valid=m && m.tail_support?.sufficient!==false;
    const f=key.startsWith('p')&&key!=='peak_rss_bytes'?ms:key==='peak_rss_bytes'?bytes:num;
    return `<div class="comparison-row ${valid?'':'unsupported'}"><span class="label">${label}</span><span>${valid?f(m.baseline?.median):'—'}</span><span>${valid?f(m.candidate?.median):'Insufficient data'}</span></div>`;
  }).join('')+'<p class="note">Tail values are withheld when the per-run observation guard fails. CPU, memory and non-objective quantiles are descriptive. No request pooling.</p>';
}
function pairChart(c) {
  const pairs=(c.pairs||[]).filter(p=>p.tail_sufficient);
  if(!pairs.length)return '<p class="note">No paired runs with sufficient tail support.</p>';
  const w=640,h=215,left=48,top=16,bottom=36,max=Math.max(...pairs.flatMap(p=>[p.baseline_latency_ns,p.candidate_latency_ns]))/1e6*1.12;
  const x=i=>left+(w-left-25)*(i+.5)/pairs.length,y=v=>h-bottom-(v/1e6)/max*(h-bottom-top);
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Paired baseline and candidate p99 latency in milliseconds">`;
  for(let i=0;i<4;i++){const v=max*i/3,yy=y(v*1e6);svg+=`<line class="gridline" x1="${left}" x2="${w-10}" y1="${yy}" y2="${yy}"/><text x="0" y="${yy+4}">${num(v,2)}</text>`;}
  pairs.forEach((p,i)=>{const xx=x(i),a=y(p.baseline_latency_ns),b=y(p.candidate_latency_ns);svg+=`<line class="connector" x1="${xx}" x2="${xx}" y1="${a}" y2="${b}"/><circle class="baseline dot" cx="${xx}" cy="${a}" r="4"><title>Pair ${esc(p.pair)} baseline: ${ms(p.baseline_latency_ns)}</title></circle><circle class="candidate dot" cx="${xx}" cy="${b}" r="4"><title>Pair ${esc(p.pair)} candidate: ${ms(p.candidate_latency_ns)}</title></circle><text text-anchor="middle" x="${xx}" y="${h-10}">${esc(p.pair)}</text>`;});
  return svg+'</svg><div class="legend"><span><i class="baseline"></i>Baseline</span><span><i></i>Candidate</span><span>Separate baseline / candidate process runs</span></div>';
}
function progressChart() {
  const trials=current.report.trials||[],fresh=current.report.confirmation;
  const points=trials.map((t,i)=>({v:t.comparison?.effect?.median_fraction,label:'E'+(i+1),fresh:false}));
  if(fresh)points.push({v:fresh.comparison?.effect?.median_fraction,label:'Fresh',fresh:true});
  const finite=points.filter(p=>Number.isFinite(p.v));
  if(!finite.length)return '<p class="note">No completed comparisons recorded.</p>';
  const w=640,h=156,left=48,bottom=30,low=Math.min(0,...finite.map(p=>p.v)),high=Math.max(.1,...finite.map(p=>p.v));
  const y=v=>16+(high-v)/(high-low)*105,x=i=>left+(w-left-30)*(i+.5)/points.length;
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Controller progression, median paired fractional improvement"><line class="gridline" x1="${left}" x2="${w-10}" y1="${y(0)}" y2="${y(0)}"/><text x="0" y="${y(0)+4}">0%</text><text x="0" y="20">${num(high*100,0)}%</text>`;
  const margin=current.spec.comparison?.min_effect;
  if(Number.isFinite(margin)&&margin<=high)svg+=`<line class="threshold" x1="${left}" x2="${w-10}" y1="${y(margin)}" y2="${y(margin)}"/>`;
  points.forEach((p,i)=>{if(Number.isFinite(p.v))svg+=`<line class="connector" x1="${x(i)}" x2="${x(i)}" y1="${y(0)}" y2="${y(p.v)}"/><circle class="${p.fresh?'candidate':'baseline'} dot" cx="${x(i)}" cy="${y(p.v)}" r="5"><title>${esc(p.label)}: ${pct(p.v)}</title></circle>`;svg+=`<text text-anchor="middle" x="${x(i)}" y="${h-8}">${esc(p.label)}</text>`;});
  return svg+'</svg><p class="note">E = exploratory candidate; fresh = frozen finalist holdout. Dashed line is the practical gain threshold. This is search history, not a convergence guarantee.</p>';
}
function budgets() {
  const cost=current.report.cost || current.state.cost || {},budget=current.spec.budget||{};
  const keys=[['runs','max_runs','Processes'],['builds','max_builds','Builds'],['failures','max_failures','Failures'],['seconds','wall_seconds','Wall time']];
  return keys.map(([key,limit,label])=>{const used=cost[key],total=budget[limit]; const fraction=Number.isFinite(used)&&total>0?Math.min(1,used/total):0; return `<div class="budget"><div class="budget-top"><span>${label}</span><span>${key==='seconds'?seconds(used):num(used,0)} / ${key==='seconds'?seconds(total):num(total,0)}</span></div><progress class="meter" value="${fraction}" max="1" aria-label="${label} consumed"></progress><div class="budget-foot">${key==='seconds'?seconds(Math.max(0,total-used)):num(Math.max(0,total-used),0)} remaining</div></div>`;}).join('')+`<dl class="kv"><dt>Timed application CPU</dt><dd>${seconds(cost.timed_application_cpu_seconds)}</dd><dt>Application setup</dt><dd>${seconds(cost.application_setup_seconds)}</dd><dt>Build time</dt><dd>${seconds(cost.build_seconds)}</dd><dt>Per-use API cost</dt><dd>0 · local execution</dd></dl><p class="note">Wall time includes orchestration. CPU covers timed application regions; electricity and hardware cost are not measured.</p>`;
}
function reproduceSection() {return section('Reproduce this investigation',`<div class="command"><pre class="code">${esc(current.reproduce)}</pre><button data-copy="reproduce">Copy</button></div><p class="note">Replay uses saved inputs and executables. Host compatibility is checked. Timing, search choices and conclusions may differ. Output must be a new directory.</p>`);}
function searchView() {
  const trials=current.report.trials||current.state.trials||[],confirmation=current.report.confirmation || current.state.confirmation;
  const rows=trials.map((t,i)=>trialRow(t,i,false)).join('')+(confirmation?trialRow(confirmation,'fresh',true):'');
  const active=selectedTrial===null?confirmation:trials[selectedTrial];
  return section('Configurations explored',`<div class="table-scroll"><table><thead><tr><th>Experiment</th><th>Configuration</th><th>Selection</th><th>Paired gain</th><th>Decision</th></tr></thead><tbody>${rows || '<tr><td colspan="5">No candidate completed yet.</td></tr>'}</tbody></table></div>`,'stored controller decisions')+`<div class="grid"><div>${section('Selected experiment',active?`<div class="config">${config(active.configuration)}</div><div class="reason"><strong>WHY THIS EXPERIMENT</strong>${esc(active.selection?.basis || 'One finalist frozen from promising exploratory evidence; fixed fresh confirmation block.')}</div>${metricsTable(active.comparison||{})}${pairChart(active.comparison||{})}`:'<p class="note">Select a completed experiment.</p>')}</div><div>${section('Decision & uncertainty',active?`${badge(active.comparison?.status || 'incomplete')}<p>${esc((active.comparison?.reasons||[]).map(human).join(' · ') || (active.comparison?.accepted?'Fresh confirmation passed the practical effect bound and every observed constraint.':'Exploration is descriptive and cannot authorize a recommendation.'))}</p>${code({effect:active.comparison?.effect,uncertainty:active.comparison?.uncertainty,tail_support:active.comparison?.tail_support})}<details><summary>Controller selection record</summary>${code(active.selection || {phase:'fresh confirmation'})}</details>`:'<p class="note">No decision recorded.</p>')}${section('Search specification',code(current.spec.search))}</div></div>`;
}
function trialRow(t,i,fresh) {
  const c=t.comparison||{},status=c.status||t.status||'incomplete';
  return `<tr class="${(fresh&&selectedTrial===null)||i===selectedTrial?'selected':''}"><td><button class="inline-btn" data-trial="${i}">${fresh?'Fresh confirmation':'E'+(i+1)}</button></td><td class="config">${config(t.configuration)}</td><td>${esc(fresh?'frozen finalist':t.selection?.mode || t.selection?.strategy || '—')}</td><td class="mono">${pct(c.effect?.median_fraction)}</td><td>${badge(status)}</td></tr>`;
}
function evidenceView() {
  const hypotheses=current.report.hypotheses || current.state.hypotheses || [],trials=current.report.trials||[];
  return `<p class="note">Dependency analysis proposes mechanisms. Candidate comparisons test changes; a successful change does not prove its proposed mechanism caused the original delay.</p>`+hypotheses.map(h=>{
    const tests=trials.filter(t=>Object.hasOwn(t.selection?.hypothesis_support||{},h.id));
    return `<section class="hypothesis"><h3>${esc(human(h.id))} <span class="badge">hypothesis</span></h3><p>${esc(h.mechanism)}</p><div class="support"><span>${ms(h.support_ns)} observed support</span><span>${num(h.tail_component_fraction*100,1)}% tail component</span></div><div class="relation"><div><small>01 · HYPOTHESIS</small>${esc(human(h.id))}</div><span>→</span><div><small>02 · SELECTED TESTS</small>${tests.length?tests.map(t=>config(t.configuration)).join('<br>'):'No direct selection attribution recorded'}</div><span>→</span><div><small>03 · OBSERVED EVIDENCE</small>${tests.length?tests.map(t=>esc(human(t.comparison?.status))+' · '+pct(t.comparison?.effect?.median_fraction)).join('<br>'):'Unresolved in this investigation'}</div></div><p class="note">${esc(h.caveat)}</p><details><summary>Trace support and proposed test</summary>${code({evidence:h.evidence,suggested_experiment:h.suggested_experiment})}</details></section>`;
  }).join('') || '<p class="note">No diagnostic hypotheses recorded. Tracing may be disabled, incomplete or unsupported.</p>';
}
function timeline() {
  const records=current.records;
  return `<div class="timeline" aria-label="Experiment sequence">${records.map((row,i)=>`<button data-run="${esc(row.path)}" class="${row.diagnostic?'diagnostic':/confirmation/.test(row.stage)?'confirmation':''} ${row.status==='failed'?'failed':''} ${selectedRun===row.path?'active':''}" title="${i+1} · ${esc(row.stage)} · ${seconds(row.seconds)} · ${esc(row.status)}" aria-label="Inspect process ${i+1}, ${esc(row.stage)}"></button>`).join('')}</div><div class="timeline-axis"><span>PROCESS 1</span><span>SEQUENCE ORDER · ${records.length} PROCESSES</span></div><div class="legend"><span><i class="baseline"></i>Exploration / baseline</span><span><i></i>Fresh confirmation</span><span>Gold = diagnostic · red = failure</span></div>`;
}
function experimentsView() {
  return section('Experiment timeline',timeline(),'select a process to inspect')+`<div class="measure-heading"><h2>Process inspector</h2><select id="measurement-select" aria-label="Select measurement">${current.records.map((row,i)=>`<option value="${esc(row.path)}" ${selectedRun===row.path?'selected':''}>${i+1} / ${esc(row.stage)} / ${esc(row.status)} / ${esc(row.path)}</option>`).join('')}</select></div>`+(measurement?measurementView():'<div class="spinner">Reading measurement…</div>');
}
function measurementView() {
  const row=current.records.find(r=>r.path===selectedRun)||{}, m=measurement,summary=m.summary||{},runs=summary.runs||[];
  return `${(m.available_runs||[]).length>1?`<label class="note">Repetition <select id="repetition-select" class="trace-select" aria-label="Select repetition">${m.available_runs.map((name,i)=>`<option value="${i+1}" ${name===m.runs[0]?.name?'selected':''}>${esc(name)}</option>`).join('')}</select></label>`:''}<div class="grid"><div>${row.error?`<div class="notice error-box">${esc(row.error)}</div>`:''}${section('Correctness & constraints',runs.map((r,i)=>`<h3>Run ${i+1} · correctness ${esc(r.correctness || 'unknown')}</h3><div class="table-scroll"><table><thead><tr><th>Constraint</th><th>Result</th></tr></thead><tbody>${Object.entries(r.constraints||{}).map(([key,pass])=>`<tr><td>${esc(human(key))}</td><td class="${pass?'green':'red'}">${pass?'Passed':'VIOLATED'}</td></tr>`).join('')}</tbody></table></div><p class="note">${esc((r.warnings||[]).join(' · '))}</p>`).join('')||'<p class="note">No completed correctness result. Failed or running processes must not be interpreted as valid measurements.</p>')}${section('Metrics',code(summary))}${section('Logs & errors',m.runs.map(run=>`<h3>${esc(run.name)}</h3>${Object.entries(run.logs).map(([name,log])=>`<details><summary>${esc(name)} ${log.text?'':'· empty'}</summary><pre class="code">${esc(log.text || '(empty)')}${log.truncated?'\n[truncated at 128 KiB]':''}</pre></details>`).join('')}`).join('')||'<p class="note">No process logs recorded.</p>')}</div><div class="inspector">${section('Execution',`<dl class="kv"><dt>Stage</dt><dd>${esc(human(row.stage))}</dd><dt>Status</dt><dd>${esc(row.status)}</dd><dt>Runtime</dt><dd>${seconds(row.seconds)}</dd><dt>Configuration</dt><dd>${config(row.configuration)}</dd></dl>${selectionForRun(row)}`)}${section('Specification',code(m.spec))}${section('Environment & provenance',code(m.environment))}${section('Execution records',code(m.runs.map(r=>r.execution)))}</div></div>`;
}
function selectionForRun(row) {
  const trials=[...(current.report.trials||[]),...(current.report.confirmation?[current.report.confirmation]:[])];
  const trial=trials.find(t=>(t.pairs||[]).some(p=>p.baseline===row.path||p.candidate===row.path));
  return trial?`<div class="reason"><strong>SELECTION → DECISION</strong>${esc(trial.selection?.basis || 'Frozen finalist, fresh confirmation')}<br><br>${esc(human(trial.comparison?.status))}<br>${esc((trial.comparison?.reasons||[]).map(human).join(' · '))}</div>`:'<p class="note">Baseline or diagnostic control; no candidate decision attached.</p>';
}
async function loadMeasurement(path, repetition=1) {
  const version=++measureGeneration,id=current.id; selectedRun=path; measurement=null; measurementLoading=true; traceTarget=null;
  try {const data=await api(`/api/measurement?id=${encodeURIComponent(id)}&run=${encodeURIComponent(path)}&repetition=${repetition}`);if(version!==measureGeneration||id!==current.id)return; measurement=data; measurementLoading=false; if(tab==='experiments'||tab==='trace')render();}
  catch(error){if(version===measureGeneration){measurementLoading=false;measurement={error:error.message,summary:{},runs:[],spec:{},environment:{}};toast(error.message);render();}}
}
function traceView() {
  const diagnostic=measurement?.runs?.find(x=>x.diagnostics?.graph)?.diagnostics;
  const select=`<select id="measurement-select" class="trace-select" aria-label="Select process for dependency inspection">${current.records.map(row=>`<option value="${esc(row.path)}" ${selectedRun===row.path?'selected':''}>${esc(row.path)} · ${esc(row.stage)}${row.diagnostic?' · diagnostic trace':''}</option>`).join('')}</select>`;
  if(!diagnostic) return select+'<p class="note">No dependency graph in this process. Select a diagnostic process where tracing is enabled. Missing or incomplete trace data cannot establish a bottleneck.</p>';
  const graph=diagnostic.graph,edges=graph.edges||[],targets=[...new Set(edges.map(e=>e.target))];
  if(!targets.includes(traceTarget))traceTarget=targets[0];
  const all=edges.filter(e=>e.target===traceTarget).sort((a,b)=>b.overlap_ns-a.overlap_ns), relevant=all.slice(0,12);
  return select+`<div class="grid"><div>${section('Observed dependency paths',`<select id="trace-target" class="trace-select" aria-label="Select delayed request">${targets.map(t=>`<option value="${esc(t)}" ${t===traceTarget?'selected':''}>${esc(t)}</option>`).join('')}</select>${relevant.map(e=>`<div class="dependency-path"><div class="node">${esc(e.source)}<small>Thread ${esc(e.source_thread_id)} · resource ${esc(e.resource_id)}</small></div><div class="edge">${ms(e.overlap_ns)}<br>→</div><div class="node">${esc(e.target)}<small>${esc(human(e.kind))}</small></div></div>`).join('')||'<p class="note">No supported dependency edges.</p>'}<p class="note">Showing ${relevant.length} of ${all.length} recorded edges for this request, ranked by observed overlap. Edges describe observed overlap, a lower bound on attributed waiting; parallel overlaps are not additive. ${esc(graph.truncated?'Graph was truncated.':'')}</p>`)}${section('Tail requests',code(diagnostic.tail_requests))}</div><div>${section('Trace quality',code({status:diagnostic.status,quality:diagnostic.trace_quality,edge_limit:graph.edge_limit}))}${section('Suspected bottlenecks',(diagnostic.hypotheses||[]).map(h=>`<h3>${esc(human(h.id))}</h3><p>${esc(h.mechanism)}</p><p class="note">${esc(h.caveat)}</p>`).join('')||'<p class="note">No supported hypothesis.</p>')}${section('Limitations',(diagnostic.limitations||[]).map(x=>`<p class="note">${esc(x)}</p>`).join(''))}</div></div>`;
}
function provenanceView() {
  const r=current.report,s=current.state;
  return `<div class="grid"><div>${reproduceSection()}${section('Stored investigation specification',code(current.spec))}${section('Goal & constraints',code(r.goal || current.spec.objective || current.spec.experiment?.objective))}${section('Scientific limitations',(r.limitations||[]).map(x=>`<p>${esc(x)}</p>`).join('')||'<p class="note">See the CLI report and experiment contract.</p>')}</div><div>${section('Host',code(r.host || s.host))}${section('Controller driver',code(r.driver || s.driver || {note:'Driver identity was not separately recorded in this older artifact.'}))}${section('Native source & engines',`<details><summary>Source identity and file hashes</summary>${code(r.source || s.source)}</details><details><summary>Saved engine provenance</summary>${code(s.builds)}</details>${Object.entries(current.build_logs||{}).map(([implementation,logs])=>`<details><summary>${esc(implementation)} · build logs</summary>${Object.entries(logs).map(([name,log])=>`<h3>${esc(name)}</h3><pre class="code">${esc(log.text || '(empty)')}${log.truncated?'\n[truncated]':''}</pre>`).join('')||'<p class="note">No compiler logs; saved or supplied engine.</p>'}</details>`).join('')}<p class="note">Supplied executable provenance can differ from the controller checkout. Jitter does not claim source correspondence where it is not established.</p>`)}${section('Validation command',`<div class="command"><pre class="code">${esc(current.verify)}</pre><button data-copy="verify">Copy</button></div><p class="note">The dashboard displays stored artifact values. Verify evidence checks hashes, correctness, constraints, statistics and controller decisions against raw data. It executes no saved application code.</p>`)}</div></div>`;
}
function overheadView() {
  const r=current.report,d=r.paired_deltas||{},pairs=r.pairs||[];
  return `<div class="notice">${esc(r.interpretation || 'No completed overhead comparison.')}</div><div class="metric-strip">${metric('Signed p99 delta',ms(d.p99_latency_ns?.p50),'Tracing on − off · median paired delta')}${metric('CPU per request',num(d.cpu_ns_per_completed_request?.p50,1)+' ns','Signed on − off · application process')}${metric('Peak RSS delta',bytes(d.peak_rss_bytes?.p50),'Includes reserved trace buffers')}${metric('Comparable pairs',`${pairs.filter(p=>p.comparable).length} / ${pairs.length}`,'Non-comparable pairs never become headline wins')}</div>${section('Alternating trace controls',`<div class="table-scroll"><table><thead><tr><th>Pair</th><th>Comparable</th><th>p99 off</th><th>p99 on</th><th>CPU Δ ns/request</th><th>RSS Δ</th><th>Dropped events</th></tr></thead><tbody>${pairs.map(p=>`<tr><td>${esc(p.pair)}</td><td>${p.comparable?'Yes':'No'}</td><td class="mono">${ms(p.p99_latency_ns?.off)}</td><td class="mono">${ms(p.p99_latency_ns?.on)}</td><td class="mono">${num(p.cpu_ns_per_completed_request?.delta,1)}</td><td class="mono">${bytes(p.peak_rss_bytes?.delta)}</td><td class="mono">${num(p.trace_events_dropped,0)}</td></tr>`).join('')}</tbody></table></div>`)}${section('Interpretation & limits',(r.limitations||[]).map(x=>`<p>${esc(x)}</p>`).join(''))}${reproduceSection()}`;
}
// One event boundary; artifact strings are always escaped before HTML insertion.
document.addEventListener('click',event=>{
  const result=event.target.closest('[data-result]');if(result)return void select(result.dataset.result);
  const t=event.target.closest('[data-tab]');if(t){tab=t.dataset.tab;render();return;}
  const trial=event.target.closest('[data-trial]');if(trial){selectedTrial=trial.dataset.trial==='fresh'?null:Number(trial.dataset.trial);render();return;}
  const run=event.target.closest('[data-run]');if(run){tab='experiments';loadMeasurement(run.dataset.run);render();return;}
  const copy=event.target.closest('[data-copy]');if(copy){navigator.clipboard.writeText(current[copy.dataset.copy]).then(()=>toast('Command copied'),()=>toast('Clipboard unavailable. Select and copy the command.'));}
});
document.addEventListener('change',event=>{if(event.target.id==='measurement-select'){loadMeasurement(event.target.value);render();}if(event.target.id==='repetition-select'){loadMeasurement(selectedRun,Number(event.target.value));render();}if(event.target.id==='trace-target'){traceTarget=event.target.value;render();}});
$('#search').addEventListener('input',drawLibrary);$('#filter').addEventListener('change',drawLibrary);$('#refresh').addEventListener('click',refresh);
$('#verify').addEventListener('click',async()=>{
  if(!current)return;const id=current.id,version=++verifyGeneration;$('#verify').disabled=true;$('#verification').textContent='Checking raw evidence…';$('#verification').className='';
  try{const result=await api('/api/verify?id='+id+'&snapshot='+current.snapshot);if(id!==current.id||version!==verifyGeneration)return;$('#verification').textContent='Verified · '+new Date().toLocaleTimeString();$('#verification').className='verified';$('#verification').title=result.scope;toast(result.scope);}
  catch(error){if(id!==current.id||version!==verifyGeneration)return;$('#verification').textContent='Verification failed';$('#verification').className='error';toast(error.message);}
  finally{if(id===current.id&&version===verifyGeneration)$('#verify').disabled=false;}
});
refresh();
