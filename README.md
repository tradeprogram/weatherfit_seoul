# 웨더핏 서울 (WeatherFit Seoul)

**오늘 날씨에, 지금 열려 있는 곳만 골라 짜는 AI 서울 코스**

2026 서울관광재단 비짓서울 API 데이터·AI 활용 아이디어 공모전 출품작.

서울을 찾은 외국인 관광객이 겪는 가장 흔한 실패는 코스가 시시한 것이 아니라
**갔는데 없는 것**이다. 이미 끝난 축제, 오늘 문을 닫은 가게, 비가 와서 못 하게
된 야외 일정. 웨더핏 서울은 비짓서울 API의 관광 콘텐츠에 *지금 갈 수 있는가*를
판정하는 계층을 얹어, 통과한 것만으로 반나절 코스를 만든다.

## 빠른 시작

```bash
pip install -r requirements.txt

# 1. 콘텐츠 수집 (키 없이 공개 카탈로그에서, 약 15분)
python -m weatherfit.collect --all

# 2. 행정동 경계 생성 (통계청 shapefile 필요)
python -c "from weatherfit.geo import build_seoul_geojson as b; b(r'경로/BND_ADM_DONG_PG.shp')"

# 3. 서버
python -m weatherfit.server        # http://127.0.0.1:8020
```

근거 수치만 뽑고 싶다면:

```bash
python -m weatherfit.report --at "2026-09-02 14:00"
python -m weatherfit.report --rain          # 우천 시나리오로 재판정
```

## API 키

**셋 다 없어도 전부 동작한다.** 없으면 규칙·기본값으로 떨어지고, 무엇이 쓰였는지는
`/api/health`와 화면 상단에 항상 표시된다. `.env.example` 참고.

| 변수 | 없을 때 |
|---|---|
| `VISITSEOUL_API_KEY` | 공개 카탈로그를 소스로 수집 |
| `KMA_API_KEY` | 맑음 21°C 기본값으로 판정 |
| `TMAP_APP_KEY` | 도보 시간을 직선거리 × 1.3으로 추정 |
| `ODSAY_API_KEY` | 대중교통 시간을 평균속도로 추정 |
| `NAVER_CLIENT_ID` / `SECRET` | 자동차 시간을 직선거리로 추정 |
| `ANTHROPIC_API_KEY` | 규칙 기반 의도 파악·템플릿 답변 |

각 구간의 소요시간에는 **실측인지 추정인지**가 함께 표시된다.

## 이 저장소가 증명하는 것

제출물은 3장짜리 문서이고 데모 심사가 없다. 그래서 이 코드는 제품이 아니라
**제안서의 주장을 실측으로 뒷받침하기 위한 것**이다.

전수 3,788건(8개 카테고리 전부)을 수집해 측정한 값이다.

| 항목 | 측정값 | 뜻 |
|---|---:|---|
| 운영시간이 규칙만으로 확정 | **21.7%** (821건) | 나머지 78.3%가 LLM 정규화의 몫 |
| └ 그중 요일 표기가 아예 없음 | **45.7%** | '매일'은 우리 가정이지 원문의 진술이 아니다 |
| 실내·실외 판정 불가 | **24.8%** (940건) | 날씨 대응의 전제가 비어 있음 |
| 축제·행사 중 이미 종료 | **99.2%** (1,136/1,145) | 목록 API에는 기간 필드가 없다 |
| 필터 통과 (수 14시·맑음) | 3,788 → **1,900건** | 비가 오면 1,511건까지 줄어든다 |

`python -m weatherfit.report --at "2026-09-02 14:00"` 한 줄로 재현된다.

## 구조

```
weatherfit/
  models.py      Content 도메인 모델, 카테고리 코드
  sources/       catalog(공개 HTML) · api(공식 API) — 같은 인터페이스
  collect.py     수집 CLI. cid 기준으로 재개 가능
  normalize.py   자유 문장 운영시간 → 구조화 시간표, 실내외 태깅
  validate.py    기간·날씨·운영 판정. 탈락과 판정불가를 구분
  routing.py     TMAP 보행 · ODsay 대중교통 · 네이버 자동차 어댑터
  course.py      종료 임박 행사를 앵커로 도보권 코스 구성
  chat.py        대화형 의도 추출과 답변 생성
  weather.py     기상청 초단기실황 (격자 변환 포함)
  llm.py         LLM 호출 어댑터. 없으면 규칙으로 폴백
  geo.py         행정동 경계 추출·좌표 매칭
  report.py      근거 수치 리포트
  server.py      FastAPI + 정적 파일
web/
  index.html     지도 위에 유리판을 얹은 단일 화면
  app.js         Leaflet, 대화·코스·후보·근거
  style.css      밝은 낮의 서울 — 글라스모피즘
  vendor/        Leaflet (CDN 의존을 없애기 위해 동봉)
```

자세한 설계 판단은 [ARCHITECTURE.md](ARCHITECTURE.md).

## 엔드포인트

| 경로 | 하는 일 |
|---|---|
| `GET /api/health` | 적재 건수, 키 보유 여부 |
| `GET /api/weather` | 현재 기상 (`mode=auto\|clear\|rain\|heat`) |
| `GET /api/candidates` | 판정 결과가 붙은 후보 목록 |
| `GET /api/course` | 반나절 코스 (`explain=true`면 LLM 설명) |
| `GET /api/stats` | 근거 수치 + 자치구 분포 |
| `POST /api/chat` | 대화 한 턴 → 답변 + 코스 + 의도 |
| `GET /api/routing` | 두 지점 사이 도보·대중교통 소요시간 |

## 대화

챗봇은 **말을 알아듣고 말을 만드는 데만** LLM을 쓴다. 무엇을 추천할지는 판정
엔진이 정한다. LLM이 장소를 지어내면 "갔는데 없더라"가 다시 시작되기 때문이다.

```
사용자: 비 오는데 홍대에서 3시간
        ↓ 의도 추출 (LLM 또는 규칙)
        {area:"홍대", weather_mode:"rain", hours:3}
        ↓ 판정 엔진 — 실제 콘텐츠에서만 고른다
        코스 4곳
        ↓ 답변 생성 (LLM 또는 템플릿)
챗봇:   홍대 기준으로 지금 갈 수 있는 곳만 골랐습니다…
```

앞 턴의 조건을 물려받아 "밥 먹을 곳 위주로", "강남으로 옮길래" 같은 이어지는
요청이 성립한다.

## 데이터 출처

관광 콘텐츠 [비짓서울 API](https://api.visitseoul.net) · 행정동 경계 통계청
`BND_ADM_DONG_PG` · 기상 기상청 초단기실황 · 경로 TMAP / ODsay / 네이버 ·
지도 배경 OpenStreetMap
