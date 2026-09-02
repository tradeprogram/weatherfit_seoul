/* 서비스 워커 — 로밍 중에도 앱이 열리게.

   관광객은 데이터가 끊기는 환경에서 쓴다. 지하철, 로밍 한도 초과,
   외국인 관광객이 흔히 쓰는 공용 와이파이의 사각지대. 앱 껍데기가
   그때 안 열리면 보관함에 저장해 둔 일정도 못 본다.

   전략을 셋으로 나눈다.

     껍데기(HTML·CSS·JS·지도 라이브러리)  네트워크 우선, 실패하면 캐시.
       개발 중에 옛 파일이 눌러앉으면 안 되므로 캐시 우선은 쓰지 않는다.
     지도 타일                            캐시 우선, 상한 300장.
       이미 받은 것만 다시 쓴다. OSM 타일 서버에 부담을 더하지 않는다.
     /api/*                               네트워크만. 오래된 판정은 위험하다.
       "지금 열려 있는가"에 어제 답을 주면 이 앱의 존재 이유가 사라진다.
*/
const VERSION = 'weatherfit-v1';
const SHELL = `${VERSION}-shell`;
const TILES = `${VERSION}-tiles`;
const TILE_MAX = 300;

const PRECACHE = [
  './',
  'index.html',
  'style.css',
  'app.js',
  'vendor/leaflet.css',
  'vendor/leaflet.js',
  'manifest.webmanifest',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL)
      // 하나가 없어도 설치는 되게 한다. 아이콘 하나 때문에 전체가 실패하면
      // 오프라인 지원이 통째로 사라진다.
      .then(c => Promise.allSettled(PRECACHE.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => !k.startsWith(VERSION)).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // 판정 결과는 캐시하지 않는다 — 어제의 '열려 있음'은 틀린 답이다
  if (url.pathname.startsWith('/api/')) return;

  if (url.origin !== self.location.origin) {
    if (/tile\.openstreetmap|tile\./.test(url.host)) e.respondWith(tile(req));
    return;                                   // 폰트 등은 브라우저에 맡긴다
  }
  e.respondWith(shell(req));
});

async function shell(req) {
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const c = await caches.open(SHELL);
      c.put(req, res.clone());
    }
    return res;
  } catch (err) {
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) return hit;
    if (req.mode === 'navigate') {
      const home = await caches.match('index.html', { ignoreSearch: true });
      if (home) return home;
    }
    throw err;
  }
}

async function tile(req) {
  const c = await caches.open(TILES);
  const hit = await c.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && res.ok) {
    c.put(req, res.clone());
    trim(c);
  }
  return res;
}

async function trim(cache) {
  const keys = await cache.keys();
  if (keys.length <= TILE_MAX) return;
  for (const k of keys.slice(0, keys.length - TILE_MAX)) await cache.delete(k);
}
