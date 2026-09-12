/* Shared rendering helpers. All numeric comparisons are computed on the server. */
window.LEO = (() => {
  const fmt = new Intl.NumberFormat('ko-KR');
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const number = value => value == null ? '—' : fmt.format(value);
  const statusLabel = value => ({operating:'운영',operating_expanding:'운영·확장',deploying:'구축중',development:'개발'})[value] || value;
  const scopeLabel = value => ({all_catalogued:'전체 카탈로그',reported_fleet:'발표 대상 위성군',gen2:'Gen2',gen1_nominal:'Gen1 명목 배치',initial_constellation:'초기 위성망',production_network:'생산 위성망',long_term:'장기 목표',leo_meo_total:'LEO·MEO 전체',unspecified:'대상 범위 미확인'})[value] || value || '대상 범위 미확인';
  const qualityLabel = value => ({fresh:'수집 성공',cached:'저장값 사용',legacy:'기존 집계',stale:'이전 값 유지',unavailable:'관측값 없음',manual:'자동 수집 대상 아님'})[value] || value;
  const dateTime = value => value ? new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '—';
  const safeUrl = value => { try { const url=new URL(value); return ['https:','http:'].includes(url.protocol)?esc(url.href):'#'; } catch { return '#'; } };
  async function fetchJSON(url) {
    const controller = new AbortController();
    const timer = setTimeout(()=>controller.abort(),15000);
    try {
      const response = await fetch(url,{signal:controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function errorBox(target, retry, message='이 영역의 데이터를 불러오지 못했습니다.') {
    if (!target) return;
    target.innerHTML=`<div class="load-error" role="alert">${esc(message)} <button type="button">다시 시도</button></div>`;
    target.querySelector('button').onclick=retry;
  }
  function sourceLink(id,sources) {
    const source=sources[id];
    return source?`<a href="${safeUrl(source.url)}" target="_blank" rel="noopener">${esc(source.publisher)} ↗</a>`:esc(id||'—');
  }
  function trendBadge(item) {
    const label=item.trend==='delayed'?`약 +${item.delta_months??'?'}개월`:item.trend==='expanded'?'규모 확대':item.trend==='completed'?'완료':'계획 유지';
    return `<span class="trend ${esc(item.trend)}">${esc(label)}</span>`;
  }
  function crosschecks(check,sources) {
    return (check?.groups||[]).map(g=>`<article class="xcheck-group"><div class="xcheck-head"><h4>${esc(g.metric_label)} <small>${esc(scopeLabel(g.scope))}</small></h4><span class="xcheck-status ${esc(g.status)}">${esc(g.label)}</span></div>${g.points.map(p=>`<div class="xcheck-row"><div class="xcheck-src">${sourceLink(p.source_id,sources)}<small>${esc(p.date||'기준일 미확인')}</small></div><div class="xcheck-val">${({approx:'약 ',lower_bound:'≥ ',upper_bound:'≤ ',lower_exclusive:'> ',upper_exclusive:'< '})[p.qualifier]||''}${number(p.value)}</div></div>`).join('')}</article>`).join('')||'<div class="empty">비교 가능한 출처가 아직 없습니다.</div>';
  }
  function lineChart(points) {
    if (!points.length) return '<div class="empty">표시할 관측 스냅샷이 없습니다.</div>';
    const width=900,height=250,pad=55;
    const times=points.map(p=>Date.parse(p.date+'T00:00:00Z'));
    const low=Math.min(...points.map(p=>p.value)),high=Math.max(...points.map(p=>p.value));
    const span=Math.max(1,high-low),margin=Math.max(1,span*.1),min=low-margin,max=high+margin;
    const x=t=>pad+(t-times[0])/Math.max(86400000,times.at(-1)-times[0])*(width-pad*2);
    const y=v=>height-pad-(v-min)/(max-min)*(height-pad*2);
    let path='';
    points.forEach((p,i)=>{path+=`${!i||times[i]-times[i-1]>1.5*86400000?'M':'L'}${x(times[i]).toFixed(2)},${y(p.value).toFixed(2)} `;});
    const ticks=[min,(min+max)/2,max].map(v=>`<line x1="${pad}" y1="${y(v)}" x2="${width-pad}" y2="${y(v)}" class="chart-grid"/><text x="${pad-8}" y="${y(v)+4}" text-anchor="end">${number(Math.round(v))}</text>`).join('');
    return `<svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="카탈로그 추적 수. ${esc(points[0].date)} ${number(points[0].value)}기부터 ${esc(points.at(-1).date)} ${number(points.at(-1).value)}기까지. 날짜 공백은 선을 끊어 표시합니다.">${ticks}<path d="${path}" class="chart-line"/>${points.map((p,i)=>`<circle cx="${x(times[i])}" cy="${y(p.value)}" r="3.5"><title>${esc(p.date)} · ${number(p.value)}기</title></circle>`).join('')}<text x="${pad}" y="${height-15}">${esc(points[0].date)}</text><text x="${width-pad}" y="${height-15}" text-anchor="end">${esc(points.at(-1).date)}</text></svg>`;
  }
  function multiLineChart(seriesList) {
    const active=(seriesList||[]).filter(s=>s.points&&s.points.length);
    if (!active.length) return '<div class="empty">비교할 관측 스냅샷이 없습니다.</div>';
    const width=900,height=280,pad=55;
    const allPoints=active.flatMap(s=>s.points);
    const times=allPoints.map(p=>Date.parse(p.date+'T00:00:00Z'));
    const t0=Math.min(...times),t1=Math.max(...times);
    const values=allPoints.map(p=>p.value);
    const low=Math.min(...values),high=Math.max(...values);
    const span=Math.max(1,high-low),margin=Math.max(1,span*.1),min=low-margin,max=high+margin;
    const x=t=>pad+(t-t0)/Math.max(86400000,t1-t0)*(width-pad*2);
    const y=v=>height-pad-(v-min)/(max-min)*(height-pad*2);
    const palette=['#2f6fed','#e0663e','#3fae6a','#a35fd0','#d0a92f','#3fb8c9','#c94f7c','#6b7a8f'];
    const ticks=[min,(min+max)/2,max].map(v=>`<line x1="${pad}" y1="${y(v)}" x2="${width-pad}" y2="${y(v)}" class="chart-grid"/><text x="${pad-8}" y="${y(v)+4}" text-anchor="end">${number(Math.round(v))}</text>`).join('');
    const paths=active.map((s,i)=>{
      const pointTimes=s.points.map(p=>Date.parse(p.date+'T00:00:00Z'));
      let path='';
      s.points.forEach((p,j)=>{path+=`${!j||pointTimes[j]-pointTimes[j-1]>1.5*86400000?'M':'L'}${x(pointTimes[j]).toFixed(2)},${y(p.value).toFixed(2)} `;});
      const color=palette[i%palette.length];
      const last=s.points.at(-1);
      return `<path d="${path}" fill="none" stroke="${color}" stroke-width="2"><title>${esc(s.constellation)}</title></path><circle cx="${x(pointTimes.at(-1))}" cy="${y(last.value)}" r="3.5" style="fill:${color}"><title>${esc(s.constellation)} · ${esc(last.date)} · ${number(last.value)}기</title></circle>`;
    }).join('');
    const legend=active.map((s,i)=>`<span class="legend-item"><i style="background:${palette[i%palette.length]}"></i>${esc(s.constellation)}</span>`).join('');
    return `<svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="위성망별 카탈로그 추적 수 비교">${ticks}${paths}</svg><div class="chart-legend">${legend}</div>`;
  }
  function histogramChart(bins, rangeKey, unit) {
    if (!bins || !bins.length) return '<div class="empty">표시할 분포 데이터가 없습니다.</div>';
    const max = Math.max(...bins.map(b=>b.count));
    return `<div class="histogram">${bins.map(b=>{
      const [lo,hi]=b[rangeKey];
      const pct=max?Math.round(b.count/max*100):0;
      return `<div class="histogram-row"><span class="histogram-label">${number(lo)}–${number(hi)}${unit}</span><span class="histogram-bar-track"><span class="histogram-bar" style="width:${pct}%"></span></span><span class="histogram-count">${number(b.count)}</span></div>`;
    }).join('')}</div>`;
  }
  function monthlyTable(rows) {
    if (!rows.length) return '<div class="empty">월별 비교에는 관측값이 필요합니다.</div>';
    return `<div class="table-wrap"><table><thead><tr><th>월</th><th>비교 기간 (UTC)</th><th class="num">기준 수</th><th class="num">마지막 수</th><th class="num">추적 순증감</th><th>수집 범위</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.month)}</td><td>${esc(r.baseline_date)} → ${esc(r.end_date)}</td><td class="num">${number(r.start_count)}</td><td class="num">${number(r.end_count)}</td><td class="num ${r.net_change>0?'positive':r.net_change<0?'negative':''}">${r.net_change>0?'+':''}${number(r.net_change)}</td><td>${r.complete_month?'완전한 월':'일부 기간'} · ${r.observed_days}/${r.expected_days}일</td></tr>`).join('')}</tbody></table></div>`;
  }
  function activityTable(rows) {
    if (!rows.length) return '<p class="empty">비교할 위성망이 없습니다.</p>';
    return `<div class="table-wrap"><table><thead><tr><th>위성망</th><th>실제 비교 기간 (UTC)</th><th class="num">첫 관측 → 마지막 관측</th><th class="num">순증감</th><th class="num">변화율</th><th>관측 범위</th></tr></thead><tbody>${rows.map(r=>`<tr><td><a href="/constellation/${encodeURIComponent(r.constellation_id)}">${esc(r.constellation)}</a></td><td>${esc(r.baseline_date||'—')} → ${esc(r.end_date||'—')}</td><td class="num">${number(r.start_count)} → ${number(r.end_count)}</td><td class="num ${r.net_change>0?'positive':r.net_change<0?'negative':''}">${r.net_change>0?'+':''}${number(r.net_change)}</td><td class="num">${r.change_pct>0?'+':''}${number(r.change_pct)}${r.change_pct==null?'':'%'}</td><td>${r.coverage==='complete'?'전체 기간':'일부 기간'} · ${number(r.observed_days)}/${number(r.expected_days)}일<br><small>관측 공백 ${number(r.missing_days)}일${r.comparison_status==='scope_changed'?' · 집계 범위 변경으로 비교 보류':r.comparison_status==='insufficient'?' · 비교 관측 부족':''}</small></td></tr>`).join('')}</tbody></table></div>`;
  }
  return {esc,number,statusLabel,scopeLabel,qualityLabel,dateTime,safeUrl,fetchJSON,errorBox,sourceLink,trendBadge,crosschecks,lineChart,multiLineChart,histogramChart,monthlyTable,activityTable};
})();
