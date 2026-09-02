/* 웨더핏 서울 — 프런트엔드
   위치에서 시작해 시간표가 있는 일정으로 끝난다. */
'use strict';

const CITY_HALL = [37.5665, 126.9780];
const ROLE_COLOR = { anchor:'#f08a34', food:'#22a06b', spot:'#1f7ac4', shelter:'#7c62d8' };
const ROLE_NAME  = { anchor:'앵커', food:'식사·카페', spot:'둘러보기', shelter:'플랜 B' };
const LS_KEY = 'weatherfit.origin';
const LS_TASTE = 'weatherfit.taste';
const LS_LANG = 'weatherfit.lang';

const S = {
  lat:CITY_HALL[0], lon:CITY_HALL[1], accuracy:null, where:null, precise:false,
  mode:'auto', hours:4, at:null,
  course:null, candidates:[], stats:null,
  catFilter:null, selected:null, showAll:false,
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
    layers.dong = L.geoJSON(gj, {
      style:{ color:'#1f7ac4', weight:0.6, opacity:0.24,
              fillColor:'#1f7ac4', fillOpacity:0.03 },
      interactive:false,
    }).addTo(map);
    layers.dong.bringToBack();
  }).catch(e => console.warn('행정동 경계를 불러오지 못했습니다', e));
} catch (e) {
  console.warn('지도를 만들지 못했습니다', e);
  map = null;
  showMapFallback('이 브라우저에서 지도를 표시할 수 없습니다.');
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

async function refresh() {
  setLoading(true);
  try {
    const [course, cands] = await Promise.all([
      postJSON('/api/plan', { lat:S.lat, lon:S.lon, mode:S.mode, at:S.at,
                              hours:S.hours, interests:S.interests,
                              taste:S.taste, lang:S.lang, exclude:S.exclude }),
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
  } catch (e) {
    $('#plan-notes').innerHTML =
      `<div>서버에 연결하지 못했습니다. (${esc(e.message)})</div>`;
  } finally {
    setLoading(false);
  }
  getJSON('/api/stats', { mode:S.mode, ...(S.at ? { at:S.at } : {}) })
    .then(s => { S.stats = s; renderEvidence(); }).catch(() => {});
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

function travelRow(step) {
  const leg = legOf(step);
  if (!leg || !leg.minutes) return '';
  const icon = leg.mode === 'walk' ? '🚶' : '🚇';
  const label = leg.mode === 'walk' ? '도보' : '대중교통';
  const how = { tmap:'TMAP 보행경로', odsay:'ODsay 대중교통',
                naver:'네이버 경로', estimate:'직선거리 추정' }[leg.provider] || '';
  const extra = leg.mode === 'transit' && leg.summary ? ` · ${esc(leg.summary)}` : '';
  return `<li class="leg" title="${esc(how)}">
    <span class="leg-line"></span>
    <span class="leg-txt">${icon} ${label} ${leg.minutes}분${extra}
      <em>${leg.exact ? '실측' : '추정'}</em></span></li>`;
}

function renderPlan() {
  const c = S.course;
  const head = $('#plan-head'), tl = $('#timeline');
  const notes = $('#plan-notes'), backup = $('#backup');

  if (!c || !c.steps.length) {
    head.innerHTML = '';
    tl.innerHTML = '';
    backup.innerHTML = '';
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

  tl.innerHTML = c.steps.map((s, i) => `
    ${travelRow(s)}
    <li class="stop${S.selected === s.cid ? ' sel' : ''}"
        data-role="${s.role}" data-cid="${esc(s.cid)}">
      <span class="tick">${i + 1}</span>
      <div class="when">${s.arrive}<small>${s.depart}</small></div>
      <div class="body">
        <div class="t">${esc(s.title)}
          ${s.ends_today ? '<span class="badge today">오늘 마지막</span>' : ''}</div>
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

  $$('#timeline .stop').forEach(li => li.onclick = e => {
    if (e.target.closest('.stop-acts')) return;
    selectCid(li.dataset.cid);
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
  return S.catFilter ? S.candidates.filter(c => c.category === S.catFilter)
                     : S.candidates;
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
  $('#list-summary').textContent = S.where
    ? `${S.where.label} 반경 2.5km · 지금 갈 수 있는 ${rows.length.toLocaleString()}곳`
    : `지금 갈 수 있는 ${rows.length.toLocaleString()}곳`;
  $('#cand-list').innerHTML = rows.slice(0, 120).map(c => `
    <li data-cid="${esc(c.cid)}" class="${S.selected === c.cid ? 'sel' : ''}">
      <div class="t">${esc(c.title)}</div>
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
  $$('#timeline .stop, #cand-list li').forEach(li =>
    li.classList.toggle('sel', li.dataset.cid === cid));
  const p = inPlan || inList;
  if (mapReady && p?.lat) {
    map.setView([p.lat, p.lon], Math.max(map.getZoom(), 16), { animate:false });
  }
}

/* ───────────────────────── 대화 ───────────────────────── */

function pushMsg(who, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + (who === 'me' ? 'me' : 'bot');
  el.innerHTML = esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  $('#chat-log').appendChild(el);
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
  return el;
}

async function send(text) {
  const msg = (text || $('#chat-input').value).trim();
  if (!msg || S.busy) return;
  S.busy = true;
  $('#chat-input').value = '';
  $('#chat-send').disabled = true;

  pushMsg('me', msg);
  S.history.push({ role:'user', content:msg });
  const typing = document.createElement('div');
  typing.className = 'typing';
  typing.innerHTML = '<i></i><i></i><i></i>';
  $('#chat-log').appendChild(typing);
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;

  try {
    const r = await fetch('/api/chat', {
      method:'POST', headers:{ 'Content-Type':'application/json' },
      body:JSON.stringify({ messages:S.history, lat:S.lat, lon:S.lon,
                            at:S.at, intent:S.intent, taste:S.taste,
                            lang:S.lang }),
    });
    const data = await r.json();
    typing.remove();
    S.intent = data.intent;
    if (data.taste) { S.taste = data.taste; saveTaste(); renderTaste(); }
    if (data.course) {
      S.course = data.course;
      if (data.course.weather) renderWeather(data.course.weather);
      renderPlan(); drawMap();
    }
    const bubble = pushMsg('bot', data.reply);
    const it = data.intent || {};
    const bits = [it.area, it.hours ? `${it.hours}시간` : null,
      { rain:'비', heat:'폭염', clear:'맑음', auto:'실시간 날씨' }[it.weather_mode],
      (it.interests || []).join('·') || null].filter(Boolean);
    bubble.insertAdjacentHTML('beforeend', `<span class="meta">${esc(bits.join(' · '))}
      · ${data.engine === 'llm' ? 'AI 응답' : '규칙 기반'}</span>`);
    S.history.push({ role:'assistant', content:data.reply });
    switchTab('plan');
  } catch (e) {
    typing.remove();
    pushMsg('bot', '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    S.busy = false;
    $('#chat-send').disabled = false;
  }
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
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  ['plan','chat','list','evidence'].forEach(t => $('#pane-' + t).hidden = t !== name);
}

/* ───────────────────────── 시작 ───────────────────────── */

async function start(usePrecise) {
  $('#geo-overlay').hidden = true;
  if (usePrecise) await locate();
  await refreshWhere();
  await refresh();
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
    refresh();
  });

  $$('#tabs button').forEach(b => b.onclick = () => switchTab(b.dataset.tab));

  $('#chat-form').onsubmit = e => { e.preventDefault(); send(); };
  $$('#chips button').forEach(b => b.onclick = () => { switchTab('chat'); send(b.textContent); });

  $('#btn-dong').onclick = e => {
    const on = e.currentTarget.classList.toggle('on');
    if (!mapReady || !layers.dong) return;
    if (on) { layers.dong.addTo(map); layers.dong.bringToBack(); }
    else map.removeLayer(layers.dong);
  };
  $('#btn-all').onclick = e => {
    S.showAll = e.currentTarget.classList.toggle('on');
    if (mapReady) S.showAll ? layers.cands.addTo(map) : map.removeLayer(layers.cands);
    drawMap();
  };
  $('#btn-me').onclick = () => {
    if (mapReady) map.setView([S.lat, S.lon], 15, { animate:false });
  };

  $('#detail-close').onclick = () => { $('#detail-panel').hidden = true; };
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
  syncLanguages();
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

let booted = false;
function bootOnce() { if (!booted) { booted = true; init(); } }
document.addEventListener('DOMContentLoaded', bootOnce);
if (document.readyState !== 'loading') bootOnce();
