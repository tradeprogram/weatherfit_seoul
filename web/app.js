/* 웨더핏 서울 — 프런트엔드 */
'use strict';

const API = '';                       // 같은 오리진에서 서빙
const ORIGINS = [
  ['서울시청',       37.5665, 126.9780],
  ['강남역',         37.4979, 127.0276],
  ['홍대입구역',     37.5570, 126.9245],
  ['성수역',         37.5445, 127.0557],
  ['명동',           37.5636, 126.9827],
  ['동대문역사문화공원', 37.5654, 127.0090],
  ['여의도',         37.5215, 126.9243],
  ['잠실역',         37.5133, 127.1000],
];
const ROLE_COLOR = { anchor:'#f4a259', food:'#5fd3a0', shelter:'#a78bfa' };

const S = {
  mode:'auto', at:null, lat:37.5665, lon:126.9780,
  course:null, candidates:[], stats:null,
  catFilter:null, selected:null, showAll:false,
};

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ───────────────────────── 지도 ─────────────────────────
   Leaflet을 쓴다. 필요한 건 래스터 타일·폴리곤·원형 마커·선뿐이고,
   WebGL이 없는 환경(일부 임베디드 브라우저)에서도 그대로 그려진다.
   Leaflet 좌표는 [위도, 경도] 순으로 GeoJSON과 반대다.            */

function showMapFallback(why) {
  const el = document.getElementById('map');
  if (!el || el.querySelector('.map-fallback')) return;
  const box = document.createElement('div');
  box.className = 'map-fallback';
  box.innerHTML = '<b>지도를 표시할 수 없습니다</b><span>' + why +
    ' 코스·후보·근거는 왼쪽에서 그대로 확인할 수 있습니다.</span>';
  el.appendChild(box);
}

let map = null, mapReady = false;
const layers = { dong:null, cands:null, route:null, steps:null };

try {
  map = L.map('map', { zoomControl:true, minZoom:9, maxZoom:16, attributionControl:true })
         .setView([37.5665, 126.9780], 11);
  // Esri Dark Gray Canvas — 키가 필요 없고 어두운 UI와 맞는다.
  // (CARTO 다크 타일은 이제 API 키를 요구해 워터마크가 찍힌다)
  const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas';
  L.tileLayer(ESRI + '/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution:'&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
    maxZoom:16, maxNativeZoom:16,
  }).addTo(map);
  // 지명 라벨은 별도 레이어다. 경계·마커 아래에 깔린다.
  L.tileLayer(ESRI + '/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
    maxZoom:16, maxNativeZoom:16, opacity:0.85,
  }).addTo(map);

  layers.cands = L.layerGroup();
  layers.route = L.layerGroup().addTo(map);
  layers.steps = L.layerGroup().addTo(map);
  mapReady = true;

  // 행정동 경계는 1MB라 지도가 뜬 다음에 얹는다
  fetch('data/seoul_dong.geojson')
    .then(r => r.json())
    .then(gj => {
      layers.dong = L.geoJSON(gj, {
        style:{ color:'#4ea8de', weight:0.6, opacity:0.30,
                fillColor:'#4ea8de', fillOpacity:0.05 },
        interactive:false,
      }).addTo(map);
      if (layers.dong.bringToBack) layers.dong.bringToBack();
    })
    .catch(e => console.warn('행정동 경계를 불러오지 못했습니다', e));

  drawMap();
} catch (e) {
  console.warn('지도를 만들지 못했습니다', e);
  map = null;
  showMapFallback('이 브라우저에서 지도를 표시할 수 없습니다.');
}

// 지도 타일이 막히거나 늦어도 목록·근거는 나와야 한다
let booted = false;
function bootOnce() { if (!booted) { booted = true; boot(); } }
document.addEventListener('DOMContentLoaded', bootOnce);
if (document.readyState !== 'loading') bootOnce();

/* ───────────────────────── 데이터 ───────────────────────── */

function qs(extra = {}) {
  const p = new URLSearchParams({ lat:S.lat, lon:S.lon, mode:S.mode, ...extra });
  if (S.at) p.set('at', S.at);
  return p.toString();
}

async function getJSON(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function boot() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  $('#at').value = now.toISOString().slice(0, 16);

  $('#origin').innerHTML = ORIGINS
    .map(([n, la, lo], i) => `<option value="${i}">${esc(n)}</option>`).join('');

  bindUI();
  await refresh();
}

async function refresh() {
  try {
    const [course, cands] = await Promise.all([
      getJSON('/api/course?' + qs({ explain:'true' })),
      getJSON('/api/candidates?' + qs({ limit:600 })),
    ]);
    S.course = course;
    S.candidates = cands.items;
    renderWeather(cands.weather);
    renderHeadStats(cands);
    renderCourse();
    renderCandidates();
    drawMap();
  } catch (e) {
    $('#course-notes').innerHTML =
      `<div>백엔드에 연결하지 못했습니다. <code>python -m weatherfit.server</code>가 실행 중인지 확인하세요. (${esc(e.message)})</div>`;
  }
  getJSON('/api/stats?' + qs()).then(s => { S.stats = s; renderEvidence(); })
    .catch(() => {});
}

/* ───────────────────────── 렌더 ───────────────────────── */

function renderWeather(w) {
  const icon = w.pty !== '없음' ? '🌧' : w.temp_c >= 33 ? '🔥'
             : w.sky === '흐림' ? '☁' : w.sky === '구름많음' ? '⛅' : '☀';
  $('#wx-icon').textContent = icon;
  $('#wx-desc').textContent = w.desc;
  const src = { kma:'기상청 초단기실황', fallback:'기본값', manual:'수동 지정' }[w.source] || w.source;
  $('#wx-src').textContent = `${src}${w.note ? ' · ' + w.note : ''}`;
}

function renderHeadStats(c) {
  const outdoorOff = !c.weather.outdoor_ok;
  $('#head-stats').innerHTML = `
    <div class="stat"><span class="v">${c.count.toLocaleString()}</span>
      <span class="k">지금 갈 수 있는 곳</span></div>
    <div class="stat"><span class="v">${outdoorOff ? '제외' : '가능'}</span>
      <span class="k">실외 활동</span></div>`;
}

function renderCourse() {
  const c = S.course;
  const list = $('#course-list'), notes = $('#course-notes');
  if (!c || !c.steps.length) {
    list.innerHTML = '';
    notes.innerHTML = `<div>${esc((c && c.notes[0]) || '조건에 맞는 코스를 찾지 못했습니다.')}</div>`;
    return;
  }
  list.innerHTML = c.steps.map(s => `
    <li data-role="${s.role}" data-cid="${esc(s.cid)}"
        class="${S.selected === s.cid ? 'sel' : ''}">
      <div class="t">${esc(s.title)}
        ${s.ends_today ? '<span class="badge today">오늘 마지막</span>' : ''}
        ${s.environment === 'indoor' ? '<span class="badge indoor">실내</span>' : ''}
      </div>
      <div class="m">${esc(s.category_path || s.category)}${
        s.walk_min ? ` · 도보 ${s.walk_min}분` : ''}</div>
      <div class="l">${esc(s.line)}</div>
    </li>`).join('');

  notes.innerHTML = (c.notes || []).map(n => `<div>${esc(n)}</div>`).join('') +
    (c.engine === 'rules' && c.steps.length
      ? `<div style="border-left-color:var(--line)">설명 문장은 규칙으로 생성했습니다. ANTHROPIC_API_KEY를 지정하면 LLM이 씁니다.</div>`
      : '');

  list.querySelectorAll('li').forEach(li =>
    li.onclick = () => selectCid(li.dataset.cid));
}

function renderCandidates() {
  const cats = [...new Set(S.candidates.map(c => c.category).filter(Boolean))];
  $('#cat-filters').innerHTML =
    [`<button data-cat="" class="${S.catFilter ? '' : 'on'}">전체</button>`]
    .concat(cats.map(c =>
      `<button data-cat="${esc(c)}" class="${S.catFilter === c ? 'on' : ''}">${esc(c)}</button>`))
    .join('');
  $$('#cat-filters button').forEach(b => b.onclick = () => {
    S.catFilter = b.dataset.cat || null;
    renderCandidates(); drawMap();
  });

  const rows = filtered();
  $('#list-summary').textContent =
    `판정을 통과한 ${rows.length.toLocaleString()}곳 · 가까운 순`;
  $('#cand-list').innerHTML = rows.slice(0, 150).map(c => `
    <li data-cid="${esc(c.cid)}" class="${S.selected === c.cid ? 'sel' : ''}">
      <div class="t">${esc(c.title)}</div>
      <div class="m">
        <span>${esc(c.category)}</span>
        <span class="n">도보 ${c.walk_min}분</span>
        <span>${c.environment === 'indoor' ? '실내' : c.environment === 'outdoor' ? '실외' : '실내외 불명'}</span>
      </div>
    </li>`).join('');
  $$('#cand-list li').forEach(li => li.onclick = () => selectCid(li.dataset.cid));
}

function filtered() {
  return S.catFilter
    ? S.candidates.filter(c => c.category === S.catFilter)
    : S.candidates;
}

function renderEvidence() {
  const s = S.stats;
  if (!s) return;
  const bar = (lab, n, total, cls = '') => {
    const pct = total ? (n / total * 100) : 0;
    return `<div class="ev-bar"><span class="lab">${esc(lab)}</span>
      <span class="track"><span class="fill ${cls}" style="width:${pct.toFixed(1)}%"></span></span>
      <span class="val">${pct.toFixed(1)}%</span></div>`;
  };
  const h = s.hours_confidence || {}, e = s.environment || {};
  const t = s.total || 1;
  const ended = s.dated.total ? (s.dated.ended / s.dated.total * 100) : 0;
  const dist = Object.entries(s.distribution || {}).slice(0, 8);
  const maxDist = dist.length ? dist[0][1] : 1;

  $('#evidence').innerHTML = `
    <div class="ev-block">
      <h3>운영시간, 규칙만으로 어디까지</h3>
      ${bar('확정 가능', h.high || 0, t, 'ok')}
      ${bar('가정·예외 섞임', h.low || 0, t)}
      ${bar('판정 불가', h.none || 0, t, 'warn')}
      <p class="ev-note">규칙만으로 확정되는 건 <b>${((h.high || 0) / t * 100).toFixed(1)}%</b>.
        나머지가 LLM 정규화가 담당할 몫입니다.</p>
    </div>
    <div class="ev-block">
      <h3>실내·실외 — API에 없는 필드</h3>
      ${bar('실내', e.indoor || 0, t, 'ok')}
      ${bar('실외', e.outdoor || 0, t)}
      ${bar('불명', e.unknown || 0, t, 'warn')}
      <p class="ev-note">날씨 대응의 전제가 되는 속성인데
        <b>${((e.unknown || 0) / t * 100).toFixed(1)}%</b>가 규칙으로 가려지지 않습니다.</p>
    </div>
    <div class="ev-block">
      <h3>기간이 있는 콘텐츠의 시의성</h3>
      ${bar('이미 종료', s.dated.ended, s.dated.total, 'warn')}
      <p class="ev-note">기간 지정 콘텐츠 ${s.dated.total.toLocaleString()}건 중
        <b>${s.dated.ended.toLocaleString()}건(${ended.toFixed(1)}%)</b>이 끝난 행사입니다.
        목록 조회 API에는 기간 필드가 없어 상세 조회 없이는 구분할 수 없습니다.</p>
    </div>
    <div class="ev-block">
      <h3>필터 통과</h3>
      ${bar('지금 가능', s.funnel.passed, t, 'ok')}
      <p class="ev-note">전체 ${t.toLocaleString()}건 →
        <b>${s.funnel.passed.toLocaleString()}건</b>.
        ${Object.entries(s.funnel.dropped).map(([k, v]) =>
          `${esc(k)} ${v.toLocaleString()}`).join(' · ')}</p>
    </div>
    ${dist.length ? `<div class="ev-block">
      <h3>자치구 분포 — 관광 분산 지표의 기준선</h3>
      ${dist.map(([gu, n]) => bar(gu, n, maxDist)).join('')}
      <p class="ev-note">추천 결과가 이 분포보다 고르게 퍼지면 분산 효과가 있다고 본다.</p>
    </div>` : ''}`;
}

function renderDetail(c) {
  if (!c) return;
  $('#detail-title').textContent = c.title;
  const vClass = c.verdict === '통과' ? '' : c.verdict === '판정불가' ? 'unknown' : 'fail';
  const envText = { indoor:'실내', outdoor:'실외', unknown:'실내외 불명' }[c.environment] || c.environment;
  const period = c.schedule_start
    ? `${esc(c.schedule_start)} ~ ${esc(c.schedule_end || '')}` : '';

  $('#detail').innerHTML = `
    <div class="verdict ${vClass}">
      <div><b>${esc(c.verdict || '통과')}</b>
        <small>${esc(c.reason || c.verdict_reason || '')}</small></div>
    </div>
    ${c.line ? `<div class="d-sec"><h4>왜 지금</h4><p>${esc(c.line)}</p></div>` : ''}
    ${c.summary ? `<div class="d-sec"><h4>요약</h4><p>${esc(c.summary)}</p></div>` : ''}
    <div class="d-sec"><h4>분류 · 환경</h4>
      <p>${esc(c.category_path || c.category)} · ${esc(envText)}</p></div>
    ${period ? `<div class="d-sec"><h4>기간</h4><p>${period}</p></div>` : ''}
    ${c.address ? `<div class="d-sec"><h4>주소</h4><p>${esc(c.address)}</p></div>` : ''}
    ${c.subway ? `<div class="d-sec"><h4>교통</h4><p>${esc(c.subway)}</p></div>` : ''}
    ${c.use_time ? `<div class="d-sec"><h4>이용시간 원문</h4>
      <p class="raw">${esc(c.use_time)}</p></div>` : ''}
    ${c.closed_days ? `<div class="d-sec"><h4>휴무일 원문</h4>
      <p class="raw">${esc(c.closed_days)}</p></div>` : ''}
    ${(c.accessibility && c.accessibility.length) ? `<div class="d-sec">
      <h4>무장애 시설</h4><div class="tag-row">${
        c.accessibility.map(a => `<span>${esc(a)}</span>`).join('')}</div></div>` : ''}
    ${(c.tags && c.tags.length) ? `<div class="d-sec"><h4>태그</h4>
      <div class="tag-row">${c.tags.map(t => `<span>#${esc(t)}</span>`).join('')}</div></div>` : ''}
    ${c.homepage ? `<div class="d-sec"><h4>홈페이지</h4>
      <a class="d-link" href="${esc(c.homepage)}" target="_blank" rel="noopener">${esc(c.homepage)}</a></div>` : ''}
    <div class="d-sec"><h4>운영시간 정규화</h4>
      <p>${esc({high:'확정 — 요일·시각 모두 명시', low:'가정 포함 — 예외 단서 또는 요일 누락',
                none:'판정 불가 — 시각 패턴 없음'}[c.hours_confidence] || '—')}</p></div>`;
}

/* ───────────────────────── 지도 그리기 ───────────────────────── */

function drawMap() {
  if (!mapReady) return;
  const steps = (S.course && S.course.steps || []).filter(s => s.lat && s.lon);

  layers.steps.clearLayers();
  layers.route.clearLayers();

  steps.forEach((s, i) => {
    const color = ROLE_COLOR[s.role] || '#4ea8de';
    L.circleMarker([s.lat, s.lon], {
      radius:16, color:color, weight:0, fillColor:color, fillOpacity:0.16,
      interactive:false,
    }).addTo(layers.steps);
    L.marker([s.lat, s.lon], {
      icon: L.divIcon({
        className:'step-pin',
        html:`<span style="background:${color}">${i + 1}</span>`,
        iconSize:[22, 22], iconAnchor:[11, 11],
      }),
      title: s.title,
    }).addTo(layers.steps).on('click', () => selectCid(s.cid));
  });

  if (steps.length > 1) {
    L.polyline(steps.map(s => [s.lat, s.lon]), {
      color:'#4ea8de', weight:2, opacity:0.7, dashArray:'6 5',
    }).addTo(layers.route);
  }

  layers.cands.clearLayers();
  if (S.showAll) {
    filtered().filter(c => c.lat && c.lon).forEach(c => {
      L.circleMarker([c.lat, c.lon], {
        radius:3.5, color:'#0d1117', weight:0.5,
        fillColor:'#5a6a7d', fillOpacity:0.8,
      }).addTo(layers.cands).on('click', () => selectCid(c.cid));
    });
  }

  if (steps.length) {
    const b = L.latLngBounds(steps.map(s => [s.lat, s.lon]));
    // 화면이 작으면 패딩이 지도보다 커져 fitBounds가 실패한다
    const el = map.getContainer();
    const pad = Math.max(12, Math.min(70, Math.floor(Math.min(el.clientWidth, el.clientHeight) / 6)));
    map.fitBounds(b, { padding:[pad, pad], maxZoom:15 });
  }
}

function selectCid(cid) {
  S.selected = cid;
  const inCourse = (S.course && S.course.steps || []).find(s => s.cid === cid);
  const inList = S.candidates.find(c => c.cid === cid);
  const merged = { ...(inList || {}), ...(inCourse || {}) };
  renderDetail(merged);
  $$('#course-list li, #cand-list li').forEach(li =>
    li.classList.toggle('sel', li.dataset.cid === cid));
  if (mapReady && merged.lat && merged.lon) {
    map.setView([merged.lat, merged.lon], Math.max(map.getZoom(), 15), { animate:true });
  }
}

/* ───────────────────────── UI 바인딩 ───────────────────────── */

function bindUI() {
  $$('#mode-seg button').forEach(b => b.onclick = () => {
    $$('#mode-seg button').forEach(x => x.classList.toggle('on', x === b));
    S.mode = b.dataset.mode;
    refresh();
  });

  $('#at').onchange = e => { S.at = e.target.value || null; refresh(); };

  $('#origin').onchange = e => {
    const [, la, lo] = ORIGINS[+e.target.value];
    S.lat = la; S.lon = lo; refresh();
  };

  $('#locate').onclick = () => {
    if (!navigator.geolocation) return alert('이 브라우저는 위치를 지원하지 않습니다.');
    navigator.geolocation.getCurrentPosition(
      p => { S.lat = p.coords.latitude; S.lon = p.coords.longitude; refresh(); },
      () => alert('위치를 가져오지 못했습니다. 목록에서 출발지를 선택해 주세요.'));
  };

  $$('#tabs button').forEach(b => b.onclick = () => {
    $$('#tabs button').forEach(x => x.classList.toggle('on', x === b));
    ['course', 'list', 'evidence'].forEach(t =>
      $('#pane-' + t).hidden = t !== b.dataset.tab);
  });

  $('#btn-dong').onclick = e => {
    const on = e.target.classList.toggle('on');
    if (!mapReady || !layers.dong) return;
    on ? layers.dong.addTo(map) : map.removeLayer(layers.dong);
    if (on && layers.dong.bringToBack) layers.dong.bringToBack();
  };
  $('#btn-all').onclick = e => {
    S.showAll = e.target.classList.toggle('on');
    if (mapReady) {
      S.showAll ? layers.cands.addTo(map) : map.removeLayer(layers.cands);
    }
    drawMap();
  };

  const send = async () => {
    const msg = $('#chat-input').value.trim();
    if (!msg) return;
    const btn = $('#chat-send');
    btn.disabled = true; btn.textContent = '생각 중';
    try {
      const r = await fetch(API + '/api/chat', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ message:msg, lat:S.lat, lon:S.lon,
                               mode:S.mode, at:S.at }),
      });
      const c = await r.json();
      S.course = c;
      const u = c.understood || {};
      $('#chat-understood').textContent =
        `이해한 조건 — ${u.area || '현재 위치'} · ` +
        `${{rain:'우천', heat:'폭염', clear:'맑음', auto:'실시간 날씨'}[u.weather_mode] || u.weather_mode}` +
        ` · 도보 ${u.max_walk_min}분 이내`;
      if (c.weather) renderWeather(c.weather);
      renderCourse(); drawMap();
    } catch (e) {
      $('#chat-understood').textContent = '요청을 처리하지 못했습니다: ' + e.message;
    } finally {
      btn.disabled = false; btn.textContent = '코스 짜기';
    }
  };
  $('#chat-send').onclick = send;
  $('#chat-input').onkeydown = e => { if (e.key === 'Enter') send(); };

  $('#help-btn').onclick = () => $('#help-overlay').hidden = false;
  $('#help-close').onclick = () => $('#help-overlay').hidden = true;
  $('#help-overlay').onclick = e => {
    if (e.target.id === 'help-overlay') $('#help-overlay').hidden = true;
  };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $('#help-overlay').hidden = true;
  });
}
