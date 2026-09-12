const {esc,number,statusLabel,qualityLabel,dateTime,fetchJSON,errorBox,sourceLink,trendBadge,crosschecks,lineChart,monthlyTable,histogramChart}=window.LEO;
const $=selector=>document.querySelector(selector);
const cid=document.body.dataset.constellationId;
let relevantSourceIds=new Set();
let record=null,sources={},page=1,objectTotal=0,objectRequest=0,historyRequest=0;

function showPrimary(data) {
  record=data.constellation;
  relevantSourceIds=new Set([...(record.source_ids||[]),...(data.sources||[]).map(s=>s.id)]);
  document.title=record.name+' · Global LEO Tracker';
  $('#detailName').textContent=`${record.flag||''} ${record.name}`;
  $('#detailOperator').textContent=`${record.operator} · ${record.country}`;
  $('#detailStatus').className='status '+record.status;
  $('#detailStatus').textContent=statusLabel(record.status);
  $('#detailTracked').textContent=number(record.tracked_in_orbit);
  $('#detailTrackedSource').textContent=qualityLabel(data.quality.status);
  $('#detailPlanned').textContent=number(record.planned_satellites);
  $('#detailPlannedLabel').textContent=record.planned_label||'—';
  $('#detailPct').textContent=record.deployment_pct==null?'비교 보류':number(record.deployment_pct)+'%';
  $('#detailPct').title=record.progress?.note||'';
  $('#detailDataDate').textContent='관측 기준일 '+(record.last_data_date||'—');
  $('#detailOrbit').textContent=record.orbit_label||'—';
  $('#detailMilestone').textContent=record.next_milestone||'—';
  $('#detailService').textContent=record.target_service||'—';
  $('#detailCountry').textContent=record.country||'—';
  $('#detailNote').textContent=(record.note||'')+' '+(record.progress?.note||'');
  $('#detailQuality').innerHTML=`<p class="${['stale','unavailable'].includes(data.quality.status)?'quality-warning':'chart-note'}">${esc(qualityLabel(data.quality.status))} · 마지막 수집 성공 ${esc(dateTime(data.quality.last_success_at))}${record.reference_count!=null?` · 별도 발표값 ${number(record.reference_count)} (${esc(record.reference_date)})`:''}</p>`;
  $('#detailCrosscheck').innerHTML=crosschecks(record.crosscheck,sources);
  renderSources();
}
async function section(url,errorId,render) {
  try { const data=await fetchJSON(url);$('#'+errorId).innerHTML='';render(data); }
  catch { errorBox($('#'+errorId),()=>section(url,errorId,render)); }
}
function renderLaunches(data) {
  const rows=data.filter(r=>r.constellation_id===cid).sort((a,b)=>(b.date_start||'').localeCompare(a.date_start||''));
  $('#detailLaunchCount').textContent=number(rows.length);
  $('#detailLaunches').innerHTML=rows.map(r=>`<tr><td>${esc(r.date_label)}</td><td><strong>${esc(r.mission)}</strong><br><small>${esc(r.note)}</small></td><td>${r.status==='completed'?'완료':'계획'}</td><td>${esc(r.vehicle)}</td><td class="num">${number(r.satellites)}</td><td>${esc(r.site)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">완전한 발사 목록이 아닙니다. 수록된 임무가 없습니다.</td></tr>';
}
function renderRoadmap(data) {
  $('#detailRoadmap').innerHTML=data.filter(r=>r.constellation_id===cid).map(r=>`<div class="mini-roadmap-item"><div><strong>${esc(r.milestone)}</strong><small>${esc(r.category)}</small></div>${trendBadge(r)}<div class="mini-compare"><span>${esc(r.baseline)}</span><b>→</b><span>${esc(r.current)}</span></div></div>`).join('')||'<div class="empty">공개된 비교 기준선이 없습니다.</div>';
}
function renderChanges(data) {
  $('#detailChanges').innerHTML=data.filter(r=>r.constellation_id===cid).map(r=>`<div class="stack-item"><small>${esc(r.date)} · ${r.type==='source_change'?'집계 기준 변경':esc(r.type)}</small><strong>${esc(r.field)}</strong><p>${esc(r.previous??'—')} → ${esc(r.current??'—')}</p>${r.note?`<small>${esc(r.note)}</small>`:''}</div>`).join('')||'<div class="empty">변경 이력이 없습니다.</div>';
}
async function loadObjects() {
  const requestId=++objectRequest;
  const params=new URLSearchParams({q:$('#objectQuery').value,presence:$('#objectPresence').value,page,per_page:25});
  try {
    const data=await fetchJSON(`/api/objects/${encodeURIComponent(cid)}?${params}`);
    if(requestId!==objectRequest) return;
    $('#objectError').innerHTML='';objectTotal=data.total;
    $('#objectSummary').textContent=data.first_observed_at?`관측 이력 시작 ${dateTime(data.first_observed_at)} · 검색 결과 ${number(data.total)}개`:'첫 실제 카탈로그 수집 후 NORAD별 관측 이력이 표시됩니다. 기존 집계 스냅샷에는 개별 위성 정보가 없습니다.';
    $('#objectTable').innerHTML=data.objects.length?`<div class="table-wrap"><table><thead><tr><th>NORAD</th><th>위성명</th><th>국제식별자</th><th>첫 관측 (UTC)</th><th>마지막 관측 (UTC)</th><th>최근 카탈로그</th></tr></thead><tbody>${data.objects.map(r=>`<tr><td><button class="object-link" data-norad="${r.norad_cat_id}" type="button">${r.norad_cat_id}</button></td><td>${esc(r.object_name)}</td><td>${esc(r.object_id)}</td><td>${esc(r.first_seen_at.slice(0,10))}</td><td>${esc(r.last_seen_at.slice(0,10))}</td><td>${r.present?'수록':'미수록 · 원인 미확인'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">조건에 맞는 관측 이력이 없습니다.</div>';
    $('#objectPrev').disabled=page<=1;$('#objectNext').disabled=page*25>=objectTotal;
    $('#objectPage').textContent=`${page} / ${Math.max(1,Math.ceil(objectTotal/25))}`;
    document.querySelectorAll('[data-norad]').forEach(button=>button.addEventListener('click',()=>loadHistory(button.dataset.norad)));
  } catch { if(requestId===objectRequest)errorBox($('#objectError'),loadObjects); }
}
async function loadHistory(norad) {
  const requestId=++historyRequest;
  $('#objectHistory').hidden=false;
  $('#objectHistoryTitle').textContent=`NORAD ${norad} · 최근 90일의 수집 성공일`;
  try {
    const data=await fetchJSON(`/api/objects/${encodeURIComponent(cid)}/${encodeURIComponent(norad)}?days=90`);
    if(requestId!==historyRequest)return;
    $('#objectHistoryRows').innerHTML=`<p class="chart-note">${esc(data.note)}</p><div class="table-wrap"><table><thead><tr><th>관측일 (UTC)</th><th>카탈로그</th><th>궤도요소 기준시각 (UTC)</th><th class="num">장반경 환산 고도 (km)</th><th class="num">경사각 (°)</th></tr></thead><tbody>${data.samples.map(s=>`<tr><td>${esc(s.date)}</td><td>${s.present?'수록':'미수록'}</td><td>${esc(s.observation?.epoch||'—')}</td><td class="num">${number(s.observation?.altitude_km)}</td><td class="num">${number(s.observation?.inclination_deg)}</td></tr>`).join('')}</tbody></table></div>`;
  } catch { if(requestId===historyRequest)errorBox($('#objectHistoryRows'),()=>loadHistory(norad)); }
}
async function loadShells() {
  try {
    const data=await fetchJSON(`/api/constellation/${encodeURIComponent(cid)}/shells`);
    $('#detailShellError').innerHTML='';
    $('#detailShellNote').textContent=`${data.note} · 총 ${number(data.total)}기`;
    $('#detailAltitudeChart').innerHTML=histogramChart(data.altitude_bins,'range_km','km');
    $('#detailInclinationChart').innerHTML=histogramChart(data.inclination_bins,'range_deg','°');
  } catch { errorBox($('#detailShellError'),loadShells); }
}
async function loadPrimary() {
  try {const data=await fetchJSON('/api/constellation/'+encodeURIComponent(cid));$('#detailError').innerHTML='';showPrimary(data);}
  catch {errorBox($('#detailError'),loadPrimary,'위성 현황을 읽지 못했습니다.');$('#detailName').textContent='현황 로딩 실패';}
}
async function init() {
  return Promise.allSettled([
    loadPrimary(),
    section('/api/launches','detailLaunchError',renderLaunches),
    section('/api/roadmap-history','detailRoadmapError',renderRoadmap),
    section('/api/changes','detailChangesError',renderChanges),
    section('/api/sources','detailSourcesError',data=>{sources=Object.fromEntries(data.map(s=>[s.id,s]));if(record){$('#detailCrosscheck').innerHTML=crosschecks(record.crosscheck,sources);renderSources();}}),
    section('/api/launch-coverage','detailCoverageError',data=>{const c=data.find(r=>r.constellation_id===cid);$('#detailCoverage').textContent=c?`${c.status==='not_collected'?'완료 임무 미수록':'수록 범위: '+(c.from||'—')+' ~ '+(c.through||'—')} · ${c.note}`:'';}),
    section('/api/trends?'+new URLSearchParams({constellation_id:cid,months:12}),'detailTrendError',data=>{$('#detailTrendChart').innerHTML=lineChart(data.series.find(r=>r.constellation_id===cid)?.points||[]);$('#detailMonthly').innerHTML=monthlyTable(data.monthly);}),
    loadShells(),
    loadObjects()
  ]);
}
function renderSources() {
  if(!record)return;
  $('#detailSources').innerHTML=[...new Set([...relevantSourceIds,...record.source_ids])].map(id=>sources[id]).filter(Boolean).map(s=>`<div class="stack-item"><small>${esc(s.type)} · ${esc(s.date)}</small><strong>${esc(s.publisher)}</strong><p>${esc(s.title)}</p>${sourceLink(s.id,sources)}</div>`).join('');
}
$('#objectSearch').addEventListener('submit',event=>{event.preventDefault();page=1;loadObjects();});
$('#objectPresence').addEventListener('change',()=>{page=1;loadObjects();});
$('#objectPrev').addEventListener('click',()=>{if(page>1){page--;loadObjects();}});
$('#objectNext').addEventListener('click',()=>{if(page*25<objectTotal){page++;loadObjects();}});
init().then(renderSources);
