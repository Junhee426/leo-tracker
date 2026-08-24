let state = { rows: [], sources: [], changes: [], launches: [], roadmapHistory: [], sortKey: 'tracked_in_orbit', sortDir: -1 };

const fmt = new Intl.NumberFormat('ko-KR');
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function statusLabel(status) {
  return ({operating:'운영', operating_expanding:'운영·확장', deploying:'구축중', development:'개발'})[status] || status;
}
function statusGroup(status) { return ['operating','operating_expanding'].includes(status) ? 'operating' : status; }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function sourceMap() { return Object.fromEntries(state.sources.map(s => [s.id, s])); }

function renderKPIs(data) {
  const rows = data.constellations || [];
  $('#kpiNetworks').textContent = rows.length;
  $('#kpiOrbit').textContent = fmt.format(rows.reduce((s,r) => s + (Number(r.tracked_in_orbit)||0), 0));
  $('#kpiLaunches').textContent = fmt.format(state.launches.length);
  $('#kpiDelays').textContent = state.roadmapHistory.filter(x => x.trend === 'delayed').length;
  $('#kpiActive').textContent = rows.filter(r => ['operating','operating_expanding','deploying'].includes(r.status)).length;
  if (data.generated_at) {
    const d = new Date(data.generated_at);
    $('#updatedAt').textContent = d.toLocaleString('ko-KR', {timeZone:'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
  }
  $('#updateMode').textContent = data.update_mode === 'live' ? 'LIVE CELESTRAK SNAPSHOT' : data.update_mode === 'partial' ? 'PARTIAL UPDATE' : 'V1.1 SEED DATA';
}

function filteredRows() {
  const q = $('#search').value.trim().toLowerCase();
  const f = $('#statusFilter').value;
  return state.rows.filter(r => {
    const text = `${r.name} ${r.operator} ${r.country}`.toLowerCase();
    return (!q || text.includes(q)) && (f === 'all' || statusGroup(r.status) === f);
  }).sort((a,b) => {
    const av=a[state.sortKey], bv=b[state.sortKey];
    if (typeof av === 'number' && typeof bv === 'number') return (av-bv)*state.sortDir;
    return String(av??'').localeCompare(String(bv??''))*state.sortDir;
  });
}

function renderTable() {
  const rows = filteredRows();
  const body = $('#constellationTable tbody');
  if (!rows.length) { body.innerHTML = `<tr><td colspan="9" class="empty">검색 결과가 없습니다.</td></tr>`; return; }
  body.innerHTML = rows.map(r => {
    const pct = r.deployment_pct == null ? '—' : `${r.deployment_pct.toFixed(1)}%`;
    const bar = r.deployment_pct == null ? '' : `<span class="progress"><i style="width:${Math.min(100,r.deployment_pct)}%"></i></span>`;
    return `<tr class="clickable-row" data-href="/constellation/${encodeURIComponent(r.id)}" title="${escapeHtml(r.note)}">
      <td class="name-cell"><strong>${escapeHtml(r.flag)} ${escapeHtml(r.name)}</strong><small>${escapeHtml(r.operator)}</small></td>
      <td><span class="status ${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span></td>
      <td class="num"><strong>${fmt.format(r.tracked_in_orbit ?? 0)}</strong><br><small>${escapeHtml(r.tracked_source)}</small></td>
      <td class="num"><strong>${r.planned_satellites ? fmt.format(r.planned_satellites) : '—'}</strong><br><small>${escapeHtml(r.planned_label || '')}</small></td>
      <td class="num">${pct}${bar}</td>
      <td>${escapeHtml(r.orbit_label || '—')}</td>
      <td>${escapeHtml(r.next_milestone || '—')}<br><small>${escapeHtml(r.target_service || '')}</small></td>
      <td>${escapeHtml(r.last_data_date || '—')}</td>
      <td><a class="detail-link" href="/constellation/${encodeURIComponent(r.id)}">Detail →</a></td>
    </tr>`;
  }).join('');
  $$('.clickable-row').forEach(row => row.addEventListener('click', e => {
    if (e.target.closest('a')) return;
    location.href = row.dataset.href;
  }));
}

function renderLaunches() {
  const select = $('#launchFilter');
  const names = [...new Set(state.launches.map(x => x.constellation))].sort();
  select.innerHTML = '<option value="all">전체 위성망</option>' + names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
  const draw = () => {
    const value = select.value;
    const rows = state.launches.filter(x => value === 'all' || x.constellation === value).sort((a,b) => b.date.localeCompare(a.date));
    const completed = rows.filter(x => x.status === 'completed');
    const satellites = completed.reduce((s,x) => s + (Number(x.satellites)||0), 0);
    $('#launchSummary').innerHTML = `<div><span>Records</span><strong>${fmt.format(rows.length)}</strong></div><div><span>Completed</span><strong>${fmt.format(completed.length)}</strong></div><div><span>Satellites in listed missions</span><strong>${fmt.format(satellites)}</strong></div>`;
    const byId = sourceMap();
    $('#launchTable tbody').innerHTML = rows.map(x => {
      const src = byId[x.source_id];
      return `<tr><td>${escapeHtml(x.date)}</td><td><a href="/constellation/${escapeHtml(x.constellation_id)}">${escapeHtml(x.constellation)}</a></td><td><strong>${escapeHtml(x.mission)}</strong><br><small>${escapeHtml(x.note || '')}</small></td><td><span class="mission-status ${escapeHtml(x.status)}">${escapeHtml(x.status)}</span></td><td>${escapeHtml(x.vehicle || '—')}</td><td class="num">${x.satellites == null ? '—' : fmt.format(x.satellites)}</td><td>${escapeHtml(x.site || '—')}</td><td>${src ? `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.publisher)} ↗</a>` : escapeHtml(x.source_id || '—')}</td></tr>`;
    }).join('') || '<tr><td colspan="8" class="empty">발사기록이 없습니다.</td></tr>';
  };
  select.addEventListener('change', draw);
  draw();
}

function trendBadge(item) {
  const label = item.trend === 'delayed' ? `+${item.delta_months ?? '?'} mo` : item.trend === 'expanded' ? 'SCOPE ↑' : item.trend === 'completed' ? 'DONE' : 'ON TRACK';
  return `<span class="trend ${escapeHtml(item.trend)}">${escapeHtml(label)}</span>`;
}

function renderRoadmap() {
  const byId = sourceMap();
  $('#roadmapHistory').innerHTML = state.roadmapHistory.map(item => {
    const src = byId[item.source_id];
    const baselineSrc = byId[item.baseline_source_id];
    return `<article class="roadmap-card">
      <div class="roadmap-card-top"><div><small>${escapeHtml(item.constellation)} · ${escapeHtml(item.category)}</small><h4>${escapeHtml(item.milestone)}</h4></div>${trendBadge(item)}</div>
      <div class="plan-compare"><div><span>BASELINE</span><strong>${escapeHtml(item.baseline)}</strong></div><div class="plan-arrow">→</div><div><span>CURRENT</span><strong>${escapeHtml(item.current)}</strong></div></div>
      <p>${escapeHtml(item.note || '')}</p>${baselineSrc ? `<a href="${escapeHtml(baselineSrc.url)}" target="_blank" rel="noopener">Baseline: ${escapeHtml(baselineSrc.publisher)} ↗</a> · ` : ''}${src ? `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">Current: ${escapeHtml(src.publisher)} ↗</a>` : ''}
    </article>`;
  }).join('');

  const rows = [...state.rows].sort((a,b)=>(b.deployment_pct||0)-(a.deployment_pct||0));
  $('#roadmapList').innerHTML = rows.map(r => `<div class="roadmap-row">
    <div class="roadmap-name"><strong>${escapeHtml(r.flag)} ${escapeHtml(r.name)}</strong><small>${escapeHtml(statusLabel(r.status))} · ${escapeHtml(r.next_milestone || '')}</small></div>
    <div class="bigbar"><i style="width:${Math.min(100,r.deployment_pct||0)}%"></i></div>
    <div class="roadmap-meta"><strong>${r.deployment_pct == null ? '—' : r.deployment_pct.toFixed(1)+'%'}</strong><br><small>${fmt.format(r.tracked_in_orbit||0)} / ${r.planned_satellites ? fmt.format(r.planned_satellites) : '—'}</small></div>
  </div>`).join('');
}

function renderChanges() {
  const byId = sourceMap();
  $('#changeList').innerHTML = state.changes.map(c => {
    const src = byId[c.source_id];
    return `<div class="change-item"><div class="change-date">${escapeHtml(c.date)}<br>${escapeHtml(c.type)}</div><div class="change-title"><strong>${escapeHtml(c.constellation)}</strong><small>${escapeHtml(c.field)}</small></div><div class="delta">${escapeHtml(c.previous)} <span class="arrow">→</span> <strong>${escapeHtml(c.current)}</strong>${src ? `<br><small><a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.publisher)} source ↗</a></small>` : ''}</div></div>`;
  }).join('') || '<div class="empty">기록된 변경사항이 없습니다.</div>';
}

function renderSources() {
  $('#sourceList').innerHTML = state.sources.map(s => `<article class="source-card"><span class="tag ${escapeHtml(s.type)}">${escapeHtml(s.type)}</span><h4>${escapeHtml(s.title)}</h4><small>${escapeHtml(s.publisher)} · ${escapeHtml(s.date)}</small><p>${escapeHtml(s.note)}</p><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">원문 보기 ↗</a></article>`).join('');
}

async function init() {
  try {
    const [statusRes, changesRes, sourcesRes, launchesRes, roadmapRes] = await Promise.all([fetch('/api/status'), fetch('/api/changes'), fetch('/api/sources'), fetch('/api/launches'), fetch('/api/roadmap-history')]);
    const data = await statusRes.json();
    state.rows = data.constellations || [];
    state.changes = await changesRes.json();
    state.sources = await sourcesRes.json();
    state.launches = await launchesRes.json();
    state.roadmapHistory = await roadmapRes.json();
    renderKPIs(data); renderTable(); renderLaunches(); renderRoadmap(); renderChanges(); renderSources();
  } catch (err) {
    console.error(err); $('#updateMode').textContent = 'DATA LOAD ERROR';
  }
}

$('#search').addEventListener('input', renderTable);
$('#statusFilter').addEventListener('change', renderTable);
$$('th[data-sort]').forEach(th => th.addEventListener('click', () => { const key=th.dataset.sort; if(state.sortKey===key) state.sortDir*=-1; else {state.sortKey=key; state.sortDir=1;} renderTable(); }));
$$('.tab').forEach(btn => btn.addEventListener('click', () => { $$('.tab').forEach(x=>x.classList.remove('active')); $$('.tab-panel').forEach(x=>x.classList.remove('active')); btn.classList.add('active'); document.getElementById(btn.dataset.target).classList.add('active'); }));

init();
