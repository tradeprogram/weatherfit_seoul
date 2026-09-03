# 배포

프런트는 Vercel, API는 Render. 코드는 그대로 올라간다.

```
브라우저 → weatherfit-seoul.vercel.app  (정적: HTML·JS·CSS·행정동 경계)
              └ /api/*  →  weatherfit-seoul-api.onrender.com  (FastAPI)
```

## 왜 나눴나

우리 런타임은 geopandas·shapely·pyproj까지 **162MB**인데 Vercel의 Python
함수 한도는 250MB다. 아슬아슬한 것보다 더 중요한 건, **서버리스는 요청마다
새로 뜬다**는 점이다. 우리는 시작할 때 3,788건을 인덱싱해 두고(약 1.6초)
경로·인기도·구간 캐시를 메모리에 얹어 쓴다. 그 캐시가 성능의 전부다 —
캐시가 살아 있으면 일정 하나에 67ms, 없으면 0.9초다. 상주 프로세스가 맞다.

정적 파일은 반대다. 서버가 붙들고 있을 이유가 없고, CDN에서 나가는 게 빠르다.

## 1. API — Render

저장소에 `render.yaml`이 있으므로 Blueprint로 올리면 끝난다.

1. [render.com](https://render.com) → **New** → **Blueprint**
2. `tradeprogram/weatherfit_seoul` 선택 → `render.yaml`을 자동으로 읽는다
3. 배포 후 **Environment**에 키를 넣는다 (없어도 서비스는 완전히 돈다)

| 키 | 없으면 |
|---|---|
| `KMA_API_KEY` | 맑음 21°C 기본값 |
| `GEMINI_API_KEY` | 규칙 기반 답변 |
| `VISITSEOUL_API_KEY` | 저장소에 든 수집본 사용 |
| `ODSAY_API_KEY` | 대중교통 시간 추정 |
| `TMAP_APP_KEY` | 공개 OSRM으로 도보 실측 |

서비스 이름을 바꾸면 주소도 바뀌므로 `vercel.json`의 rewrite 대상도 같이
고쳐야 한다.

**무료 티어는 15분 놀면 잠든다.** 다시 깨는 데 50초쯤 걸린다. 심사 기간에
데모를 돌리실 거면 유료 인스턴스로 올리거나, 외부 헬스체크(UptimeRobot 등)로
`/api/health`를 10분마다 찔러 깨워 두는 편이 낫다.

## 2. 프런트 — Vercel

1. [vercel.com](https://vercel.com) → **Add New** → **Project** → 같은 저장소
2. 프로젝트 이름을 **`weatherfit-seoul`**로 (그대로 주소가 된다)
3. Framework Preset **Other**, Build Command **비움**, Output Directory **`web`**
4. Deploy

`vercel.json`이 나머지를 한다 — `/api/*`를 Render로 넘기고(그래서 CORS 설정이
필요 없다), HTML·JS·CSS는 캐시하지 않고, Leaflet과 행정동 경계는 오래 캐시한다.

## 데이터

수집 원본은 `data/raw/*.jsonl.gz`로 저장소에 들어 있다. 19.2MB가 5.0MB로
줄어 넣을 만해졌고, 그래야 서버가 콘텐츠를 가진 채로 뜬다. 다시 수집하려면
`python -m weatherfit.collect --all`을 돌리면 되고, 압축본보다 새 원본이
우선한다.

위성 열지도(`data/dong_thermal.json`)와 인기도(`data/popularity.json`)도
결과가 저장소에 있어 배포본에서 다시 계산하지 않는다. 근거를 재현하려면
로컬에서 `python -m weatherfit.remote build` / `popularity build`를 돌린다.

## 확인

```bash
curl https://weatherfit-seoul-api.onrender.com/api/health
```

`items`가 3,788이고 `keys`에 무엇이 켜져 있는지가 그대로 나온다. 화면
오른쪽 위에도 같은 내용이 표시된다 — 무엇이 실측이고 무엇이 추정인지
숨기지 않는 것이 이 서비스의 원칙이다.
