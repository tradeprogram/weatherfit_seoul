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

/* ───────────────────────── 지도 ───────────────────────── */

function webglOK() {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch (e) { return false; }
}

let map = null;
const mapAvailable = typeof maplibregl !== 'undefined' && webglOK();

if (!mapAvailable) {
  document.getElementById('map').innerHTML =
    '<div class="map-fallback"><b>지도를 표시할 수 없습니다</b>' +
    '<span>이 브라우저에서 WebGL을 사용할 수 없습니다. 코스·후보·근거는 왼쪽에서 그대로 확인할 수 있습니다.</span></div>';
}

if (mapAvailable) map = new maplibregl.Map({
  container:'map',
  style:{
    version:8,
    glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources:{
      carto:{
        type:'raster',
        tiles:['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
        tileSize:256,
        attribution:'&copy; OpenStreetMap contributors &copy; CARTO',
      },
    },
    layers:[{ id:'base', type:'raster', source:'carto' }],
  },
  center:[126.9780, 37.5665], zoom:11.2, maxZoom:17, minZoom:9,
});
if (map) {
  map.addControl(new maplibregl.NavigationControl({showCompass:false}), 'top-right');
  map.on('error', e => console.warn('map:', e && e.error && e.error.message));
}

const EMPTY = { type:'FeatureCollection', features:[] };
let mapReady = false;

if (map) map.on('load', async () => {
  // 행정동 경계
  try {
    const r = await fetch('data/seoul_dong.geojson');
    map.addSource('dong', { type:'geojson', data:await r.json() });
    map.addLayer({ id:'dong-fill', type:'fill', source:'dong',
      paint:{ 'fill-color':'#4ea8de', 'fill-opacity':0.045 } });
    map.addLayer({ id:'dong-line', type:'line', source:'dong',
      paint:{ 'line-color':'#4ea8de', 'line-opacity':0.28, 'line-width':0.6 } });
  } catch (e) {
    console.warn('행정동 경계를 불러오지 못했습니다', e);
  }

  map.addSource('cands', { type:'geojson', data:EMPTY });
  map.addLayer({ id:'cands', type:'circle', source:'cands',
    paint:{
      'circle-radius':['interpolate',['linear'],['zoom'],10,2.2,14,4,16,6],
      'circle-color':'#5a6a7d', 'circle-opacity':0.75,
      'circle-stroke-width':0.5, 'circle-stroke-color':'#0d1117',
    }});

  map.addSource('route', { type:'geojson', data:EMPTY });
  map.addLayer({ id:'route', type:'line', source:'route',
    paint:{ 'line-color':'#4ea8de', 'line-width':2, 'line-dasharray':[2,1.4],
            'line-opacity':0.7 }});

  map.addSource('steps', { type:'geojson', data:EMPTY });
  map.addLayer({ id:'steps-halo', type:'circle', source:'steps',
    paint:{ 'circle-radius':13, 'circle-color':['get','color'], 'circle-opacity':0.18 }});
  map.addLayer({ id:'steps', type:'circle', source:'steps',
    paint:{ 'circle-radius':7, 'circle-color':['get','color'],
            'circle-stroke-width':1.5, 'circle-stroke-color':'#0d1117' }});
  map.addLayer({ id:'steps-label', type:'symbol', source:'steps',
    layout:{ 'text-field':['get','n'], 'text-size':11,
             'text-font':['Open Sans Bold','Arial Unicode MS Bold'],
             'text-allow-overlap':true },
    paint:{ 'text-color':'#0d1117' }});

  for (const id of ['cands','steps']) {
    map.on('click', id, e => selectCid(e.features[0].properties.cid));
    map.on('mouseenter', id, () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', id, () => map.getCanvas().style.cursor = '');
  }

  mapReady = true;
  drawMap();          // 지도보다 데이터가 먼저 와 있었다면 이제 그린다
});

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
  if (!mapReady) return;              // 지도가 준비되면 load 핸들러가 다시 부른다
  const steps = (S.course && S.course.steps || []).filter(s => s.lat && s.lon);
  map.getSource('steps').setData({
    type:'FeatureCollection',
    features: steps.map((s, i) => ({
      type:'Feature',
      geometry:{ type:'Point', coordinates:[s.lon, s.lat] },
      properties:{ cid:s.cid, n:String(i + 1), color:ROLE_COLOR[s.role] || '#4ea8de' },
    })),
  });
  map.getSource('route').setData(steps.length > 1 ? {
    type:'Feature',
    geometry:{ type:'LineString', coordinates: steps.map(s => [s.lon, s.lat]) },
  } : EMPTY);

  const pts = S.showAll ? filtered() : [];
  map.getSource('cands').setData({
    type:'FeatureCollection',
    features: pts.filter(c => c.lat && c.lon).map(c => ({
      type:'Feature',
      geometry:{ type:'Point', coordinates:[c.lon, c.lat] },
      properties:{ cid:c.cid },
    })),
  });

  if (steps.length) {
    const b = new maplibregl.LngLatBounds();
    steps.forEach(s => b.extend([s.lon, s.lat]));
    map.fitBounds(b, { padding:110, maxZoom:15, duration:700 });
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
  if (map && merged.lat && merged.lon) {
    map.flyTo({ center:[merged.lon, merged.lat], zoom:Math.max(map.getZoom(), 14.5),
                duration:600 });
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
    if (!map) return;
    const on = e.target.classList.toggle('on');
    ['dong-fill', 'dong-line'].forEach(l => {
      if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', on ? 'visible' : 'none');
    });
  };
  $('#btn-all').onclick = e => {
    S.showAll = e.target.classList.toggle('on');
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
      if (c.weather) renderWeather({ ...c.weather, temp_c:0, pty:'없음', sky:'맑음',
                                     desc:c.weather.desc, source:c.weather.source,
                                     note:c.weather.note });
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
