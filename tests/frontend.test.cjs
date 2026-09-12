const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const root=require('node:path').join(__dirname,'..');
const current=JSON.parse(fs.readFileSync(root+'/data/current.json'));
const launches=JSON.parse(fs.readFileSync(root+'/data/launches.json'));

function environment(respond) {
  const elements=new Map();
  const el=selector=>{
    if(!elements.has(selector)) elements.set(selector,{innerHTML:'',textContent:'',value:selector==='#statusFilter'||selector==='#launchFilter'?'all':selector==='#trendMonths'?'12':selector==='#trendFilter'?'starlink':'',dataset:{},hidden:true,addEventListener(){},querySelector(){return {onclick:null};}});
    return elements.get(selector);
  };
  const context={console,Intl,URL,URLSearchParams,AbortController,setTimeout,clearTimeout,
    document:{body:{dataset:{constellationId:'starlink'}},querySelector:el,querySelectorAll:()=>[],getElementById:el},
    fetch:async url=>{const body=await respond(String(url));return {ok:true,status:200,json:async()=>body};}};
  context.window=context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(root+'/static/common.js','utf8'),context);
  return {context,el};
}

function statusData() {
  return {...current,quality:{update_mode:current.update_mode,generated_at:current.generated_at,degraded:false,rows:current.constellations.map(r=>({id:r.id,name:r.name,...r.observation}))}};
}
function loadScript(context,filename) {
  let source=fs.readFileSync(root+'/static/'+filename,'utf8');
  source=source.replace(/\ninit\(\)(?:\.then\(renderSources\))?;\s*$/,'');
  vm.runInContext(source,context);
}

test('a failed source request does not hide successful overview data',async()=>{
  const {context,el}=environment(url=>{
    if(url==='/api/sources') throw new Error('source outage');
    if(url==='/api/status')return statusData();
    if(url==='/api/launches')return launches;
    if(url.startsWith('/api/trends'))return {series:[],monthly:[],warnings:[],note:''};
    return [];
  });
  loadScript(context,'app.js');
  await context.init();
  assert.match(el('#constellationTable tbody').innerHTML,/Starlink/);
  assert.equal(el('#kpiNetworks').textContent,'7');
  assert.match(el('#sourcesError').innerHTML,/다시 시도/);
  assert.equal(el('#overviewError').innerHTML,'');
});

test('unknown tracked counts are displayed as unavailable, not zero',async()=>{
  const payload=statusData();
  payload.constellations=[{...payload.constellations[0],tracked_in_orbit:null,reference_count:null}];
  const {context,el}=environment(url=>url==='/api/status'?payload:url.startsWith('/api/trends')?{series:[],monthly:[],warnings:[],note:''}:[]);
  loadScript(context,'app.js');await context.init();
  assert.match(el('#constellationTable tbody').innerHTML,/<strong>—<\/strong>/);
  assert.equal(el('#kpiOrbit').textContent,'—');
});

test('crosscheck rendering keeps the server decision without recomputing confidence',()=>{
  const {context}=environment(()=>[]);
  const html=context.LEO.crosschecks({groups:[{metric_label:'배치 발표',scope:'reported',status:'same_origin',label:'동일 원천의 반복 발표',points:[{source_id:'amazon',value:396,date:'2026-07-02'},{source_id:'amazon',value:400,date:'2026-07-30',qualifier:'approx'}]}]},{});
  assert.match(html,/동일 원천의 반복 발표/);
  assert.doesNotMatch(html,/교차검증됨/);
});

test('date gaps break the chart line and null month deltas remain unavailable',()=>{
  const {context}=environment(()=>[]);
  const html=context.LEO.lineChart([{date:'2026-09-01',value:100},{date:'2026-09-02',value:102},{date:'2026-09-05',value:105}]);
  const path=html.match(/<path d="([^"]*)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,2);
  const table=context.LEO.monthlyTable([{month:'2026-09',baseline_date:'2026-09-01',end_date:'2026-09-01',start_count:100,end_count:100,net_change:null,observed_days:1,expected_days:5,complete_month:false}]);
  assert.match(table,/일부 기간/);assert.match(table,/—/);
});

test('detail remains usable if launch history fails and sources finish before primary',async()=>{
  const r=current.constellations[0];
  const {context,el}=environment(async url=>{
    if(url.startsWith('/api/constellation/')){await new Promise(resolve=>setTimeout(resolve,5));return {constellation:r,quality:{...r.observation,status:'fresh'}};}
    if(url==='/api/launches')throw new Error('outage');
    if(url==='/api/sources')return [{id:r.source_ids[0],publisher:'Test Source',title:'Evidence',url:'https://example.com'}];
    if(url.startsWith('/api/objects/'))return {objects:[],total:0,first_observed_at:null};
    if(url.startsWith('/api/trends'))return {series:[],monthly:[]};
    return [];
  });
  loadScript(context,'detail.js');await context.init();context.renderSources();
  assert.match(el('#detailName').textContent,/Starlink/);
  assert.match(el('#detailLaunchError').innerHTML,/다시 시도/);
  assert.match(el('#detailSources').innerHTML,/Test Source/);
});

test('untrusted HTML and URL schemes cannot become markup or script links',()=>{
  const {context}=environment(()=>[]);
  assert.equal(context.LEO.esc('<img onerror="bad">'),'&lt;img onerror=&quot;bad&quot;&gt;');
  assert.equal(context.LEO.safeUrl('javascript:alert(1)'),'#');
});

test('activity comparison preserves missing values and escapes network names',()=>{
  const {context}=environment(()=>[]);
  const html=context.LEO.activityTable([{constellation_id:'test',constellation:'<img>',
    baseline_date:null,end_date:null,start_count:null,end_count:null,net_change:null,change_pct:null,
    observed_days:0,expected_days:8,missing_days:8,coverage:'partial',comparison_status:'insufficient'}]);
  assert.match(html,/&lt;img&gt;/);
  assert.match(html,/관측 공백 8일/);
  assert.match(html,/비교 관측 부족/);
  assert.doesNotMatch(html,/—%|<img>/);
});

test('activity request failure clears previous comparison and allows retry',async()=>{
  const {context,el}=environment(()=>{throw new Error('outage');});
  loadScript(context,'app.js');
  el('#activityTable').innerHTML='old comparison';
  await context.loadActivity();
  assert.equal(el('#activityTable').innerHTML,'');
  assert.match(el('#activityError').innerHTML,/다시 시도/);
});

test('a late activity response cannot overwrite a newer period selection',async()=>{
  const pending=[];
  const {context,el}=environment(()=>new Promise(resolve=>pending.push(resolve)));
  loadScript(context,'app.js');
  el('#activityDays').value='7';
  const first=context.loadActivity();
  el('#activityDays').value='90';
  const second=context.loadActivity();
  pending[1]({rows:[],from:'new period',through:'today',note:'new',warnings:[]});
  await second;
  pending[0]({rows:[],from:'old period',through:'today',note:'old',warnings:[]});
  await first;
  assert.match(el('#activityNote').textContent,/new period/);
  assert.doesNotMatch(el('#activityNote').textContent,/old period/);
});
