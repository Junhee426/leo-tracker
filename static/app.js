const {esc,number,statusLabel,scopeLabel,qualityLabel,dateTime,safeUrl,fetchJSON,errorBox,sourceLink,trendBadge,lineChart,monthlyTable}=window.LEO;
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
const state={rows:[],sources:[],changes:null,launches:null,roadmap:null,coverage:null,sortKey:'tracked_in_orbit',sortDir:-1,quality:{rows:[]}};
const sourceMap=()=>Object.fromEntries(state.sources.map(s=>[s.id,s]));

function renderKPIs() {
  $('#kpiNetworks').textContent=number(state.rows.length);
  const observed=state.rows.filter(r=>r.tracked_in_orbit!=null);
  $('#kpiOrbit').textContent=observed.length?number(observed.reduce((sum,r)=>sum+r.tracked_in_orbit,0)):'—';
  $('#kpiOrbitNote').textContent=`관측값 보유 ${observed.length}/${state.rows.length}개 위성망`;
  $('#kpiLaunches').textContent=number(state.launches?.length);
  $('#kpiDelays').textContent=state.roadmap?number(state.roadmap.filter(r=>r.trend==='delayed').length):'—';
  $('#kpiActive').textContent=number(state.rows.filter(r=>['operating','operating_expanding','deploying'].includes(r.status)).length);
}
function renderQuality() {
  const mode={live:'수집 완료',cached:'저장값 사용',partial:'일부 수집 실패',failed:'전체 수집 실패',manual:'수동 자료',seed:'초기 자료'}[state.quality.update_mode]||'기존 스냅샷';
  $('#updatedAt').textContent=dateTime(state.quality.generated_at);
  $('#updateMode').textContent=mode+(state.quality.degraded?' · 오래된 관측값 확인 필요':'');
  const bad=state.quality.rows.filter(r=>['stale','unavailable'].includes(r.status));
  $('#qualityNotice').innerHTML=bad.length?`<div class="quality-warning" role="status">${bad.map(r=>esc(r.name)).join(', ')}: 새 관측값을 확보하지 못했거나 48시간이 지났습니다. 이전 값과 기준일을 확인해 주세요.</div>`:'';
}
function filteredRows() {
  const q=$('#search').value.trim().toLowerCase(),f=$('#statusFilter').value;
  return state.rows.filter(r=>(!q||`${r.name} ${r.operator} ${r.country}`.toLowerCase().includes(q))&&(f==='all'||(f==='operating'?['operating','operating_expanding'].includes(r.status):r.status===f))).sort((a,b)=>{
    const av=a[state.sortKey],bv=b[state.sortKey];
    if(av==null) return bv==null?0:1;
    if(bv==null) return -1;
    return (typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv)))*state.sortDir;
  });
}
function renderTable() {
  const qualities=Object.fromEntries(state.quality.rows.map(r=>[r.id,r]));
  $('#constellationTable tbody').innerHTML=filteredRows().map(r=>{
    const q=qualities[r.id]||r.observation||{};
    return `<tr><td class="name-cell"><a href="/constellation/${encodeURIComponent(r.id)}"><strong>${esc(r.flag)} ${esc(r.name)}</strong></a><small>${esc(r.operator)}</small></td><td><span class="status ${esc(r.status)}">${esc(statusLabel(r.status))}</span></td><td class="num"><strong>${number(r.tracked_in_orbit)}</strong><br><small>${r.tracked_in_orbit==null?'미관측':esc(qualityLabel(q.status))}</small>${r.reference_count!=null?`<br><small>별도 발표 ${number(r.reference_count)} · ${esc(r.reference_date)}</small>`:''}</td><td class="num"><strong>${number(r.planned_satellites)}</strong><br><small>${esc(r.planned_label)}</small></td><td class="num" title="${esc(r.progress?.note)}">${r.deployment_pct==null?'비교 보류':number(r.deployment_pct)+'%'}<br><small>${esc(scopeLabel(r.plan_scope))}</small></td><td>${esc(r.orbit_label||'—')}</td><td>${esc(r.next_milestone||'—')}<br><small>${esc(r.target_service)}</small></td><td>${esc(r.last_data_date||'—')}<br><small>수집 ${dateTime(q.last_success_at)}</small></td><td><a href="/constellation/${encodeURIComponent(r.id)}">상세 →</a></td></tr>`;
  }).join('')||'<tr><td colspan="9" class="empty">검색 결과가 없습니다.</td></tr>';
}
function renderLaunches() {
  if(!state.launches) return;
  const selected=$('#launchFilter').value;
  const rows=state.launches.filter(r=>selected==='all'||r.constellation_id===selected).sort((a,b)=>(b.date_start||'').localeCompare(a.date_start||''));
  const completed=rows.filter(r=>r.status==='completed');
  $('#launchSummary').innerHTML=`<div><span>수록 기록</span><strong>${rows.length}</strong></div><div><span>수록 완료 임무</span><strong>${completed.length}</strong></div><div><span>수록 완료 임무의 위성 수</span><strong>${number(completed.reduce((s,r)=>s+(r.satellites||0),0))}</strong></div>`;
  const byId=sourceMap();
  $('#launchTable tbody').innerHTML=rows.map(r=>`<tr><td>${esc(r.date_label)}</td><td><a href="/constellation/${encodeURIComponent(r.constellation_id)}">${esc(r.constellation)}</a></td><td><strong>${esc(r.mission)}</strong><br><small>${esc(r.note)}</small></td><td><span class="mission-status ${esc(r.status)}">${r.status==='completed'?'완료':'계획'}</span></td><td>${esc(r.vehicle)}</td><td class="num">${number(r.satellites)}</td><td>${esc(r.site)}</td><td>${sourceLink(r.source_id,byId)}</td></tr>`).join('')||'<tr><td colspan="8" class="empty">수록된 발사 기록이 없습니다.</td></tr>';
  if(state.coverage) $('#coverageList').innerHTML=state.coverage.filter(r=>selected==='all'||r.constellation_id===selected).map(r=>`<div class="coverage-item"><strong>${esc(state.rows.find(x=>x.id===r.constellation_id)?.name||r.constellation_id)}</strong><span>${r.status==='complete_for_period'?'명시 기간 수록 완료':r.status==='not_collected'?'완료 임무 미수록':'일부 임무 수록'} · ${esc(r.from||'—')} ~ ${esc(r.through||'—')}</span><small>${esc(r.note)}</small></div>`).join('');
}
function renderRoadmap() {
  if(!state.roadmap) return;
  const byId=sourceMap();
  $('#roadmapHistory').innerHTML=state.roadmap.map(r=>`<article class="roadmap-card"><div class="roadmap-card-top"><div><small>${esc(r.constellation)}</small><h4>${esc(r.milestone)}</h4></div>${trendBadge(r)}</div><div class="plan-compare"><div><span>기준 계획</span><strong>${esc(r.baseline)}</strong></div><div class="plan-arrow">→</div><div><span>현재 계획</span><strong>${esc(r.current)}</strong></div></div><p>${esc(r.note)}</p>${sourceLink(r.source_id,byId)}</article>`).join('');
}
function renderChanges() {
  if(!state.changes) return;
  const byId=sourceMap();
  $('#changeList').innerHTML=state.changes.map(r=>`<div class="change-item"><div class="change-date">${esc(r.date)}<br>${r.type==='source_change'?'집계 기준 변경':esc(r.type)}</div><div class="change-title"><strong>${esc(r.constellation)}</strong><small>${esc(r.field)}</small></div><div class="delta">${esc(r.previous??'—')} → <strong>${esc(r.current??'—')}</strong><br><small>${sourceLink(r.source_id,byId)}${r.note?' · '+esc(r.note):''}</small></div></div>`).join('')||'<div class="empty">변경 이력이 없습니다.</div>';
}
function renderSources() {
  $('#sourceList').innerHTML=state.sources.map(r=>`<article class="source-card"><span class="tag ${esc(r.type)}">${esc(r.type)}</span><h4>${esc(r.title)}</h4><small>${esc(r.publisher)} · ${esc(r.date)}</small><p>${esc(r.note)}</p><a href="${safeUrl(r.url)}" target="_blank" rel="noopener">원문 보기 ↗</a></article>`).join('');
}
async function loadPart(key,url,render,errorId) {
  try { const data=await fetchJSON(url); if(!Array.isArray(data))throw new Error('Expected a list'); state[key]=data; $('#'+errorId).innerHTML=''; render(); renderKPIs(); }
  catch { errorBox($('#'+errorId),()=>loadPart(key,url,render,errorId)); }
}
let trendRequest=0;
let activityRequest=0;
async function loadActivity() {
  const requestId=++activityRequest;
  $('#activityTable').innerHTML='<p class="empty">최근 동향을 불러오는 중입니다.</p>';
  $('#activityError').innerHTML='';
  try {
    const data=await fetchJSON('/api/activity?days='+($('#activityDays').value||'30'));
    if(requestId!==activityRequest) return;
    $('#activityTable').innerHTML=window.LEO.activityTable(data.rows);
    $('#activityNote').textContent=`${data.from} ~ ${data.through} (UTC) · ${data.note}`+(data.warnings.length?' 일부 스냅샷을 읽지 못했습니다.':'');
  } catch {
    if(requestId!==activityRequest) return;
    $('#activityTable').innerHTML='';
    errorBox($('#activityError'),loadActivity);
  }
}
async function loadTrends() {
  const requestId=++trendRequest,cid=$('#trendFilter').value,months=$('#trendMonths').value,compareAll=cid==='__all__';
  const params=new URLSearchParams(compareAll?{months}:{constellation_id:cid,months});
  try {
    const data=await fetchJSON('/api/trends?'+params);
    if(requestId!==trendRequest) return;
    $('#trendError').innerHTML='';
    if (compareAll) {
      $('#trendChart').innerHTML=window.LEO.multiLineChart(data.series);
      $('#monthlyTable').innerHTML='<p class="empty">위성망을 하나 선택하면 월별 비교표가 표시됩니다.</p>';
    } else {
      $('#trendChart').innerHTML=lineChart(data.series.find(s=>s.constellation_id===cid)?.points||[]);
      $('#monthlyTable').innerHTML=monthlyTable(data.monthly);
    }
    $('#trendNote').textContent=data.note+(data.warnings.length?' 일부 스냅샷을 읽지 못했습니다.':'');
    $('#downloadTrends').href='/download/trends.csv?'+params;
  } catch { if(requestId===trendRequest) errorBox($('#trendError'),loadTrends); }
}
async function loadStatus() {
  try {
    const data=await fetchJSON('/api/status'); state.rows=data.constellations; state.quality=data.quality;
    $('#overviewError').innerHTML=''; renderQuality(); renderKPIs(); renderTable();
    const options=state.rows.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
    $('#launchFilter').innerHTML='<option value="all">전체 위성망</option>'+options;
    $('#trendFilter').innerHTML='<option value="__all__">전체 비교</option>'+options;
    renderLaunches(); loadTrends();
  } catch { errorBox($('#overviewError'),loadStatus,'위성 현황을 불러오지 못했습니다.'); $('#updateMode').textContent='현황 로딩 실패'; }
}
async function init() {
  return Promise.allSettled([
    loadActivity(),
    loadStatus(),
    loadPart('launches','/api/launches',renderLaunches,'launchError'),
    loadPart('coverage','/api/launch-coverage',renderLaunches,'coverageError'),
    loadPart('roadmap','/api/roadmap-history',renderRoadmap,'roadmapError'),
    loadPart('changes','/api/changes',renderChanges,'changesError'),
    loadPart('sources','/api/sources',()=>{renderSources();renderLaunches();renderRoadmap();renderChanges();},'sourcesError')
  ]);
}
$('#search').addEventListener('input',renderTable);
$('#activityDays').addEventListener('change',loadActivity);
$('#statusFilter').addEventListener('change',renderTable);
$('#launchFilter').addEventListener('change',renderLaunches);
$('#trendFilter').addEventListener('change',loadTrends);
$('#trendMonths').addEventListener('change',loadTrends);
$$('th[data-sort] button').forEach(button=>button.addEventListener('click',()=>{const th=button.closest('th'),key=th.dataset.sort;state.sortDir=state.sortKey===key?-state.sortDir:1;state.sortKey=key;$$('th[data-sort]').forEach(x=>x.removeAttribute('aria-sort'));th.setAttribute('aria-sort',state.sortDir===1?'ascending':'descending');renderTable();}));
$$('.tab').forEach(button=>button.addEventListener('click',()=>{$$('.tab').forEach(x=>{x.classList.remove('active');x.setAttribute('aria-pressed','false');});$$('.tab-panel').forEach(x=>x.classList.remove('active'));button.classList.add('active');button.setAttribute('aria-pressed','true');document.getElementById(button.dataset.target).classList.add('active');}));
init();
