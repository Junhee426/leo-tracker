const fmt = new Intl.NumberFormat('ko-KR');
const $ = (s) => document.querySelector(s);
const escapeHtml = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const statusLabel = (s) => ({operating:'운영', operating_expanding:'운영·확장', deploying:'구축중', development:'개발'})[s] || s;

function trendBadge(item) {
  const label = item.trend === 'delayed' ? `+${item.delta_months ?? '?'} mo` : item.trend === 'expanded' ? 'SCOPE ↑' : item.trend === 'completed' ? 'DONE' : 'ON TRACK';
  return `<span class="trend ${escapeHtml(item.trend)}">${escapeHtml(label)}</span>`;
}

const METRIC_LABELS = { tracked: '카탈로그 추적', deployed: '배치 완료', authorized: '규제 승인', planned: '계획' };

function renderCrossCheck(points, sourcesById) {
  const target = $('#detailCrosscheck');
  if (!points || !points.length) {
    target.innerHTML = '<div class="empty">교차검증할 날짜 명시 수치 출처가 아직 없습니다.</div>';
    return;
  }
  const groups = {};
  points.forEach(p => { (groups[p.metric] ||= []).push(p); });
  target.innerHTML = Object.entries(groups).map(([metric, items]) => {
    const values = items.map(i => i.value).filter(v => v != null);
    let status = 'single', label = '단일 출처 · 교차검증 대기';
    if (values.length >= 2) {
      const max = Math.max(...values), min = Math.min(...values);
      const diffPct = max ? ((max - min) / max) * 100 : 0;
      const tolerance = Math.max(2, 5 / max * 100);
      if (diffPct <= tolerance) { status = 'match'; label = `일치 · 교차검증됨 (차이 ${diffPct.toFixed(1)}%)`; }
      else { status = 'conflict'; label = `수치 상이 (차이 ${diffPct.toFixed(1)}%) · 확인 필요`; }
    }
    const rows = items.slice().sort((a, b) => (b.date || '').localeCompare(a.date || '')).map(it => {
      const src = sourcesById[it.source_id];
      const val = it.value == null ? '—' : `${it.approx ? '~' : ''}${fmt.format(it.value)}`;
      const label2 = src ? `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.publisher)} ↗</a>` : escapeHtml(it.source_id);
      return `<div class="xcheck-row"><div class="xcheck-src">${label2}<small>${escapeHtml(it.date || '날짜 미상')}</small></div><div class="xcheck-val">${val}</div></div>`;
    }).join('');
    return `<article class="xcheck-group"><div class="xcheck-head"><h4>${escapeHtml(METRIC_LABELS[metric] || metric)}</h4><span class="xcheck-status ${status}">${escapeHtml(label)}</span></div>${rows}</article>`;
  }).join('');
}

async function init() {
  const id = document.body.dataset.constellationId;
  const res = await fetch(`/api/constellation/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error('Constellation not found');
  const data = await res.json();
  const r = data.constellation;
  document.title = `${r.name} · Global LEO Tracker`;
  $('#detailName').textContent = `${r.flag || ''} ${r.name}`;
  $('#detailOperator').textContent = `${r.operator} · ${r.country}`;
  $('#detailStatus').className = `status ${r.status}`;
  $('#detailStatus').textContent = statusLabel(r.status);
  $('#detailTracked').textContent = fmt.format(r.tracked_in_orbit || 0);
  $('#detailTrackedSource').textContent = r.tracked_source || '—';
  $('#detailPlanned').textContent = r.planned_satellites ? fmt.format(r.planned_satellites) : '—';
  $('#detailPlannedLabel').textContent = r.planned_label || '—';
  $('#detailPct').textContent = r.deployment_pct == null ? '—' : `${r.deployment_pct.toFixed(1)}%`;
  $('#detailDataDate').textContent = `DATA ${r.last_data_date || '—'}`;
  $('#detailLaunchCount').textContent = fmt.format(data.launches.length);
  $('#detailOrbit').textContent = r.orbit_label || '—';
  $('#detailMilestone').textContent = r.next_milestone || '—';
  $('#detailService').textContent = r.target_service || '—';
  $('#detailCountry').textContent = r.country || '—';
  $('#detailNote').textContent = r.note || '—';

  const sourcesById = Object.fromEntries(data.sources.map(s => [s.id, s]));
  renderCrossCheck(r.crosscheck_points, sourcesById);

  $('#detailRoadmap').innerHTML = data.roadmap.map(x => `<div class="mini-roadmap-item"><div><strong>${escapeHtml(x.milestone)}</strong><small>${escapeHtml(x.category)}</small></div>${trendBadge(x)}<div class="mini-compare"><span>${escapeHtml(x.baseline)}</span><b>→</b><span>${escapeHtml(x.current)}</span></div></div>`).join('') || '<div class="empty">비교 가능한 공개 기준선이 아직 없습니다.</div>';
  $('#detailLaunches').innerHTML = data.launches.sort((a,b)=>b.date.localeCompare(a.date)).map(x => `<tr><td>${escapeHtml(x.date)}</td><td><strong>${escapeHtml(x.mission)}</strong><br><small>${escapeHtml(x.note || '')}</small></td><td><span class="mission-status ${escapeHtml(x.status)}">${escapeHtml(x.status)}</span></td><td>${escapeHtml(x.vehicle || '—')}</td><td class="num">${x.satellites == null ? '—' : fmt.format(x.satellites)}</td><td>${escapeHtml(x.site || '—')}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">임무 단위 발사기록이 아직 등록되지 않았습니다.</td></tr>';
  $('#detailChanges').innerHTML = data.changes.map(x => `<div class="stack-item"><small>${escapeHtml(x.date)} · ${escapeHtml(x.type)}</small><strong>${escapeHtml(x.field)}</strong><p>${escapeHtml(x.previous)} → ${escapeHtml(x.current)}</p></div>`).join('') || '<div class="empty">변경 이력이 없습니다.</div>';
  $('#detailSources').innerHTML = data.sources.map(x => `<div class="stack-item"><small>${escapeHtml(x.type)} · ${escapeHtml(x.date)}</small><strong>${escapeHtml(x.publisher)}</strong><p>${escapeHtml(x.title)}</p><a href="${escapeHtml(x.url)}" target="_blank" rel="noopener">원문 보기 ↗</a></div>`).join('') || '<div class="empty">등록된 출처가 없습니다.</div>';
}

init().catch(err => { console.error(err); $('#detailName').textContent = 'Data load error'; });
