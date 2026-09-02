/* 웨더핏 서울 — 프런트엔드 */
'use strict';

const ORIGINS = [
  ['서울시청', 37.5665, 126.9780],
  ['홍대입구역', 37.5570, 126.9245],
  ['성수역', 37.5445, 127.0557],
  ['강남역', 37.4979, 127.0276],
  ['명동', 37.5636, 126.9827],
  ['익선동', 37.5732, 126.9905],
  ['이태원', 37.5346, 126.9946],
  ['여의도', 37.5215, 126.9243],
  ['잠실역', 37.5133, 127.1000],
];
const ROLE_COLOR = { anchor:'#f08a34', food:'#22a06b', shelter:'#7c62d8' };
const ROLE_NAME  = { anchor:'오늘의 행사', food:'로컬 음식', shelter:'실내 대안' };

const S = {
  mode:'auto', at:null, lat:37.5665, lon:126.9780,
  course:null, candidates:[], stats:null,
  catFilter:null, selected:null, showAll:false,
  history:[], intent:null, busy:false,
};

const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ───────────────────────── 지도 ─────────────────────────
   Leaflet. WebGL이 없는 환경에서도 그려진다.
   Leaflet 좌표는 [위도, 경도]로 GeoJSON과 반대다.               */

function showMapFallback(why) {
  const el = document.getElementById('map');
  if (!el || el.querySelector('.map-fallback')) return;
  const box = document.createElement('div');
  box.className = 'map-fallback';
  box.innerHTML = '<b>지도를 표시할 수 없습니다</b><span>' + why +
    ' 코스와 후보는 왼쪽에서 그대로 확인할 수 있습니다.</span>';
  el.appendChild(box);
}

let map = null, mapReady = false;
const layers = { dong:null, cands:null, route:null, steps:null };

try {
  map = L.map('map', { zoomControl:true, minZoom:10, maxZoom:17 })
         .setView([37.5665, 126.9780], 12);

  // OpenStreetMap 표준 타일. 키가 필요 없고 서울 전 축척에 데이터가 있다.
  // (Esri Light Gray는 국내 z14 이상에서 빈 타일을 준다)
  // 원본은 채도가 높아 유리판 위 글씨를 방해하므로 CSS에서 한 겹 눌러 준다.
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution:'&copy; OpenStreetMap contributors',
    maxZoom:18, className:'basemap',
  }).addTo(map);

  layers.cands = L.layerGroup();
  layers.route = L.layerGroup().addTo(map);
  layers.steps = L.layerGroup().addTo(map);
  mapReady = true;

  fetch('data/seoul_dong.geojson')
    .then(r => r.json())
    .then(gj => {
      layers.dong = L.geoJSON(gj, {
        style:{ color:'#1f7ac4', weight:0.6, opacity:0.26,
                fillColor:'#1f7ac4', fillOpacity:0.035 },
        interactive:false,
      }).addTo(map);
      if (layers.dong.bringToBack) layers.dong.bringToBack();
    })
    .catch(e => console.warn('행정동 경계를 불러오지 못했습니다', e));
} catch (e) {
  console.warn('지도를 만들지 못했습니다', e);
  map = null;
  showMapFallback('이 브라우저에서 지도를 표시할 수 없습니다.');
}

/* ───────────────────────── 데이터 ───────────────────────── */

function qs(extra = {}) {
  const p = new URLSearchParams({ lat:S.lat, lon:S.lon, mode:S.mode, ...extra });
  if (S.at) p.set('at', S.at);
  return p.toString();
}

async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function boot() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  $('#at').value = now.toISOString().slice(0, 16);
  $('#origin').innerHTML = ORIGINS
    .map(([n], i) => `<option value="${i}">${esc(n)}</option>`).join('');

  bindUI();
  greet();
  await refresh();
}

function greet() {
  pushMsg('bot',
    '안녕하세요. 지금 서울에서 **실제로 갈 수 있는 곳**만 골라 코스를 짜 드립니다.\n' +
    '어디서 얼마나 시간이 있으신지 알려주세요.');
}

async function refresh() {
  try {
    const [course, cands] = await Promise.all([
      getJSON('/api/course?' + qs()),
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
      `<div>백엔드에 연결하지 못했습니다. (${esc(e.message)})</div>`;
  }
  getJSON('/api/stats?' + qs())
    .then(s => { S.stats = s; renderEvidence(); })
    .catch(() => {});
}

/* ───────────────────────── 렌더 ───────────────────────── */

function renderWeather(w) {
  const icon = w.pty && w.pty !== '없음' ? '🌧'
             : w.temp_c >= 33 ? '🔥'
             : w.sky === '흐림' ? '☁'
             : w.sky === '구름많음' ? '⛅' : '☀';
  $('#wx-icon').textContent = icon;
  $('#wx-desc').textContent = w.desc || '';
  const src = { kma:'기상청 초단기실황', fallback:'기본값', manual:'수동 지정' }[w.source]
            || w.source || '';
  $('#wx-src').textContent = src + (w.note ? ' · ' + w.note : '');
  $('#wx-src').title = w.note || '';
}

function renderHeadStats(c) {
  const off = !c.weather.outdoor_ok;
  $('#head-stats').innerHTML = `
    <div class="stat"><span class="v">${c.count.toLocaleString()}</span>
      <span class="k">지금 갈 수 있는 곳</span></div>
    <div class="stat"><span class="v${off ? ' off' : ''}">${off ? '제외' : '가능'}</span>
      <span class="k">실외 활동</span></div>`;
}

function tripChip(step) {
  const tv = step.travel || {};
  const rec = tv.recommended;
  if (!rec) return '';
  const leg = tv[rec];
  if (!leg) return '';
  const icon = rec === 'walk' ? '🚶' : '🚇';
  const label = rec === 'walk' ? '도보' : '대중교통';
  const how = { tmap:'TMAP 보행경로', odsay:'ODsay 대중교통',
                naver:'네이버 경로', estimate:'직선거리 추정' }[leg.provider] || '';
  const detail = (rec === 'transit' && leg.summary) ? ` · ${esc(leg.summary)}` : '';
  return `<span class="trip${leg.exact ? '' : ' est'}" title="${esc(how)}">
    ${icon} ${label} ${leg.minutes}분${detail}
    <small>${leg.exact ? '실측' : '추정'}</small></span>`;
}

function renderCourse() {
  const c = S.course;
  const list = $('#course-list'), notes = $('#course-notes');
  if (!c || !c.steps.length) {
    list.innerHTML = '';
    notes.innerHTML =
      `<div>${esc((c && c.notes && c.notes[0]) || '조건에 맞는 코스를 찾지 못했습니다.')}</div>`;
    return;
  }
  list.innerHTML = c.steps.map((s, i) => `
    <li data-role="${s.role}" data-cid="${esc(s.cid)}"
        class="${S.selected === s.cid ? 'sel' : ''}">
      <span class="n">${i + 1}</span>
      <div class="t">${esc(s.title)}
        ${s.ends_today ? '<span class="badge today">오늘 마지막</span>' : ''}
        ${s.environment === 'indoor' ? '<span class="badge indoor">실내</span>' : ''}
      </div>
      <div class="m">${esc(s.category_path || s.category)} · ${ROLE_NAME[s.role] || ''}</div>
      ${tripChip(s)}
      <div class="l">${esc(s.line)}</div>
    </li>`).join('');

  notes.innerHTML = (c.notes || []).map(n => `<div>${esc(n)}</div>`).join('');
  list.querySelectorAll('li').forEach(li =>
    li.onclick = () => selectCid(li.dataset.cid));
}

function filtered() {
  return S.catFilter
    ? S.candidates.filter(c => c.category === S.catFilter)
    : S.candidates;
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
        <span class="n">약 ${c.walk_min}분</span>
        <span>${c.environment === 'indoor' ? '실내'
              : c.environment === 'outdoor' ? '실외' : '실내외 불명'}</span>
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
    <div class="ev-block">
      <h3>운영시간, 규칙만으로 어디까지</h3>
      ${bar('확정 가능', h.high || 0, t, 'ok')}
      ${bar('가정·예외', h.low || 0, t)}
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
      <p class="ev-note">기간 지정 ${s.dated.total.toLocaleString()}건 중
        <b>${s.dated.ended.toLocaleString()}건(${ended.toFixed(1)}%)</b>이 끝난 행사입니다.
        목록 조회 API에는 기간 필드가 없어 상세 조회 없이는 구분할 수 없습니다.</p>
    </div>
    <div class="ev-block">
      <h3>필터 통과</h3>
      ${bar('지금 가능', s.funnel.passed, t, 'ok')}
      <p class="ev-note">전체 ${t.toLocaleString()}건 →
        <b>${s.funnel.passed.toLocaleString()}건</b>.
        ${Object.entries(s.funnel.dropped)
          .map(([k, v]) => `${esc(k)} ${v.toLocaleString()}`).join(' · ')}</p>
    </div>
    ${dist.length ? `<div class="ev-block">
      <h3>자치구 분포 — 관광 분산의 기준선</h3>
      ${dist.map(([gu, n]) => bar(gu, n, maxDist)).join('')}
      <p class="ev-note">추천 결과가 이 분포보다 고르게 퍼지면 분산 효과가 있다고 봅니다.</p>
    </div>` : ''}`;
}

function renderDetail(c) {
  if (!c || !c.cid) return;
  $('#detail-panel').hidden = false;
  $('#detail-title').textContent = c.title || '';
  const vClass = c.verdict === '통과' ? '' : c.verdict === '판정불가' ? 'unknown' : 'fail';
  const envText = { indoor:'실내', outdoor:'실외', unknown:'실내외 불명' }[c.environment]
                || c.environment || '';
  const period = c.schedule_start
    ? `${esc(c.schedule_start)} ~ ${esc(c.schedule_end || '')}` : '';

  const tv = c.travel || {};
  const legs = ['walk', 'transit'].map(k => {
    const leg = tv[k];
    if (!leg) return '';
    const name = k === 'walk' ? '도보' : '대중교통';
    const how = { tmap:'TMAP 보행경로', odsay:'ODsay', naver:'네이버',
                  estimate:'직선거리 추정' }[leg.provider] || leg.provider;
    return `<div class="leg-row">
      <span>${name} <b>${leg.minutes}분</b>
        ${leg.distance_m ? `<span class="who">${leg.distance_m.toLocaleString()}m</span>` : ''}</span>
      <span class="who">${esc(leg.summary || how)}</span></div>`;
  }).join('');

  $('#detail').innerHTML = `
    <div class="verdict ${vClass}">
      <div><b>${esc(c.verdict || '통과')}</b>
        <small>${esc(c.reason || c.verdict_reason || '')}</small></div>
    </div>
    ${c.line ? `<div class="d-sec"><h4>왜 지금</h4><p>${esc(c.line)}</p></div>` : ''}
    ${legs ? `<div class="d-sec"><h4>앞 장소에서 오는 길</h4>${legs}</div>` : ''}
    ${c.summary ? `<div class="d-sec"><h4>요약</h4><p>${esc(c.summary)}</p></div>` : ''}
    <div class="d-sec"><h4>분류 · 환경</h4>
      <p>${esc(c.category_path || c.category || '')} · ${esc(envText)}</p></div>
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
      <p>${esc({ high:'확정 — 요일·시각 모두 명시',
                 low:'가정 포함 — 예외 단서 또는 요일 누락',
                 none:'판정 불가 — 시각 패턴 없음' }[c.hours_confidence] || '—')}</p></div>`;
}

/* ───────────────────────── 지도 그리기 ───────────────────────── */

function drawMap() {
  if (!mapReady) return;
  const steps = (S.course && S.course.steps || []).filter(s => s.lat && s.lon);

  layers.steps.clearLayers();
  layers.route.clearLayers();

  steps.forEach((s, i) => {
    const color = ROLE_COLOR[s.role] || '#1f7ac4';
    L.circleMarker([s.lat, s.lon], {
      radius:17, weight:0, fillColor:color, fillOpacity:0.15, interactive:false,
    }).addTo(layers.steps);
    L.marker([s.lat, s.lon], {
      icon:L.divIcon({
        className:'step-pin',
        html:`<span style="background:${color}">${i + 1}</span>`,
        iconSize:[26, 26], iconAnchor:[13, 13],
      }),
      title:s.title,
    }).addTo(layers.steps).on('click', () => selectCid(s.cid));
  });

  if (steps.length > 1) {
    L.polyline(steps.map(s => [s.lat, s.lon]), {
      color:'#1f7ac4', weight:2.5, opacity:0.55, dashArray:'7 6',
    }).addTo(layers.route);
  }

  layers.cands.clearLayers();
  if (S.showAll) {
    filtered().filter(c => c.lat && c.lon).forEach(c => {
      L.circleMarker([c.lat, c.lon], {
        radius:3.5, weight:1, color:'#fff', fillColor:'#8aa0b4', fillOpacity:0.9,
      }).addTo(layers.cands).on('click', () => selectCid(c.cid));
    });
  }

  if (steps.length) {
    // 패널이 자리를 잡기 전에 맞추면 엉뚱한 축척이 나온다
    map.invalidateSize({ animate:false });
    // 좌우 유리판이 지도를 덮고 있으므로, 그 폭만큼만 비켜서 맞춘다.
    const el = map.getContainer();
    const wide = el.clientWidth > 980;
    const left = wide ? (document.querySelector('.panel-left')?.offsetWidth || 0) + 40 : 24;
    const right = wide && !document.getElementById('detail-panel').hidden
      ? (document.querySelector('.panel-right')?.offsetWidth || 0) + 40 : 24;
    const top = wide ? 100 : 24;
    // 애니메이션을 켜면 이동이 중간에 끊겨 축척이 그대로 남는 경우가 있다
    map.fitBounds(L.latLngBounds(steps.map(s => [s.lat, s.lon])), {
      paddingTopLeft:[left, top], paddingBottomRight:[right, 60],
      maxZoom:16, animate:false,
    });
  }
}

function selectCid(cid) {
  S.selected = cid;
  const inCourse = (S.course && S.course.steps || []).find(s => s.cid === cid);
  const inList = S.candidates.find(c => c.cid === cid);
  renderDetail({ ...(inList || {}), ...(inCourse || {}) });
  $$('#course-list li, #cand-list li').forEach(li =>
    li.classList.toggle('sel', li.dataset.cid === cid));
  const p = inCourse || inList;
  if (mapReady && p && p.lat && p.lon) {
    map.setView([p.lat, p.lon], Math.max(map.getZoom(), 15), { animate:false });
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

function showTyping() {
  const el = document.createElement('div');
  el.className = 'typing';
  el.innerHTML = '<i></i><i></i><i></i>';
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
  const typing = showTyping();

  try {
    const r = await fetch('/api/chat', {
      method:'POST', headers:{ 'Content-Type':'application/json' },
      body:JSON.stringify({
        messages:S.history, lat:S.lat, lon:S.lon, at:S.at, intent:S.intent,
      }),
    });
    const data = await r.json();
    typing.remove();

    S.intent = data.intent;
    if (data.course) {
      S.course = data.course;
      if (data.course.weather) renderWeather(data.course.weather);
      renderCourse();
      drawMap();
    }
    const bubble = pushMsg('bot', data.reply);
    const it = data.intent || {};
    const bits = [
      it.area, it.hours ? `${it.hours}시간` : null,
      { rain:'비', heat:'폭염', clear:'맑음', auto:'실시간 날씨' }[it.weather_mode],
      (it.interests || []).join('·') || null,
    ].filter(Boolean);
    const engine = data.engine === 'llm' ? 'AI 응답' : '규칙 기반 응답';
    bubble.insertAdjacentHTML('beforeend',
      `<span class="meta">${esc(bits.join(' · '))} · ${engine}</span>`);

    S.history.push({ role:'assistant', content:data.reply });
    switchTab('course');
  } catch (e) {
    typing.remove();
    pushMsg('bot', '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    S.busy = false;
    $('#chat-send').disabled = false;
    $('#chat-input').focus();
  }
}

function switchTab(name) {
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  ['chat', 'course', 'list', 'evidence'].forEach(t =>
    $('#pane-' + t).hidden = t !== name);
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
      () => alert('위치를 가져오지 못했습니다. 목록에서 출발지를 골라 주세요.'));
  };

  $$('#tabs button').forEach(b => b.onclick = () => switchTab(b.dataset.tab));

  $('#chat-form').onsubmit = e => { e.preventDefault(); send(); };
  $$('#chips button').forEach(b => b.onclick = () => send(b.textContent));

  $('#btn-dong').onclick = e => {
    const on = e.currentTarget.classList.toggle('on');
    if (!mapReady || !layers.dong) return;
    if (on) { layers.dong.addTo(map); layers.dong.bringToBack(); }
    else map.removeLayer(layers.dong);
  };
  $('#btn-all').onclick = e => {
    S.showAll = e.currentTarget.classList.toggle('on');
    if (mapReady) {
      if (S.showAll) layers.cands.addTo(map); else map.removeLayer(layers.cands);
    }
    drawMap();
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

let booted = false;
function bootOnce() { if (!booted) { booted = true; boot(); } }
document.addEventListener('DOMContentLoaded', bootOnce);
if (document.readyState !== 'loading') bootOnce();
