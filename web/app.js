/* 웨더핏 서울 — 프런트엔드
   위치에서 시작해 시간표가 있는 일정으로 끝난다. */
'use strict';

const CITY_HALL = [37.5665, 126.9780];
const ROLE_COLOR = { anchor:'#f08a34', food:'#22a06b', spot:'#1f7ac4', shelter:'#7c62d8' };
const ROLE_NAME  = { anchor:'앵커', food:'식사·카페', spot:'둘러보기', shelter:'플랜 B' };
const LS_KEY = 'weatherfit.origin';
const LS_TASTE = 'weatherfit.taste';
const LS_LANG = 'weatherfit.lang';
const LS_VAULT = 'weatherfit.vault';

const S = {
  lat:CITY_HALL[0], lon:CITY_HALL[1], accuracy:null, where:null, precise:false,
  mode:'auto', hours:4, at:null,
  course:null, candidates:[], stats:null, area:null, quiet:false,
  catFilter:null, selected:null, showAll:false,
  mapMode:'plain', thermal:null, dongGeo:null, styles:[],
  history:[], intent:null, busy:false,
  interests:[], taste:null, lang:'ko', langsReady:['ko'], exclude:[],
};

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtMin = m => m >= 60 ? `${Math.floor(m/60)}시간 ${m%60 ? m%60+'분' : ''}`.trim()
                            : `${m}분`;

/* ───────────────────────── 지도 ───────────────────────── */

function showMapFallback(why) {
  const el = $('#map');
  if (!el || el.querySelector('.map-fallback')) return;
  const box = document.createElement('div');
  box.className = 'map-fallback';
  box.innerHTML = `<b>지도를 표시할 수 없습니다</b><span>${why}
    일정과 주변 목록은 왼쪽에서 그대로 확인할 수 있습니다.</span>`;
  el.appendChild(box);
}

let map = null, mapReady = false;
const layers = { dong:null, cands:null, route:null, steps:null, me:null };

try {
  map = L.map('map', { zoomControl:true, minZoom:10, maxZoom:18 })
         .setView(CITY_HALL, 12);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution:'&copy; OpenStreetMap contributors', maxZoom:19, className:'basemap',
  }).addTo(map);

  layers.cands = L.layerGroup();
  layers.route = L.layerGroup().addTo(map);
  layers.steps = L.layerGroup().addTo(map);
  layers.me    = L.layerGroup().addTo(map);
  mapReady = true;

  fetch('data/seoul_dong.geojson').then(r => r.json()).then(gj => {
    S.dongGeo = gj;
    layers.dong = L.geoJSON(gj, { style:dongStyle, interactive:false }).addTo(map);
    layers.dong.bringToBack();
    /* 경계는 비동기로 온다. 그 사이에 사용자가 이미 지도 모드를 골랐으면
       레이어가 기본 스타일로 붙어 아무것도 안 칠해진 채 남는다 — 실제로
       빈 지도가 나왔다. 도착한 뒤 지금 모드를 한 번 다시 입힌다. */
    if (S.mapMode && S.mapMode !== 'plain') setMapMode(S.mapMode);
  }).catch(e => console.warn('행정동 경계를 불러오지 못했습니다', e));
} catch (e) {
  console.warn('지도를 만들지 못했습니다', e);
  map = null;
  showMapFallback('이 브라우저에서 지도를 표시할 수 없습니다.');
}

/* ───────────────── 지표면온도 열지도 ─────────────────
   위성이 준 값을 판정에만 쓰고 화면에 안 보여 주면, 왜 이 코스가
   나왔는지 알 길이 없다. 지도 모드를 둘로 나눠 눈으로 확인하게 한다.

   순차형(sequential) 인코딩이라 색은 하나여야 한다 — 무지개를 쓰면
   중간 단계의 순서가 사라진다. 파랑은 '차갑다'로 읽히므로 주황을
   밝은 쪽에서 어두운 쪽으로 6단계. 검증기 통과값이다(단일 색상,
   단조 명도, 단계 간격 0.06 이상, 표면 대비 2.03:1). */

const LST_RAMP = ['#e29a64','#d47a33','#bb5c1d','#9c4514','#77330e','#522109'];

/* 동네 모멘텀 — 발산 램프. 0을 가운데 두고 오르는 쪽과 내리는 쪽을
   반대 색으로 가른다. 순차 램프를 쓰면 '안 변한 곳'과 '줄어든 곳'이
   같은 끝에 몰려 구분이 안 된다. */
/* 양쪽 모두 **옅은 쪽이 앞**이어야 한다. 처음에 하락 쪽만 진한 색을
   앞에 뒀더니, 거의 안 변한 동네까지 최고 농도로 칠해져 208곳 중 181곳이
   같은 진한 갈색이 됐다. 서울 전체가 무너지는 것처럼 보였다. */
const AREA_RAMP = [['#d8c4bd','#b08575','#8c5a4a'],
                   ['#c9dfd6','#7bbfa8','#2f8f74']];

function areaColor(m) {
  const a = Math.min(Math.abs(m), 0.6) / 0.6;
  const side = AREA_RAMP[m >= 0 ? 1 : 0];
  return side[Math.min(Math.floor(a * side.length), side.length - 1)];
}

/* 등간격으로 나누면 서울 대부분이 맨 위 칸에 몰려 지도가 통째로 진해진다.
   실제 분포가 더운 쪽으로 치우쳐 있기 때문이다. 그래서 분위(quantile)로
   나눠 칸마다 행정동이 비슷하게 들어가게 한다 — 공간 구조가 그때 보인다.
   대신 범례에 각 칸의 실제 온도 경계를 적어 크기 정보를 잃지 않는다. */
function lstColor(c) {
  const t = S.thermal;
  if (c == null || !t) return null;
  const b = t.breaks;
  let i = 0;
  while (i < b.length && c >= b[i]) i++;
  return LST_RAMP[Math.min(i, LST_RAMP.length - 1)];
}

function quantiles(vals, n) {
  const v = [...vals].sort((a, b) => a - b);
  return Array.from({ length: n - 1 },
    (_, i) => v[Math.floor(v.length * (i + 1) / n)]);
}

function dongStyle(f) {
  const base = { color:'#1f7ac4', weight:0.6, opacity:0.24,
                 fillColor:'#1f7ac4', fillOpacity:0.03 };
  const cd = f.properties.adm_cd;

  if (S.mapMode === 'area' && S.area) {
    const row = S.area.dong[cd];
    if (!row) return { color:'#fff', weight:0.5, opacity:0.3,
                       fillColor:'#c9cdd2', fillOpacity:0.22 };
    /* 조용히 뜨는 곳은 테두리를 굵게 준다. 색만으로는 '많이 뜬 곳'과
       '뜨는데 아직 안 붐비는 곳'이 구분되지 않는데, 뒤가 제품이다. */
    return { color: row.quiet ? '#1c6b56' : '#fff',
             weight: row.quiet ? 2.0 : 0.5,
             opacity: row.quiet ? 0.95 : 0.5,
             fillColor: areaColor(row.momentum), fillOpacity:0.72 };
  }
  if (S.mapMode !== 'lst' || !S.thermal) return base;
  const row = S.thermal.dong[cd];
  const col = row ? lstColor(row.lst_c) : null;
  return col
    ? { color:'#fff', weight:0.5, opacity:0.5, fillColor:col, fillOpacity:0.78 }
    : { color:'#fff', weight:0.5, opacity:0.35, fillColor:'#c9cdd2', fillOpacity:0.3 };
}

async function setMapMode(mode) {
  S.mapMode = mode;
  $$('.seg-map button').forEach(b => {
    const on = b.dataset.map === mode;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  $('#legend-plan').hidden = mode !== 'plain';
  $('#legend-lst').hidden = mode !== 'lst';
  $('#legend-area').hidden = mode !== 'area';
  document.body.classList.toggle('lst-on', mode === 'lst');

  if (mode === 'area' && !S.area) {
    try {
      S.area = await getJSON('/api/area');
      if (!S.area.meta.dong) throw new Error('동네 자료가 비어 있습니다');
      renderAreaLegend();
    } catch (e) {
      toast('동네 모멘텀 자료를 불러오지 못했습니다');
      return setMapMode('plain');
    }
  }

  if (mode === 'lst' && !S.thermal) {
    try {
      const d = await getJSON('/api/thermal');
      const vals = Object.values(d.dong).map(r => r.lst_c);
      if (!vals.length) throw new Error('열지도 데이터가 비어 있습니다');
      S.thermal = { dong:d.dong, meta:d.meta,
                    range:{ lo:Math.min(...vals), hi:Math.max(...vals) },
                    breaks:quantiles(vals, LST_RAMP.length) };
      renderLstLegend();
    } catch (e) {
      toast('지표면온도 자료를 불러오지 못했습니다');
      return setMapMode('plain');
    }
  }
  if (layers.dong) {
    layers.dong.setStyle(dongStyle);
    layers.dong.eachLayer(l => {
      l.options.interactive = mode !== 'plain';
      l.unbindTooltip();
      if (mode === 'lst') bindLstTip(l);
      else if (mode === 'area') bindAreaTip(l);
    });
  }
}

/* 동네에 마우스를 올리면 무엇으로 그렇게 칠했는지 말한다. */
function bindAreaTip(layer) {
  const row = S.area && S.area.dong[layer.feature.properties.adm_cd];
  if (!row) return;
  const pct = (row.momentum * 100).toFixed(1);
  layer.bindTooltip(
    `<b>${esc(row.name)}</b><br>작년 같은 달 대비 ${pct > 0 ? '+' : ''}${pct}%` +
    `<br><span class="tip-sub">낮 시간대 외국인 연 ${
      Math.round(row.level).toLocaleString()}명·시` +
    (row.quiet ? ' · <b>아직 조용함</b>' : '') + '</span>',
    { sticky:true, className:'lst-tip' });
}

function renderAreaLegend() {
  const m = S.area.meta;
  $('#area-ramp').innerHTML =
    [...AREA_RAMP[0]].reverse().concat(AREA_RAMP[1])
      .map(c => `<i style="background:${c}"></i>`).join('');
  $('#area-note').textContent =
    `${m.dong}개 행정동 · 43개월 · ${m.source}`;
  $('#area-quiet').textContent = `${m.quiet}개`;
}

function bindLstTip(layer) {
  const row = S.thermal.dong[layer.feature.properties.adm_cd];
  const p = layer.feature.properties;
  if (!row) return layer.bindTooltip(`${p.gu} ${p.dong}<br>자료 없음`, { sticky:true });
  // 상위 0%는 말이 안 된다. 양 끝은 말로 적는다.
  const rank = row.lst_pct >= 99 ? '서울에서 가장 더운 축'
             : row.lst_pct <= 2  ? '서울에서 가장 시원한 축'
             : `서울 상위 ${100 - row.lst_pct}%`;
  layer.bindTooltip(
    `<b>${esc(p.gu)} ${esc(p.dong)}</b><br>지표면온도 ${row.lst_c}°C` +
    ` <span class="tt-dim">(${rank})</span><br>` +
    `식생지수 ${row.ndvi ?? '—'}`, { sticky:true, className:'lst-tip' });
}

function renderLstLegend() {
  const { lo, hi } = S.thermal.range;
  const b = S.thermal.breaks;
  const edges = [lo, ...b, hi];
  $('#lst-ramp').innerHTML = LST_RAMP.map((c, i) =>
    `<span style="background:${c}" title="${edges[i].toFixed(1)}~${edges[i + 1].toFixed(1)}°C"></span>`
  ).join('');
  $('#lst-lo').textContent = `${lo.toFixed(0)}°C`;
  $('#lst-hi').textContent = `${hi.toFixed(0)}°C`;
  $('#lst-mid').textContent = b.map(v => v.toFixed(0)).join(' · ') + '°C';
  const m = S.thermal.meta || {};
  $('#lst-src').textContent =
    `Landsat 8/9 · Sentinel-2 여름 한낮 합성 · 행정동 ${m.dong || 426}개 · ` +
    `칸마다 ${Math.round(426 / LST_RAMP.length)}곳씩(분위 구간)`;
}

/* ───────────────────────── 통신 ───────────────────────── */

async function getJSON(path, params = {}) {
  const p = new URLSearchParams(params);
  const r = await fetch(p.toString() ? `${path}?${p}` : path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

const baseParams = () => {
  const o = { lat:S.lat, lon:S.lon, mode:S.mode, lang:S.lang };
  if (S.at) o.at = S.at;
  return o;
};

/* ───────────────────────── 위치 ───────────────────────── */

function setOrigin(lat, lon, { precise = false, accuracy = null } = {}) {
  S.lat = lat; S.lon = lon; S.precise = precise; S.accuracy = accuracy;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ lat, lon, precise }));
  } catch (e) { /* 사생활 모드 등 — 무시 */ }
  drawMe();
}

function drawMe() {
  if (!mapReady) return;
  layers.me.clearLayers();
  if (S.accuracy && S.accuracy < 3000) {
    L.circle([S.lat, S.lon], {
      radius:S.accuracy, color:'#1f7ac4', weight:1, opacity:.35,
      fillColor:'#1f7ac4', fillOpacity:.08, interactive:false,
    }).addTo(layers.me);
  }
  L.marker([S.lat, S.lon], {
    icon:L.divIcon({ className:'me-pin', html:'<span></span>',
                     iconSize:[18,18], iconAnchor:[9,9] }),
    title:S.precise ? '내 위치' : '출발 위치', zIndexOffset:500,
  }).addTo(layers.me);
}

function locate({ silent = false } = {}) {
  return new Promise(resolve => {
    if (!navigator.geolocation) {
      if (!silent) geoNote('이 브라우저는 위치를 지원하지 않습니다. 서울시청에서 시작합니다.');
      return resolve(false);
    }
    geoNote('위치를 확인하고 있습니다…');
    navigator.geolocation.getCurrentPosition(
      pos => {
        setOrigin(pos.coords.latitude, pos.coords.longitude,
                  { precise:true, accuracy:pos.coords.accuracy });
        resolve(true);
      },
      err => {
        const msg = {
          1:'위치 권한이 거부되었습니다. 브라우저 주소창의 자물쇠에서 허용할 수 있습니다.',
          2:'위치를 확인할 수 없습니다. 실내에서는 신호가 약할 수 있습니다.',
          3:'위치 확인이 오래 걸립니다.',
        }[err.code] || '위치를 가져오지 못했습니다.';
        if (!silent) geoNote(msg + ' 서울시청 기준으로 보여 드립니다.');
        resolve(false);
      },
      { enableHighAccuracy:true, timeout:9000, maximumAge:60000 });
  });
}

function geoNote(text) {
  const el = $('#geo-note');
  if (el) el.textContent = text;
}

async function refreshWhere() {
  try {
    const w = await getJSON('/api/where', { lat:S.lat, lon:S.lon });
    S.where = w;
    $('#where-label').textContent = w.label;
    $('#where-sub').textContent = w.in_seoul
      ? `${S.precise ? '내 위치' : '기준 위치'} · 주변 ${w.nearby.toLocaleString()}곳`
      : '서울 밖입니다. 서울 기준으로 안내합니다.';
    if (!w.in_seoul) {
      setOrigin(CITY_HALL[0], CITY_HALL[1]);
      const w2 = await getJSON('/api/where', { lat:S.lat, lon:S.lon });
      S.where = w2;
      $('#where-label').textContent = w2.label;
      $('#where-sub').textContent = `서울 밖이라 서울시청 기준 · 주변 ${w2.nearby}곳`;
    }
  } catch (e) {
    $('#where-label').textContent = '서울';
    $('#where-sub').textContent = '';
  }
}

/* ───────────────────────── 화면 갱신 ───────────────────────── */

async function postJSON(path, body) {
  const r = await fetch(path, { method:'POST',
    headers:{ 'Content-Type':'application/json' }, body:JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

const LS_STYLES = 'weatherfit.styles';

function saveStyles() {
  try { localStorage.setItem(LS_STYLES, JSON.stringify(S.styles)); }
  catch (e) { /* 무시 */ }
}

function loadStyles() {
  try { S.styles = JSON.parse(localStorage.getItem(LS_STYLES) || '[]'); }
  catch (e) { S.styles = []; }
}

function syncControls() {
  $$('#hours-seg button').forEach(b =>
    b.classList.toggle('on', +b.dataset.h === S.hours));
  $$('#mode-seg button').forEach(b =>
    b.classList.toggle('on', b.dataset.mode === S.mode));
  $$('#interests button').forEach(b =>
    b.classList.toggle('on', S.interests.includes(b.dataset.i)));
  $$('#styles button').forEach(b =>
    b.classList.toggle('on', S.styles.includes(b.dataset.s)));
}

async function refresh() {
  setLoading(true);
  try {
    const [course, cands] = await Promise.all([
      postJSON('/api/plan', { lat:S.lat, lon:S.lon, mode:S.mode, at:S.at,
                              hours:S.hours, interests:S.interests,
                              styles:S.styles, taste:S.taste,
                              lang:S.lang, exclude:S.exclude }),
      getJSON('/api/candidates', { ...baseParams(), radius_m:2500, limit:200 }),
    ]);
    S.course = course;
    S.candidates = cands.items;
    renderWeather(cands.weather);
    renderHeadStats(cands);
    renderPlan();
    renderCandidates();
    renderTaste();
    drawMap();
    sweepKorean();
  } catch (e) {
    $('#plan-notes').innerHTML =
      `<div>서버에 연결하지 못했습니다. (${esc(e.message)})</div>`;
  } finally {
    setLoading(false);
  }
  getJSON('/api/stats', { mode:S.mode, ...(S.at ? { at:S.at } : {}) })
    .then(s => { S.stats = s; renderEvidence(); sweepKorean($('#evidence')); })
    .catch(() => {});
}

function setLoading(on) {
  $('#pane-plan').classList.toggle('loading', on);
}

/* ───────────────────────── 취향 ───────────────────────── */

function loadTaste() {
  try { S.taste = JSON.parse(localStorage.getItem(LS_TASTE) || 'null'); }
  catch (e) { S.taste = null; }
}

function saveTaste() {
  try { localStorage.setItem(LS_TASTE, JSON.stringify(S.taste || {})); }
  catch (e) { /* 무시 */ }
}

function renderTaste() {
  const bar = $('#taste-bar');
  const txt = (S.course && S.course.taste) || tasteSummary();
  if (txt) { $('#taste-txt').textContent = '내 취향 · ' + txt; bar.hidden = false; }
  else bar.hidden = true;
}

function tasteSummary() {
  const t = S.taste;
  if (!t) return '';
  const cats = Object.entries(t.categories || {})
    .filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 2).map(x => x[0]);
  const tags = Object.entries(t.tags || {})
    .filter(([, v]) => v > 0.4).sort((a, b) => b[1] - a[1]).slice(0, 3).map(x => x[0]);
  const bits = [];
  if (cats.length) bits.push(cats.join(' · '));
  if (tags.length) bits.push(tags.map(x => '#' + x).join(' '));
  return bits.join(' / ');
}

/** 이번 일정에서만 빼고 다시 짠다. 취향에는 남기지 않는다 —
    "오늘은 여기 말고"와 "이런 곳은 싫다"는 다른 말이다. */
function swapStop(cid) {
  if (!S.exclude.includes(cid)) S.exclude.push(cid);
  S.exclude = S.exclude.slice(-30);
  refresh();
}

/** 좋아요·관심없음을 프로필에 반영한다. 서버에 저장하지 않고 화면이 들고 있는다. */
function feedback(cid, kind) {
  const step = (S.course?.steps || []).find(s => s.cid === cid)
            || S.candidates.find(c => c.cid === cid);
  if (!step) return;
  const t = S.taste = S.taste || { categories:{}, tags:{}, liked:[], disliked:[] };
  t.categories = t.categories || {}; t.tags = t.tags || {};
  t.liked = t.liked || []; t.disliked = t.disliked || [];

  const w = kind === 'like' ? 1.0 : -0.7;
  const cat = step.category;
  if (cat) t.categories[cat] = (t.categories[cat] || 0) + w;
  (step.tags || []).slice(0, 12).forEach(tag => {
    t.tags[tag] = (t.tags[tag] || 0) + w * 0.5;
  });
  const list = kind === 'like' ? t.liked : t.disliked;
  if (!list.includes(cid)) list.push(cid);

  saveTaste();
  refresh();
}

/* ───────────────────────── 렌더 ───────────────────────── */

function renderWeather(w) {
  const icon = w.pty && w.pty !== '없음' ? '🌧' : w.temp_c >= 33 ? '🔥'
             : w.sky === '흐림' ? '☁' : w.sky === '구름많음' ? '⛅' : '☀';
  $('#wx-icon').textContent = icon;
  $('#wx-desc').textContent = w.desc || '';
  const src = { kma:'기상청 실황', fallback:'기본값', manual:'시나리오' }[w.source] || '';
  $('#wx-src').textContent = src;
  $('#wx-src').title = w.note || '';
}

function renderHeadStats(c) {
  const off = !c.weather.outdoor_ok;
  $('#head-stats').innerHTML = `
    <div class="stat"><span class="v">${c.count.toLocaleString()}</span>
      <span class="k">주변에 열린 곳</span></div>
    <div class="stat"><span class="v${off ? ' off' : ''}">${off ? '실내만' : '실외 가능'}</span>
      <span class="k">지금 날씨</span></div>`;
}

function legOf(step) {
  const tv = step.travel || {};
  const rec = tv.recommended;
  return rec ? { mode:rec, ...(tv[rec] || {}) } : null;
}

/* 구간 줄은 '여기서 저기까지 몇 분'만 말한다.

   노선명까지 적었더니 위 이동 카드와 글자 하나 안 틀리고 같아졌다.
   대중교통 구간이 하나뿐이면 카드의 '🚇 대중교통 13분 · 1020 3정거장 실측'과
   구간 줄이 완전히 겹친다. 노선은 카드에 한 번만 두고, 여기서는 이 구간이
   몇 분인지만 말한다 — 그게 이 자리에만 있는 정보다. */
function travelRow(step) {
  const leg = legOf(step);
  if (!leg || !leg.minutes) return '';
  const icon = leg.mode === 'walk' ? '🚶' : '🚇';
  const label = leg.mode === 'walk' ? '도보' : '대중교통';
  const how = { tmap:'TMAP 보행경로', odsay:'ODsay 대중교통',
                naver:'네이버 경로', estimate:'직선거리 추정' }[leg.provider] || '';
  // 도보의 summary는 "TMAP 보행자 경로"처럼 방법 이름이라 how와 같은 말이
  // 된다. 노선명이 담기는 대중교통에서만 덧붙인다.
  const tip = [how, leg.mode === 'transit' && leg.exact ? leg.summary : '']
    .filter(Boolean).join(' · ');
  return `<li class="leg" title="${esc(tip)}">
    <span class="leg-line"></span>
    <span class="leg-txt">${icon} ${label} ${leg.minutes}분
      <em>${leg.exact ? '실측' : '추정'}</em></span></li>`;
}

/* 이동을 도보와 대중교통으로 갈라 보여 준다.

   구간마다 '도보 7분'이 붙어 있긴 하지만, 일정 전체에서 얼마나 걷고
   얼마나 타는지는 세어 봐야 안다. 4시간 중 40분을 걷는 일정과 12분만
   걷는 일정은 완전히 다른 하루다.

   실측인지 추정인지도 여기서 한 번에 말한다. 구간마다 붙은 배지는
   스크롤해야 보이고, 섞여 있으면 더더욱 눈에 안 띈다. */
function renderMoveCard(c) {
  const box = $('#move-card');
  if (!c || !c.steps.length) { box.innerHTML = ''; return; }

  const sum = { walk:{ n:0, min:0, m:0, exact:0, how:'' },
                transit:{ n:0, min:0, m:0, exact:0, how:'', lines:[] } };
  c.steps.forEach(s => {
    const leg = legOf(s);
    if (!leg || !leg.minutes) return;
    const a = sum[leg.mode];
    if (!a) return;
    a.n += 1; a.min += leg.minutes; a.m += leg.distance_m || 0;
    if (leg.exact) { a.exact += 1; a.how = leg.provider; }
    // summary 한 필드가 노선명("1020 3정거장")과 추정 사유("평균 이동속도
    // 기반 추정")를 겸하고 있다. 사유를 노선명 자리에 적으면 뒤의 '추정'
    // 배지와 겹쳐 "평균 이동속도 기반 추정 추정"이 된다. 실측일 때만 쓴다.
    if (leg.mode === 'transit' && leg.exact && leg.summary
        && !a.lines.includes(leg.summary)) a.lines.push(leg.summary);
  });

  const HOW = { tmap:'TMAP 보행경로', osrm:'OSM 도로망', odsay:'ODsay 대중교통' };
  const row = (mode, icon, label) => {
    const a = sum[mode];
    if (!a.n) return '';
    const measured = a.exact === a.n;
    const km = a.m >= 1000 ? `${(a.m / 1000).toFixed(1)}km` : `${a.m}m`;
    const detail = mode === 'transit' && a.lines.length
      ? a.lines.slice(0, 2).join(' · ') : (a.m ? km : '');
    return `<div class="move-row move-${mode}">
      <span class="move-ico" aria-hidden="true">${icon}</span>
      <div class="move-body">
        <b>${label} <span class="move-min">${fmtMin(a.min)}</span></b>
        <small>${a.n}구간${detail ? ' · ' + esc(detail) : ''}</small>
      </div>
      <span class="move-tag ${measured ? 'exact' : 'est'}"
            title="${esc(measured ? (HOW[a.how] || '실제 경로 API') : '직선거리 기반 추정')}"
        >${measured ? '실측' : a.exact ? `${a.exact}/${a.n} 실측` : '추정'}</span>
    </div>`;
  };

  const rows = row('walk', '🚶', '걷기') + row('transit', '🚇', '대중교통');
  if (!rows) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="move-head">이동 ${fmtMin(c.travel_min)}</div>${rows}`;
}

function renderPlan() {
  const c = S.course;
  const head = $('#plan-head'), tl = $('#timeline');
  const notes = $('#plan-notes'), backup = $('#backup');

  if (!c || !c.steps.length) {
    head.innerHTML = '';
    tl.innerHTML = '';
    backup.innerHTML = '';
    renderMoveCard(null);
    notes.innerHTML = `<div>${esc((c && c.notes && c.notes[0])
      || '조건에 맞는 일정을 만들지 못했습니다. 시간을 늘리거나 위치를 바꿔 보세요.')}</div>`;
    return;
  }

  head.innerHTML = `
    <div class="plan-time">${c.start} <span>→</span> ${c.end}</div>
    <div class="plan-meta">
      <span>${c.steps.length}곳</span>
      <span>이동 ${fmtMin(c.travel_min)}</span>
      <span>체류 ${fmtMin(c.dwell_min)}</span>
    </div>
    <div class="budget"><span style="width:${
      Math.min(100, c.total_min / c.budget_min * 100).toFixed(0)}%"></span></div>
    <div class="plan-sub">${fmtMin(c.budget_min)} 중 ${fmtMin(c.total_min)} 사용</div>`;

  renderMoveCard(c);

  tl.innerHTML = c.steps.map((s, i) => `
    ${travelRow(s)}
    <li class="stop${S.selected === s.cid ? ' sel' : ''}"
        data-role="${s.role}" data-cid="${esc(s.cid)}"
        tabindex="0" role="button"
        aria-label="${i + 1}번째 일정, ${esc(s.title)}, ${s.arrive} 도착, ${s.dwell_min}분 머묾">
      <span class="tick">${i + 1}</span>
      <div class="when">${s.arrive}<small>${s.depart}</small></div>
      <div class="body">
        <div class="t">${esc(s.title)}
          ${s.ends_today ? '<span class="badge today">오늘 마지막</span>' : ''}
          ${crowdBadge(s.crowd)}${trendBadge(s.trend)}</div>
        <div class="m">${esc(s.category_path || s.category)}
          · ${ROLE_NAME[s.role] || ''} · ${s.dwell_min}분
          ${s.hours_assumed ? '<span class="warn-tag">시간 미상</span>' : ''}</div>
        <div class="l">${esc(s.line)}</div>
        <div class="stop-acts">
          <button data-act="like" data-cid="${esc(s.cid)}">👍 이런 곳 더</button>
          <button data-act="swap" data-cid="${esc(s.cid)}">↻ 다른 곳</button>
          <button class="no" data-act="skip" data-cid="${esc(s.cid)}">✕ 관심없음</button>
        </div>
      </div>
    </li>`).join('');

  backup.innerHTML = c.backup ? `
    <div class="backup" data-cid="${esc(c.backup.cid)}">
      <span class="bk-label">플랜 B</span>
      <div>
        <b>${esc(c.backup.title)}</b>
        <small>${esc(c.backup.line || '날씨가 바뀌면 여기로 피할 수 있습니다.')}</small>
      </div>
    </div>` : '';

  notes.innerHTML = (c.notes || []).map(n => `<div>${esc(n)}</div>`).join('');

  $$('#timeline .stop').forEach(li => {
    li.onclick = e => {
      if (e.target.closest('.stop-acts')) return;
      selectCid(li.dataset.cid);
    };
    li.onkeydown = e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('.stop-acts')) return;
      e.preventDefault();
      selectCid(li.dataset.cid);
    };
  });
  $$('#timeline .stop-acts button').forEach(b => b.onclick = e => {
    e.stopPropagation();
    const act = b.dataset.act;
    if (act === 'swap') { swapStop(b.dataset.cid); return; }
    feedback(b.dataset.cid, act);
  });
  const bk = $('#backup .backup');
  if (bk) bk.onclick = () => selectCid(bk.dataset.cid);
}

function filtered() {
  let rows = S.catFilter
    ? S.candidates.filter(c => c.category === S.catFilter) : S.candidates;
  if (S.quiet && S.area) {
    /* 뜨는데 아직 안 붐비는 동네만. 동네 자료가 없는 곳은 남긴다 —
       모르는 것을 '붐빈다'로 처리하면 멀쩡한 곳이 사라진다. */
    const q = new Set(S.area.quiet);
    rows = rows.filter(c => !c.adm_cd || q.has(c.adm_cd));
  }
  return rows;
}

/* 트렌드 배지. 오르는 쪽만 보여 주면 '뜨는 곳만 있다'는 인상을 주는데,
   실제로는 식는 곳도 추천에 남는다 — 순위에 덜 반영할 뿐이다. 양쪽 다 적는다. */
/* 지금 붐비는가. 상업 지도가 구조적으로 못 하는 말이라 눈에 띄게 둔다.
   가라 마라를 정해 주지는 않는다 — 지금 몇 명이 있고 언제 한산해지는지만
   적고 판단은 사용자가 한다. 붐비는 곳을 일부러 찾는 사람도 있다. */
function crowdBadge(c) {
  if (!c || !c.crowded) return '';
  const when = c.relief_at ? c.relief_at.slice(11, 16) : '';
  const tip = [c.message, c.min ? `지금 ${c.min.toLocaleString()}~${
    c.max.toLocaleString()}명` : '', c.visitor_rate
      ? `외지인 ${c.visitor_rate.toFixed(0)}%` : ''].filter(Boolean).join(' · ');
  return `<span class="badge crowd" title="${esc(tip)}">${esc(c.level)}${
    when ? `<em>${when} 이후 여유</em>` : ''}</span>`;
}

function trendBadge(t) {
  if (!t) return '';
  const pct = Math.round(t.yoy * 100);
  const tip = `작년 같은 달 대비 ${pct > 0 ? '+' : ''}${pct}% ` +
              `(연 ${Number(t.level).toLocaleString()}회 조회)`;
  return `<span class="badge trend ${esc(t.kind)}" title="${esc(tip)}">${esc(t.label)}</span>`;
}

/* 지금 위치에서 가까운 '조용히 뜨는 동네' 몇 곳. 목록이 비었을 때
   빈 화면 대신 방향을 준다. */
function nearestQuiet() {
  const here = S.where && S.where.label ? S.where.label : '이 근처';
  const rows = Object.entries(S.area.dong)
    .filter(([, r]) => r.quiet)
    .map(([cd, r]) => ({ cd, ...r, d: dongDist(cd) }))
    .sort((a, b) => a.d - b.d).slice(0, 4);
  const items = rows.map(r =>
    `<li><b>${esc(r.name)}</b> <em>${r.momentum > 0 ? '+' : ''}${
      (r.momentum * 100).toFixed(0)}%</em>${
      r.d < 1e8 ? `<span>${(r.d / 1000).toFixed(1)}km</span>` : ''}</li>`
  ).join('');
  return `<p><b>${esc(here)}</b> 주변에는 아직 조용한 동네가 없습니다.
    도심은 이미 붐비는 쪽이라 그렇습니다.</p>
    <p class="q-h">가까운 곳 · 방문은 느는데 아직 안 붐비는 동네</p>
    <ul>${items}</ul>`;
}

function dongDist(cd) {
  if (!S.dongGeo || !S.lat) return 1e9;
  const f = S.dongGeo.features.find(x => x.properties.adm_cd === cd);
  if (!f) return 1e9;
  const c = centroidOf(f.geometry);
  if (!c) return 1e9;
  const R = 6371000, t = Math.PI / 180;
  const dLat = (c[1] - S.lat) * t, dLon = (c[0] - S.lon) * t;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(S.lat * t) * Math.cos(c[1] * t) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function centroidOf(g) {
  const rings = g.type === 'Polygon' ? [g.coordinates[0]]
    : g.type === 'MultiPolygon' ? g.coordinates.map(x => x[0]) : [];
  let x = 0, y = 0, n = 0;
  rings.forEach(r => r.forEach(pt => { x += pt[0]; y += pt[1]; n += 1; }));
  return n ? [x / n, y / n] : null;
}

function renderCandidates() {
  const cats = [...new Set(S.candidates.map(c => c.category).filter(Boolean))];
  $('#cat-filters').innerHTML =
    [`<button data-cat="" class="${S.catFilter ? '' : 'on'}">전체</button>`]
    .concat(cats.map(c => `<button data-cat="${esc(c)}" class="${
      S.catFilter === c ? 'on' : ''}">${esc(c)}</button>`)).join('');
  $$('#cat-filters button').forEach(b => b.onclick = () => {
    S.catFilter = b.dataset.cat || null;
    renderCandidates(); drawMap();
  });

  const rows = filtered();
  /* 빈 목록을 그냥 두면 고장으로 보인다. 이 필터는 도심에서 자주 비는데,
     조용한 동네 28곳이 대체로 도심 밖에 있어서다 — 그게 이 서비스가
     하려는 말이기도 하다. 어디로 가면 되는지 짚어 준다. */
  const empty = $('#quiet-empty');
  if (empty) {
    const show = S.quiet && !rows.length && S.area;
    empty.hidden = !show;
    if (show) empty.innerHTML = nearestQuiet();
  }
  $('#list-summary').textContent = S.where
    ? `${S.where.label} 반경 2.5km · 지금 갈 수 있는 ${rows.length.toLocaleString()}곳`
    : `지금 갈 수 있는 ${rows.length.toLocaleString()}곳`;
  $('#cand-list').innerHTML = rows.slice(0, 120).map(c => `
    <li data-cid="${esc(c.cid)}" class="${S.selected === c.cid ? 'sel' : ''}">
      <div class="t">${esc(c.title)}${crowdBadge(c.crowd)}${trendBadge(c.trend)}${
        S.area && c.adm_cd && S.area.dong[c.adm_cd]
          && S.area.dong[c.adm_cd].quiet
          ? '<span class="badge quiet">아직 조용함</span>' : ''}</div>
      <div class="m">
        <span>${esc(c.category)}</span>
        <span class="n">${c.distance_m < 1000 ? c.distance_m + 'm'
                        : (c.distance_m / 1000).toFixed(1) + 'km'}</span>
        <span>${c.environment === 'indoor' ? '실내'
              : c.environment === 'outdoor' ? '실외' : '실내외 불명'}</span>
        ${c.popularity > 0.4 ? '<span class="pop-tag">인기</span>' : ''}
      </div>
    </li>`).join('');
  $$('#cand-list li').forEach(li => li.onclick = () => selectCid(li.dataset.cid));
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
  const h = s.hours_confidence || {}, e = s.environment || {}, t = s.total || 1;
  const ended = s.dated.total ? (s.dated.ended / s.dated.total * 100) : 0;
  const dist = Object.entries(s.distribution || {}).slice(0, 8);
  const maxDist = dist.length ? dist[0][1] : 1;

  $('#evidence').innerHTML = `
    <div class="ev-block"><h3>운영시간, 규칙만으로 어디까지</h3>
      ${bar('확정 가능', h.high || 0, t, 'ok')}
      ${bar('가정·예외', h.low || 0, t)}
      ${bar('판정 불가', h.none || 0, t, 'warn')}
      <p class="ev-note">규칙만으로 확정되는 건 <b>${((h.high||0)/t*100).toFixed(1)}%</b>.
        나머지가 LLM 정규화의 몫입니다.</p></div>
    <div class="ev-block"><h3>실내·실외 — API에 없는 필드</h3>
      ${bar('실내', e.indoor || 0, t, 'ok')}
      ${bar('실외', e.outdoor || 0, t)}
      ${bar('불명', e.unknown || 0, t, 'warn')}
      <p class="ev-note">날씨 대응의 전제인데
        <b>${((e.unknown||0)/t*100).toFixed(1)}%</b>가 규칙으로 안 가려집니다.</p></div>
    <div class="ev-block"><h3>기간이 있는 콘텐츠의 시의성</h3>
      ${bar('이미 종료', s.dated.ended, s.dated.total, 'warn')}
      <p class="ev-note">기간 지정 ${s.dated.total.toLocaleString()}건 중
        <b>${s.dated.ended.toLocaleString()}건(${ended.toFixed(1)}%)</b>이 끝난 행사입니다.</p></div>
    <div class="ev-block"><h3>필터 통과</h3>
      ${bar('지금 가능', s.funnel.passed, t, 'ok')}
      <p class="ev-note">전체 ${t.toLocaleString()}건 →
        <b>${s.funnel.passed.toLocaleString()}건</b></p></div>
    ${dist.length ? `<div class="ev-block"><h3>자치구 분포</h3>
      ${dist.map(([gu, n]) => bar(gu, n, maxDist)).join('')}
      <p class="ev-note">추천이 이 분포보다 고르면 관광 분산 효과가 있다고 봅니다.</p>
    </div>` : ''}`;
}

/** 추천 근거를 항목별로 보여 준다.
    "AI가 골랐습니다"는 설명이 아니다. 무엇을 보고 골랐는지 말할 수 있어야
    사용자가 판단을 검증하고, 마음에 안 들면 무엇을 바꿀지 안다. */
function whyBlock(c) {
  const w = c.why;
  if (!w || !w.parts) return '';
  const rows = w.parts.map(p => {
    const pct = Math.max(0, Math.min(100, p.value * 100));
    const share = Math.round(p.weight * 100);
    return `<div class="why-row">
      <span class="why-lab">${esc(p.label)}<em>${share}%</em></span>
      <span class="why-track"><span class="why-fill" style="width:${pct}%"></span></span>
      <span class="why-note">${esc(p.note || '')}</span>
    </div>`;
  }).join('');
  return `<div class="d-sec"><h4>선정 근거</h4>${rows}
    <p class="why-foot">거리·정보 충실도·알려진 정도·요즘 뜨는 정도·취향을
      섞어 고릅니다. 어느 하나가 전부를 결정하지 않습니다.<br>
      &lsquo;요즘 뜨는&rsquo;은 작년 같은 달과 견준 관심의 변화입니다 &mdash;
      방문객 수가 아니라 새로 찾아보는 사람의 수입니다.</p></div>`;
}

function renderDetail(c) {
  if (!c || !c.cid) return;
  $('#detail-panel').hidden = false;
  $('#detail-title').textContent = c.title || '';
  const vClass = c.verdict === '판정불가' ? 'unknown' : c.verdict === '탈락' ? 'fail' : '';
  const envText = { indoor:'실내', outdoor:'실외', unknown:'실내외 불명' }[c.environment] || '';
  const period = c.schedule_start
    ? `${esc(c.schedule_start)} ~ ${esc(c.schedule_end || '')}` : '';
  const tv = c.travel || {};
  const legs = ['walk','transit'].map(k => {
    const leg = tv[k];
    if (!leg) return '';
    const how = { tmap:'TMAP 보행경로', odsay:'ODsay', naver:'네이버',
                  estimate:'직선거리 추정' }[leg.provider] || leg.provider;
    return `<div class="leg-row"><span>${k === 'walk' ? '도보' : '대중교통'}
      <b>${leg.minutes}분</b> <span class="who">${leg.distance_m.toLocaleString()}m</span></span>
      <span class="who">${esc(leg.summary || how)}</span></div>`;
  }).join('');

  $('#detail').innerHTML = `
    ${(c.arrive) ? `<div class="d-when">${c.arrive} 도착 · ${c.dwell_min}분 머묾 · ${c.depart} 출발</div>` : ''}
    <div class="verdict ${vClass}"><div><b>${esc(c.verdict || '통과')}</b>
      <small>${esc(c.reason || c.verdict_reason || '')}</small></div></div>
    ${c.line ? `<div class="d-sec"><h4>왜 여기</h4><p>${esc(c.line)}</p></div>` : ''}
    ${whyBlock(c)}
    ${legs ? `<div class="d-sec"><h4>가는 길</h4>${legs}</div>` : ''}
    ${c.summary ? `<div class="d-sec"><h4>요약</h4><p>${esc(c.summary)}</p></div>` : ''}
    <div class="d-sec"><h4>분류</h4><p>${esc(c.category_path || c.category || '')}
      · ${esc(envText)}${c.gu ? ` · ${esc(c.gu)} ${esc(c.dong || '')}` : ''}</p></div>
    ${period ? `<div class="d-sec"><h4>기간</h4><p>${period}</p></div>` : ''}
    ${c.address ? `<div class="d-sec"><h4>주소</h4><p>${esc(c.address)}</p></div>` : ''}
    ${c.subway ? `<div class="d-sec"><h4>교통</h4><p>${esc(c.subway)}</p></div>` : ''}
    ${c.phone ? `<div class="d-sec"><h4>전화</h4><p><a class="d-link" href="tel:${esc(c.phone)}">${esc(c.phone)}</a></p></div>` : ''}
    ${c.use_time ? `<div class="d-sec"><h4>이용시간 원문</h4>
      <p class="raw">${esc(c.use_time)}</p></div>` : ''}
    ${c.closed_days ? `<div class="d-sec"><h4>휴무일 원문</h4>
      <p class="raw">${esc(c.closed_days)}</p></div>` : ''}
    ${(c.accessibility && c.accessibility.length) ? `<div class="d-sec">
      <h4>무장애 시설</h4><div class="tag-row">${
      c.accessibility.map(a => `<span>${esc(a)}</span>`).join('')}</div></div>` : ''}
    ${(c.tags && c.tags.length) ? `<div class="d-sec"><h4>태그</h4><div class="tag-row">${
      c.tags.map(t => `<span>#${esc(t)}</span>`).join('')}</div></div>` : ''}
    ${c.homepage ? `<div class="d-sec"><h4>홈페이지</h4>
      <a class="d-link" href="${esc(c.homepage)}" target="_blank" rel="noopener">${esc(c.homepage)}</a></div>` : ''}
    <div class="d-sec"><h4>운영시간 정규화</h4><p>${esc({
      high:'확정 — 요일·시각 모두 명시',
      low:'가정 포함 — 예외 단서 또는 요일 누락',
      none:'판정 불가 — 시각 패턴 없음' }[c.hours_confidence] || '—')}</p></div>
    <div class="d-sec"><a class="d-link" target="_blank" rel="noopener"
      href="https://map.naver.com/p/search/${encodeURIComponent(c.title || '')}">네이버 지도에서 보기 ↗</a></div>`;
  sweepKorean($('#detail') || document.body);
}

/* ───────────────────────── 지도 그리기 ───────────────────────── */

function drawMap() {
  if (!mapReady) return;
  const steps = (S.course?.steps || []).filter(s => s.lat && s.lon);

  layers.steps.clearLayers();
  layers.route.clearLayers();

  steps.forEach((s, i) => {
    const color = ROLE_COLOR[s.role] || '#1f7ac4';
    L.circleMarker([s.lat, s.lon], { radius:17, weight:0, fillColor:color,
      fillOpacity:.15, interactive:false }).addTo(layers.steps);
    L.marker([s.lat, s.lon], {
      icon:L.divIcon({ className:'step-pin',
        html:`<span style="background:${color}">${i + 1}</span>`,
        iconSize:[26,26], iconAnchor:[13,13] }),
      title:`${s.arrive} ${s.title}`,
    }).addTo(layers.steps).on('click', () => selectCid(s.cid));
  });

  const path = [[S.lat, S.lon], ...steps.map(s => [s.lat, s.lon])];
  if (path.length > 1) {
    L.polyline(path, { color:'#1f7ac4', weight:2.5, opacity:.5,
      dashArray:'7 6' }).addTo(layers.route);
  }
  if (S.course?.backup?.lat) {
    const b = S.course.backup;
    L.circleMarker([b.lat, b.lon], { radius:7, weight:2, color:'#fff',
      fillColor:ROLE_COLOR.shelter, fillOpacity:.95 })
      .addTo(layers.steps).on('click', () => selectCid(b.cid));
  }

  layers.cands.clearLayers();
  if (S.showAll) {
    filtered().filter(c => c.lat && c.lon).forEach(c => {
      L.circleMarker([c.lat, c.lon], { radius:3.5, weight:1, color:'#fff',
        fillColor:'#8aa0b4', fillOpacity:.85 })
        .addTo(layers.cands).on('click', () => selectCid(c.cid));
    });
  }

  drawMe();

  if (path.length > 1) {
    map.invalidateSize({ animate:false });
    const el = map.getContainer();
    const wide = el.clientWidth > 980;
    const left = wide ? ($('.panel-left')?.offsetWidth || 0) + 40 : 24;
    const right = wide && !$('#detail-panel').hidden
      ? ($('.panel-right')?.offsetWidth || 0) + 40 : 24;
    map.fitBounds(L.latLngBounds(path), {
      paddingTopLeft:[left, wide ? 100 : 24],
      paddingBottomRight:[right, 60], maxZoom:16, animate:false });
  }
}

function selectCid(cid) {
  S.selected = cid;
  const inPlan = (S.course?.steps || []).find(s => s.cid === cid)
              || (S.course?.backup?.cid === cid ? S.course.backup : null);
  const inList = S.candidates.find(c => c.cid === cid);
  renderDetail({ ...(inList || {}), ...(inPlan || {}) });
  $('#detail-close').focus();
  $$('#timeline .stop, #cand-list li').forEach(li =>
    li.classList.toggle('sel', li.dataset.cid === cid));
  const p = inPlan || inList;
  if (mapReady && p?.lat) {
    map.setView([p.lat, p.lon], Math.max(map.getZoom(), 16), { animate:false });
  }
}

/* ───────────────────── AI 도우미(에이전트) ─────────────────────
   탭이 아니라 언제든 열 수 있는 창이다. 지도를 보다가, 일정을 보다가
   막히는 순간에 묻게 되므로 화면을 갈아치우면 안 된다. */

function aiOpen(on) {
  const panel = $('#ai-panel'), fab = $('#ai-fab');
  panel.hidden = !on;
  fab.setAttribute('aria-expanded', String(on));
  fab.classList.toggle('on', on);
  if (on) {
    if (!$('#ai-log').children.length) aiGreet();
    setTimeout(() => $('#ai-input').focus(), 60);
  }
}

function aiGreet() {
  const w = S.where;
  const el = pushMsg('bot', '');
  typeInto(el, w?.label
    ? `${w.label}에 계시네요. 주변에 지금 갈 수 있는 곳이 ${w.nearby}곳 있어요.
시간이 얼마나 있으신지 알려주시면 순서까지 짜 드릴게요.`
    : '어디서 얼마나 시간이 있으신지 알려주세요. 지금 열려 있는 곳만 골라 드릴게요.');
}

const fmtMsg = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

function pushMsg(who, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + (who === 'me' ? 'me' : 'bot');
  el.innerHTML = fmtMsg(text);
  $('#ai-log').appendChild(el);
  $('#ai-log').scrollTop = $('#ai-log').scrollHeight;
  return el;
}

/* 답을 한 글자씩 흘려 준다.

   통째로 나타나면 어디부터 읽어야 할지 모르겠고, 무엇보다 방금 도구를
   돌려 만든 답이라는 감각이 사라진다. 다만 길이에 비례해 마냥 길어지면
   기다리는 시간이 되므로 전체를 1.6초 안에 끝낸다 — 연출이지 지연이
   아니어야 한다. 움직임을 줄이도록 설정한 사람에게는 바로 보여 준다. */
function typeInto(el, text, done) {
  const log = $('#ai-log');
  const finish = () => {
    el.classList.remove('typing-on');
    el.innerHTML = fmtMsg(text);
    log.scrollTop = log.scrollHeight;
    if (done) done();
  };
  if (!text || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return finish();
  }
  const STEP = 16;
  const budget = Math.min(1600, Math.max(350, text.length * 11));
  const began = performance.now();
  el.classList.add('typing-on');

  // 진행을 '몇 번 그렸나'가 아니라 '얼마나 지났나'로 잰다.
  //
  // requestAnimationFrame은 탭이 안 보이면 아예 멈추고, 타이머도 배경
  // 탭에서는 초 단위로 늘어난다. 틱마다 몇 글자씩 더하는 방식으로 두면
  // 그때 타이핑이 기어가서, 다른 탭을 보다 돌아온 사람 앞에 답이 반쯤
  // 쓰이다 만 채로 남는다. 경과 시간으로 계산하면 밀린 만큼 따라잡는다.
  const tick = () => {
    if (!el.isConnected) return;          // 대화를 지웠으면 그만둔다
    const done = (performance.now() - began) / budget;
    const i = Math.min(text.length, Math.ceil(text.length * done));
    el.innerHTML = fmtMsg(text.slice(0, i));
    log.scrollTop = log.scrollHeight;
    if (done < 1) setTimeout(tick, STEP);
    else finish();
  };
  setTimeout(tick, STEP);
}

/* 대화로 정한 조건을 화면 상태에 반영한다.

   반영하지 않으면 "비 오는데 북촌에서 3시간"이라 말해 놓고도 왼쪽 폼은
   '4시간·실시간'을 가리키고, 헤더는 '실외 가능'이라고 적혀 있다.
   그 상태에서 칩을 하나 누르면 대화 결과가 조용히 사라진다. */
function adoptIntent(intent, course) {
  const it = intent || {};
  const o = (course || {}).origin;
  let moved = false;
  if (o && (o.lat !== S.lat || o.lon !== S.lon)) {
    S.lat = o.lat; S.lon = o.lon; S.precise = false; moved = true;
  }
  if (it.hours) S.hours = it.hours;
  if (it.weather_mode) S.mode = it.weather_mode;
  if (it.interests && it.interests.length) S.interests = it.interests;
  syncControls();

  // 주변 개수와 '실외 가능' 배지는 새 위치·새 날씨 기준으로 다시 센다
  getJSON('/api/candidates', { ...baseParams(), radius_m:2500, limit:200 })
    .then(c => { S.candidates = c.items; renderHeadStats(c); renderCandidates(); })
    .catch(() => {});
  if (moved) refreshWhere();
}

/* 무엇을 했는지 보여 준다. 에이전트가 챗봇과 다른 점이 여기다 —
   답만 던지지 않고 어떤 도구를 어떤 순서로 돌렸는지 남긴다. */
function appendTrace(box, trace, engine) {
  if (!trace || !trace.length) return;
  const NAME = {
    parse_intent:'말 이해', resolve_where:'위치 확인', apply_taste:'취향 반영',
    plan_course:'일정 구성', read_weather:'날씨 조회', read_thermal:'위성 열지도',
    measure_walk:'도보 실측', check_hours:'운영시간 확인',
  };
  const rows = trace.map(t => `<li class="tr-${esc(t.status)}">
      <b>${esc(NAME[t.tool] || t.tool)}</b><span>${esc(t.detail || '')}</span></li>`).join('');
  box.insertAdjacentHTML('beforeend', `
    <details class="ai-trace"><summary>어떻게 골랐는지 (${trace.length}단계 ·
      ${engine === 'llm' ? 'AI 문장' : '규칙 문장'})</summary>
      <ul>${rows}</ul></details>`);
}

function appendEvidence(box, ev) {
  if (!ev || !ev.length) return;
  const cards = ev.slice(0, 4).map(e => `<div class="ai-ev">
      <b>${esc(e.title)}</b><span>${esc(e.summary || '')}</span>
      ${e.note ? `<small>${esc(e.note)}</small>` : ''}</div>`).join('');
  box.insertAdjacentHTML('beforeend', `<div class="ai-evs">${cards}</div>`);
}

function appendActions(box, actions) {
  if (!actions || !actions.length) return;
  const el = document.createElement('div');
  el.className = 'ai-acts';
  actions.forEach(a => {
    const b = document.createElement('button');
    b.textContent = a.label;
    b.onclick = () => {
      if (a.act === 'save') { savePlan(); return; }
      if (a.tab) switchTab(a.tab);
      aiOpen(false);
    };
    el.appendChild(b);
  });
  box.appendChild(el);
}

async function send(text) {
  const msg = (text || $('#ai-input').value).trim();
  if (!msg || S.busy) return;
  S.busy = true;
  $('#ai-input').value = '';
  $('#ai-send').disabled = true;

  pushMsg('me', msg);
  S.history.push({ role:'user', content:msg });
  const typing = document.createElement('div');
  typing.className = 'typing';
  typing.innerHTML = '<i></i><i></i><i></i>';
  $('#ai-log').appendChild(typing);
  $('#ai-log').scrollTop = $('#ai-log').scrollHeight;

  try {
    const r = await fetch('/api/agent', {
      method:'POST', headers:{ 'Content-Type':'application/json' },
      // 지금 짜 둔 일정을 함께 보낸다. 그래야 "비 온대요"에 처음부터
      // 다시 짜지 않고 원래 하려던 경험을 지키며 고칠 수 있다.
      body:JSON.stringify({ message:msg, messages:S.history,
                            lat:S.lat, lon:S.lon, at:S.at,
                            intent:S.intent, taste:S.taste,
                            styles:S.styles, course:S.course, lang:S.lang }),
    });
    const data = await r.json();
    typing.remove();
    S.intent = data.intent;
    if (data.taste) { S.taste = data.taste; saveTaste(); renderTaste(); }
    if (data.course) {
      S.course = data.course;
      adoptIntent(data.intent, data.course);
      if (data.course.weather) renderWeather(data.course.weather);
      renderPlan(); drawMap();
    }
    const answer = data.answer || data.reply || '';
    const bubble = pushMsg('bot', '');
    // 근거와 행동 버튼은 문장이 다 나온 뒤에 붙인다. 타이핑 중에 아래에서
    // 버튼이 먼저 튀어나오면 읽던 자리가 밀린다.
    typeInto(bubble, answer, () => {
      appendTrace(bubble, data.tool_trace, data.engine);
      appendEvidence(bubble, data.evidence);
      appendActions(bubble, data.actions);
      $('#ai-log').scrollTop = $('#ai-log').scrollHeight;
    });
    S.history.push({ role:'assistant', content:answer });
  } catch (e) {
    typing.remove();
    pushMsg('bot', '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    S.busy = false;
    $('#ai-send').disabled = false;
  }
}

/* ───────────────────────── 보관함 ─────────────────────────
   짠 일정을 브라우저에 남긴다. 서버에는 아무것도 보내지 않는다. */

function vaultLoad() {
  try { return JSON.parse(localStorage.getItem(LS_VAULT) || '[]'); }
  catch (e) { return []; }
}

function vaultSave(list) {
  try { localStorage.setItem(LS_VAULT, JSON.stringify(list.slice(0, 20))); }
  catch (e) { toast('저장 공간이 부족합니다'); }
}

function savePlan() {
  const c = S.course;
  if (!c || !c.steps.length) return toast('저장할 일정이 없습니다');
  const list = vaultLoad();
  const item = {
    id: Date.now(),
    where: (S.where && S.where.label) || '서울',
    when: c.start, end: c.end, at: S.at, hours: S.hours, mode: S.mode,
    lat: S.lat, lon: S.lon, lang: S.lang, interests: S.interests,
    titles: c.steps.map(s => s.title),
    saved: new Date().toISOString().slice(0, 16).replace('T', ' '),
  };
  list.unshift(item);
  vaultSave(list);
  renderVault();
  toast('일정을 저장했습니다');
}

function renderVault() {
  const list = vaultLoad();
  $('#vault-count').textContent = list.length ? ` ${list.length}` : '';
  const box = $('#vault');
  if (!list.length) {
    box.innerHTML = '<p class="vault-empty">저장한 일정이 없습니다. ' +
      '마음에 드는 일정을 만든 뒤 저장을 눌러 보세요.</p>';
    return;
  }
  box.innerHTML = list.map(v => `
    <div class="vault-item" data-id="${v.id}">
      <div class="vault-body">
        <b>${esc(v.where)} · ${esc(v.when)}–${esc(v.end)}</b>
        <small>${esc(v.titles.join(' → '))}</small>
      </div>
      <button class="vault-del" data-id="${v.id}" aria-label="삭제">×</button>
    </div>`).join('');
  $$('#vault .vault-item').forEach(el => el.onclick = e => {
    if (e.target.closest('.vault-del')) return;
    restorePlan(+el.dataset.id);
  });
  $$('#vault .vault-del').forEach(b => b.onclick = e => {
    e.stopPropagation();
    vaultSave(vaultLoad().filter(v => v.id !== +b.dataset.id));
    renderVault();
  });
}

function restorePlan(id) {
  const v = vaultLoad().find(x => x.id === id);
  if (!v) return;
  S.lat = v.lat; S.lon = v.lon; S.at = v.at; S.hours = v.hours;
  S.mode = v.mode; S.lang = v.lang || 'ko'; S.interests = v.interests || [];
  if (v.at) $('#at').value = v.at;
  syncControls();
  $('#vault').hidden = true;
  $('#vault-btn').classList.remove('on');
  refreshWhere();
  refresh();
  toast('저장한 일정을 불러왔습니다');
}

/* ───────────────────────── 공유 ───────────────────────── */

function planUrl() {
  const p = new URLSearchParams({
    lat:S.lat.toFixed(5), lon:S.lon.toFixed(5),
    h:S.hours, m:S.mode, lang:S.lang,
  });
  if (S.at) p.set('at', S.at);
  if (S.interests.length) p.set('i', S.interests.join(','));
  return `${location.origin}${location.pathname}?${p}`;
}

function readUrlState() {
  const p = new URLSearchParams(location.search);
  const num = (k, d) => {
    const v = parseFloat(p.get(k));
    return Number.isFinite(v) ? v : d;
  };
  if (p.has('lat') && p.has('lon')) {
    S.lat = num('lat', S.lat); S.lon = num('lon', S.lon);
    return true;                       // 공유된 링크로 들어왔다
  }
  return false;
}

function applyUrlOptions() {
  const p = new URLSearchParams(location.search);
  if (p.has('h')) {
    S.hours = parseFloat(p.get('h')) || 4;
    $$('#hours-seg button').forEach(b =>
      b.classList.toggle('on', +b.dataset.h === S.hours));
  }
  if (p.has('m')) {
    S.mode = p.get('m');
    $$('#mode-seg button').forEach(b =>
      b.classList.toggle('on', b.dataset.mode === S.mode));
  }
  if (p.has('at')) { S.at = p.get('at'); $('#at').value = S.at; }
  if (p.has('lang')) S.lang = p.get('lang');
  if (p.has('i')) {
    S.interests = p.get('i').split(',').filter(Boolean);
    $$('#interests button').forEach(b =>
      b.classList.toggle('on', S.interests.includes(b.dataset.i)));
  }
}

async function share() {
  const url = planUrl();
  const c = S.course;
  const text = c && c.steps.length
    ? `웨더핏 서울 · ${c.start}–${c.end} · ` +
      c.steps.map(s => s.title).join(' → ')
    : '웨더핏 서울';
  try {
    if (navigator.share) {
      await navigator.share({ title:'웨더핏 서울', text, url });
      return;
    }
    await navigator.clipboard.writeText(url);
    toast('링크를 복사했습니다');
  } catch (e) {
    toast('링크 복사에 실패했습니다');
  }
}

function toast(msg) {
  let el = $('#toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast'; el.className = 'toast glass';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('on'), 2200);
}

function switchTab(name) {
  $$('#tabs button').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  ['plan','list','evidence'].forEach(t => $('#pane-' + t).hidden = t !== name);
}

/* ───────────────────────── 시작 ───────────────────────── */

async function start(usePrecise) {
  $('#geo-overlay').hidden = true;
  if (usePrecise) await locate();
  await refreshWhere();
  await refresh();
  if (S.lang === 'en') {
    pushMsg('bot', S.where && S.where.in_seoul
      ? `Starting from ${S.where.label}. ${S.where.nearby} places nearby are open right now.
Tell me if you want to change anything.`
      : 'Working from central Seoul. Where are you, and how long do you have?');
    return;
  }
  pushMsg('bot', S.where && S.where.in_seoul
    ? `${S.where.label}에서 시작합니다. 주변에 지금 갈 수 있는 곳이 ${S.where.nearby}곳 있습니다.\n조건을 바꾸고 싶으면 말씀해 주세요.`
    : '서울 기준으로 안내합니다. 어디서 얼마나 시간이 있으신지 알려주세요.');
}

function sensibleStart() {
  // 새벽에 열어도 새벽 일정을 주면 안 된다. 관광이 가능한 시간대로 당긴다.
  const d = new Date();
  const h = d.getHours();
  if (h < 8) { d.setHours(10, 0, 0, 0); }
  else if (h >= 20) { d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0); }
  else { d.setMinutes(Math.ceil(d.getMinutes() / 10) * 10, 0, 0); }
  return d;
}

function toLocalInput(d) {
  const c = new Date(d);
  c.setMinutes(c.getMinutes() - c.getTimezoneOffset());
  return c.toISOString().slice(0, 16);
}

function bindUI() {
  // 지역 변수 이름을 start로 두면 전역 start() 함수를 가려 버린다
  const startAt = sensibleStart();
  $('#at').value = toLocalInput(startAt);
  S.at = $('#at').value;
  const now = new Date();
  if (startAt.getDate() !== now.getDate()) {
    geoNote('지금은 늦은 시간이라 내일 오전 10시 기준으로 잡았습니다.');
  } else if (now.getHours() < 8) {
    geoNote('지금은 이른 시간이라 오늘 오전 10시 기준으로 잡았습니다.');
  }

  $('#geo-allow').onclick = () => start(true);
  $('#geo-skip').onclick = () => start(false);
  $('#where-chip').onclick = async () => {
    await locate();
    await refreshWhere();
    await refresh();
  };

  $('#at').onchange = e => { S.at = e.target.value || null; refresh(); };
  $$('#hours-seg button').forEach(b => b.onclick = () => {
    $$('#hours-seg button').forEach(x => x.classList.toggle('on', x === b));
    S.hours = +b.dataset.h; refresh();
  });
  $$('#mode-seg button').forEach(b => b.onclick = () => {
    $$('#mode-seg button').forEach(x => x.classList.toggle('on', x === b));
    S.mode = b.dataset.mode; refresh();
  });
  $$('#interests button').forEach(b => b.onclick = () => {
    const v = b.dataset.i;
    const on = b.classList.toggle('on');
    S.interests = on ? [...new Set([...S.interests, v])]
                     : S.interests.filter(x => x !== v);
    refresh();
  });
  $('#share-btn').onclick = share;
  $('#save-btn').onclick = savePlan;
  $('#vault-btn').onclick = e => {
    const on = e.currentTarget.classList.toggle('on');
    $('#vault').hidden = !on;
    if (on) renderVault();
  };
  $('#taste-reset').onclick = () => {
    S.taste = null; S.interests = []; S.exclude = [];
    $$('#interests button').forEach(b => b.classList.remove('on'));
    saveTaste(); refresh();
  };

  $$('#lang-seg button').forEach(b => b.onclick = () => {
    if (b.disabled) return;
    $$('#lang-seg button').forEach(x => x.classList.toggle('on', x === b));
    S.lang = b.dataset.lang;
    try { localStorage.setItem(LS_LANG, S.lang); } catch (e) { /* 무시 */ }
    applyChrome();
    refresh();
  });

  $$('#tabs button').forEach(b => b.onclick = () => switchTab(b.dataset.tab));

  $$('#quiet-seg button').forEach(b => b.onclick = async () => {
    $$('#quiet-seg button').forEach(x => x.classList.toggle('on', x === b));
    S.quiet = !!b.dataset.q;
    if (S.quiet && !S.area) {
      try { S.area = await getJSON('/api/area'); } catch (e) { /* 무시 */ }
    }
    renderCandidates(); drawMap();
  });

  $$('#styles button').forEach(b => b.onclick = e => {
    const on = e.currentTarget.classList.toggle('on');
    e.currentTarget.setAttribute('aria-pressed', on ? 'true' : 'false');
    const k = e.currentTarget.dataset.s;
    S.styles = on ? [...S.styles, k] : S.styles.filter(x => x !== k);
    saveStyles();
    refresh();
  });
  $$('.seg-map button').forEach(b => b.onclick = () => setMapMode(b.dataset.map));
  $('#ai-form').onsubmit = e => { e.preventDefault(); send(); };
  $('#ai-fab').onclick = () => aiOpen($('#ai-panel').hidden);
  document.addEventListener('keydown', e => {
    if (e.altKey && (e.key === 'a' || e.key === 'A')) {
      e.preventDefault(); aiOpen($('#ai-panel').hidden);
    } else if (e.key === 'Escape' && !$('#ai-panel').hidden) {
      aiOpen(false); $('#ai-fab').focus();
    }
  });
  $('#ai-close').onclick = () => aiOpen(false);
  $$('#ai-chips button').forEach(b => b.onclick = () => send(b.textContent));

  $('#btn-dong').onclick = e => {
    const on = e.currentTarget.classList.toggle('on');
    e.currentTarget.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (!mapReady || !layers.dong) return;
    if (on) { layers.dong.addTo(map); layers.dong.bringToBack(); }
    else map.removeLayer(layers.dong);
  };
  $('#btn-all').onclick = e => {
    S.showAll = e.currentTarget.classList.toggle('on');
    e.currentTarget.setAttribute('aria-pressed', S.showAll ? 'true' : 'false');
    if (mapReady) S.showAll ? layers.cands.addTo(map) : map.removeLayer(layers.cands);
    drawMap();
  };
  $('#btn-me').onclick = () => {
    if (mapReady) map.setView([S.lat, S.lon], 15, { animate:false });
  };

  $('#detail-close').onclick = () => {
    $('#detail-panel').hidden = true;
    // 목록에서 열었으면 목록으로 초점을 돌려준다
    const back = $(`#timeline .stop[data-cid="${S.selected}"]`)
              || $(`#cand-list li[data-cid="${S.selected}"]`);
    if (back) back.focus();
  };
  $('#help-btn').onclick = () => { $('#help-overlay').hidden = false; };
  $('#help-close').onclick = () => { $('#help-overlay').hidden = true; };
  $('#help-overlay').onclick = e => {
    if (e.target.id === 'help-overlay') $('#help-overlay').hidden = true;
  };
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    $('#help-overlay').hidden = true;
    $('#detail-panel').hidden = true;
  });
}

/* 화면 라벨을 언어에 맞춘다.
   원본 한국어를 data-ko에 담아 두고 오갈 수 있게 한다 — 한 번 영어로
   덮어쓰고 나면 되돌릴 방법이 없어서다. */
/* 화면에 남는 우리 문장을 영어로. 서버 쪽 i18n.py와 같은 구절 표이고,
   같은 이유로 세 글자 이하 키에는 경계를 건다 — '도보'가 '듣도보도못한말'
   안에서 걸리면 낱말 가운데가 잘린다. */
const KO2EN = {"판정 불가": "Unverifiable", "판정불가": "Unverifiable", "통과": "Open", "탈락": "Excluded", "기간": "Dates", "운영": "Hours", "날씨": "Weather", "이동": "Travel", "상시 콘텐츠 (기간 없음)": "Always open (no set dates)", "일정 표기를 해석할 수 없음": "Schedule text could not be parsed", "시작 예정": "starts", "종료": "ended", "진행 중": "Currently running", "오늘 마지막": "Last day today", "운영시간 판정 불가": "Opening hours could not be determined", "현재 휴무 또는 영업시간 밖": "Closed now or outside opening hours", "영업 중": "Open now", "시간 미상": "Hours unknown", "에 문을 열어 두는 곳입니다": " and open at this time", "도착이면 식사 시간에 맞습니다": " arrival fits a mealtime", "곧 닫습니다": "closing soon", "실외 가능": "Outdoor OK", "실외 부적합": "Not suited to outdoors", "실외 유지": "outdoors kept", "실내외 불명": "Indoor/outdoor unknown", "실내": "Indoor", "실외": "Outdoor", "맑음": "Clear", "흐림": "Cloudy", "구름많음": "Mostly cloudy", "구름 많음": "Mostly cloudy", "비/눈": "Rain or snow", "소나기": "Showers", "빗방울": "Drizzle", "없음": "None", "폭염": "Extreme heat", "한파": "Extreme cold", "강수": "precipitation", "야외 활동에 무리가 없습니다": "fine for being outdoors", "지금 갈 수 있음": "You can go now", "지금 날씨": "Weather now", "주변에 열린 곳": "Open nearby", "기준 위치": "Reference point", "주변": "nearby", "뜨는 중": "Rising", "최근 급등": "Recent spike", "올랐다 진정": "Rose, now easing", "꾸준함": "Steady", "식는 중": "Cooling", "자료 없음": "No data", "기준 흔들림": "Baseline shifted", "아직 조용함": "Still quiet", "붐빔": "Crowded", "약간 붐빔": "Somewhat crowded", "보통": "Moderate", "여유": "Not busy", "가까움": "Nearby", "정보 충실": "Well documented", "알려진 곳": "Well known", "요즘 뜨는": "Trending", "취향 일치": "Matches taste", "여행 스타일": "Travel style", "추이 자료 없음 — 순위에 영향 없음": "No trend data — does not affect ranking", "작년 같은 달 대비": "vs. the same month last year", "자료 없음 — 충실도로 대신": "No data — using content quality instead", "도보": "Walk", "대중교통": "Transit", "걷기": "Walking", "실측": "Measured", "추정": "Estimated", "오늘의 앵커": "Today's anchor", "식사·카페": "Meal or cafe", "둘러볼 곳": "Something to see", "내 위치": "My location", "앵커": "anchor", "기상청 초단기실황": "KMA nowcast", "격자": "grid", "직선": "straight-line", "우회율": "detour factor", "평균 이동속도 기반 추정": "estimated from average travel speed", "보행자 경로": "pedestrian route", "도로망 보행 경로": "road-network walking route", "실시간 최적경로": "real-time optimal route", "직선거리 기반 추정": "estimated from straight-line distance", "직선거리 추정": "straight-line estimate", "환승": "transfers", "운영시간 확정": "hours confirmed", "미설정 — 기본값": "not set — falling back to a default", "으로 판정합니다": " for the check", "기본값": "Default", "설명 상세": "detailed description", "태그 다수": "many tags", "무장애 정보": "accessibility info", "근처에 지금 열린 행사가 없어 상시 콘텐츠로 시작합니다.": "No events are running nearby, so the plan starts from always-open places.", "실내라 날씨의 영향을 받지 않습니다.": "Indoors — the weather does not affect it.", "라 날씨의 영향을 받지 않습니다.": " — weather does not affect it.", "날씨가 바뀌면 여기로 피할 수 있습니다.": "A fallback if the weather turns.", "일정 사이에 쉬어 가기 좋습니다.": "A good pause between stops.", "시각 패턴을 찾지 못함": "No time pattern found", "걸어서": "walk", "태그 다섯 개 넘음": "over five tags", "무장애 정보 있음": "accessibility info", "홈페이지": "website", "시간 확정": "hours confirmed", "설명이 긴 편": "long description", "위키백과": "Wikipedia", "보행자도로": "footpath", "도착": "arrive", "수도권": "Seoul metro", "본문으로 건너뛰기": "Skip to content", "웨더핏 서울": "WeatherFit Seoul", "뜨고 있지만 아직 붐비지 않는 곳": "Rising, but not yet crowded", "실외 주의": "Outdoors risky", "🍚 음식": "🍚 Food", "🎨 문화": "🎨 Culture", "🏯 역사": "🏯 History", "🌳 자연": "🌳 Nature", "🛍 쇼핑": "🛍 Shopping", "✋ 체험": "✋ Hands-on", "🏘 동네 살아보기": "🏘 Live like a local", "🎭 공연·전시": "🎭 Arts & shows", "🌿 쉬러 왔어요": "🌿 Here to rest", "🌙 분위기 좋은 곳": "🌙 Atmosphere", "🧭 편하고 안전하게": "🧭 Easy and safe", "내 취향": "My taste", "체류": "Time at stops", "저장한 일정이 없습니다. 마음에 드는 일정을 만든 뒤 저장을 눌러 보세요.": "No saved plans yet. Build one you like, then press Save.", "지금 갈 수 있는": "you can visit now", "반경": "within", "이런 곳 더": "More like this", "다른 곳": "Swap", "관심없음": "Not interested", "출발": "leave", "머묾": "stay", "선정 근거": "Why this place", "왜 여기": "Why here", "가는 길": "Getting there", "분류": "Category", "주소": "Address", "교통": "Transit", "전화": "Phone", "요약": "Summary", "이용시간 원문": "Opening hours (original)", "휴무일 원문": "Closed days (original)", "닫기": "Close", "사용": "used", "중": "of", "이 판단의 근거": "Evidence behind the call", "웨더핏 도우미": "WeatherFit assistant", "지금 갈 수 있는 곳만 골라 드려요": "Only places you can actually visit now", "어디서 얼마나 시간이 있으신가요?": "Where are you, and how long do you have?", "AI 도우미": "Assistant", "문화관광": "Culture", "음식": "Food", "역사관광": "History", "축제/공연/행사": "Events", "쇼핑": "Shopping", "체험관광": "Hands-on", "자연관광": "Nature", "숙박": "Stay", "비짓서울 API 3,788": "Visit Seoul API 3,788", "을 전수 수집해 측정한 값입니다.": " measured across the full set.", "운영시간, 규칙만으로 어디까지": "Opening hours — how far rules alone get us", "확정 가능": "Determinable", "가정·예외": "Assumed or exception", "에 없는 필드": " — a field the API does not have", "불명": "Unknown", "기간이 있는 콘텐츠의 시의성": "Are dated items still current", "이미": "already", "필터": "Filter", "지금 가능": "Available now", "자치구 분포": "Distribution by district", "추천이 이 분포보다 고르면 관광 분산 효과가 있다고 봅니다.": "If recommendations spread wider than this, the service disperses visits.", "지금 여기서": "Here, for", "비 오는데 실내로": "It is raining — somewhere indoors", "아이랑 갈 만한 곳": "Somewhere good with kids", "더운데 시원한 데 없어요?": "It is hot — anywhere cool?", "갑자기 비 온대요": "Rain just started", "보관함": "Saved", "동네 방문 모멘텀": "Area visit momentum", "줄어듦": "Falling", "늘어남": "Rising", "여름 한낮 지표면온도": "Summer midday surface temperature", "지금 계신 곳에서 시작합니다": "We start from where you are", "걸어갈 수 있는 거리": "walking distance", "로 시작": "", "서울시청에서 시작": "Start from City Hall", "지금은 이른 시간이라 오늘 오전 10시 기준으로 잡았습니다.": "It is early, so the plan starts at 10:00 today.", "지금은 늦은 시간이라 내일 오전 10시 기준으로 잡았습니다.": "It is late, so the plan starts at 10:00 tomorrow.", "이 서비스가 하는 일": "What this service does", "를 판정하는 계층": " — a layer that checks it", "시각에 열려 있는가를 봅니다": " time is what we check", "판정 단계": "Check stages", "종료된 행사인가": "Has the event ended", "에 실외를 권할 수 있는가": " — can we suggest going outdoors", "시각에 문을 열어 두는가": " time, is it open", "남은 시간 안에 닿을 수 있는가": "Can you get there in the time left", "를 따로 두는 이유": " — why it is kept separate", "탈락과": "Excluded and", "를 구분": " are kept apart", "소요시간": "Travel time", "실측인지 추정인지": "measured or estimated", "데이터": "Data", "관광 콘텐츠 비짓서울 API · 행정동 경계 통계청 · 기상": "Tourism content Visit Seoul API · District boundaries KOSTAT · Weather", "지도": "Map", "지도를 표시할 수 없습니다": "The map cannot be shown", "이 브라우저에서 지도를 표시할 수 없습니다.": "This browser cannot show the map.", "일정과": "The plan and", "목록은 왼쪽에서 그대로 확인할 수 있습니다.": " list are still on the left.", "플랜 B": "Plan B", "인기": "Popular", "적당": "Fair", "둘러보기": "Look around", "전체": "All", "한국어": "한국어", "실내·실외 — API에 없는 필드": "Indoor/outdoor — a field the API does not have", "필터 통과": "Passing the filter", "이미 종료": "Already ended", "‘지금 갈 수 있는가’를 판정하는 계층": "a layer that checks whether you can actually go now", "에서 시작합니다. 주변에 지금 갈 수 있는 곳이": " is the starting point. Places you can visit now nearby:", "있습니다.": ".", "조건을 바꾸고 싶으면 말씀해 주세요.": "Tell me if you want to change anything.", "종로구": "Jongno-gu", "중구": "Jung-gu", "강남구": "Gangnam-gu", "용산구": "Yongsan-gu", "마포구": "Mapo-gu", "서초구": "Seocho-gu", "영등포구": "Yeongdeungpo-gu", "송파구": "Songpa-gu", "성동구": "Seongdong-gu", "서대문구": "Seodaemun-gu", "광진구": "Gwangjin-gu", "노원구": "Nowon-gu", "성북구": "Seongbuk-gu", "동대문구": "Dongdaemun-gu", "강서구": "Gangseo-gu", "은평구": "Eunpyeong-gu", "강북구": "Gangbuk-gu", "동작구": "Dongjak-gu", "관악구": "Gwanak-gu", "중랑구": "Jungnang-gu", "도봉구": "Dobong-gu", "강동구": "Gangdong-gu", "양천구": "Yangcheon-gu", "구로구": "Guro-gu", "금천구": "Geumcheon-gu", "규칙만으로 확정되는 건": "Rules alone determine", "나머지가 LLM 정규화의 몫입니다.": "the rest is left to LLM normalisation.", "날씨 대응의 전제인데": "This is the premise for weather handling, and", "가 규칙으로 안 가려집니다.": " cannot be resolved by rules.", "지정": "dated", "이 끝난 행사입니다.": " have already ended."};
const KO2EN_RE = new RegExp(Object.keys(KO2EN)
  .sort((a, b) => b.length - a.length)
  .map(k => { const e = k.replace(/[.*+?^${}()|[\]\\\\\/-]/g, m => '\\' + m);
              return k.length <= 3 ? `(?<![가-힣])${e}(?![가-힣])` : e; })
  .join('|'), 'g');
const KO_UNITS = [[/([\d,]+)\s*회/g, '$1 views'], [/([\d,]+)\s*분/g, '$1 min'],
  [/([\d,]+)\s*곳/g, '$1 places'], [/([\d,]+)\s*구간/g, '$1 legs'],
  [/([\d,]+)\s*시간/g, '$1 hrs'], [/([\d,]+)\s*개월/g, '$1 months'],
  [/([\d,]+)\s*건/g, '$1 items'], [/([\d,]+)\s*정거장/g, '$1 stops']];

function tx(s) {
  if (S.lang !== 'en' || !s) return s;
  let out = s;
  KO_UNITS.forEach(([re, rep]) => { out = out.replace(re, rep); });
  return out.replace(KO2EN_RE, m => KO2EN[m]);
}

/* 렌더가 끝난 뒤 한 번 훑는다. 장소 이름은 서버가 이미 영어로 주므로
   건드리지 않는다 — 못 옮긴 4%는 그대로 두는 편이 맞다. */
const TX_SKIP = new Set(['ai-log']);   // 도우미 답변은 모델이 영어로 쓴다
function sweepKorean(root) {
  if (S.lang !== 'en') return;
  // title 속성 안의 말은 텍스트 노드가 아니라 워커가 못 본다. 배지의
  // 라벨과 툴팁이 거기 있어서 화면에는 남아 보인다.
  (root || document.body).querySelectorAll('[title]').forEach(el => {
    if (/[가-힣]/.test(el.title)) el.title = tx(el.title);
  });
  const w = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT);
  const jobs = [];
  while (w.nextNode()) {
    const n = w.currentNode;
    if (!/[가-힣]/.test(n.nodeValue)) continue;
    const host = n.parentElement;
    if (!host || host.closest('.t') || TX_SKIP.has((host.closest('[id]') || {}).id))
      continue;
    jobs.push(n);
  }
  jobs.forEach(n => { n.nodeValue = tx(n.nodeValue); });
}

function applyChrome() {
  document.querySelectorAll('[data-en]').forEach(el => {
    if (!el.dataset.ko) el.dataset.ko = el.textContent.trim();
    el.textContent = S.lang === 'en' ? el.dataset.en : el.dataset.ko;
  });
  document.documentElement.lang = S.lang;
  sweepKorean();
}

async function syncLanguages() {
  // 아직 수집하지 않은 어권은 눌러도 한국어가 나온다. 미리 잠가 둔다.
  try {
    const h = await getJSON('/api/health');
    S.langsReady = h.languages || ['ko'];
  } catch (e) { S.langsReady = ['ko']; }
  $$('#lang-seg button').forEach(b => {
    const ok = S.langsReady.includes(b.dataset.lang);
    b.disabled = !ok;
    b.title = ok ? '' : '이 언어는 아직 수집되지 않았습니다';
  });
  if (!S.langsReady.includes(S.lang)) {
    S.lang = 'ko';
    $$('#lang-seg button').forEach(x => x.classList.toggle('on', x.dataset.lang === 'ko'));
  }
}

function init() {
  loadTaste();
  try {
    const saved = localStorage.getItem(LS_LANG);
    if (saved) {
      S.lang = saved;
      $$('#lang-seg button').forEach(b =>
        b.classList.toggle('on', b.dataset.lang === saved));
    }
  } catch (e) { /* 무시 */ }
  bindUI();
  renderVault();
  syncLanguages();
  applyChrome();
  loadStyles();
  syncControls();
  if (readUrlState()) {
    applyUrlOptions();
    $('#geo-overlay').hidden = true;
    start(false);
  }
  // 지난번 위치를 기억해 두면 재방문이 매끄럽다
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
    if (saved?.lat) {
      S.lat = saved.lat; S.lon = saved.lon; S.precise = !!saved.precise;
      geoNote('지난번 위치가 기억되어 있습니다.');
    }
  } catch (e) { /* 무시 */ }
}

/* 서비스 워커 — 로밍 중에 앱 껍데기가 안 열리면 보관함의 일정도 못 본다.
   등록에 실패해도 앱은 그대로 돌아야 하므로 조용히 넘어간다. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}

/* 연결이 끊기면 알린다. 판정은 캐시하지 않으므로 새 일정은 못 짜지만,
   보관함에 저장해 둔 일정은 그대로 볼 수 있다. */
function watchConnection() {
  const tell = () => {
    const off = !navigator.onLine;
    $('#offline-bar').hidden = !off;
    document.body.classList.toggle('is-offline', off);
  };
  window.addEventListener('online', tell);
  window.addEventListener('offline', tell);
  tell();
}

let booted = false;
function bootOnce() { if (!booted) { booted = true; init(); watchConnection(); } }
document.addEventListener('DOMContentLoaded', bootOnce);
if (document.readyState !== 'loading') bootOnce();
