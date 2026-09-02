# 웨더핏 서울 (WeatherFit Seoul)

오늘 날씨에, 지금 열려 있는 곳만 골라 짜는 AI 서울 코스.
2026 서울관광재단 비짓서울 API 데이터·AI 활용 아이디어 공모전 출품작.

## 이 저장소의 목적

공모전 제출물은 3장짜리 문서이고 데모 심사가 없다. 따라서 이 코드는 제품이 아니라
**제안서의 주장을 실측으로 뒷받침하기 위한 것**이다. 구체적으로 두 가지를 증명한다.

1. 비짓서울 API의 운영정보(이용시간·휴무일)가 실제로 얼마나 비정형인가 —
   규칙 기반으로 몇 %가 파싱되고, 몇 %가 LLM 없이는 판정 불가인가
2. 시간 유효성 필터를 씌우면 후보군이 실제로 얼마나 줄어드는가

## 파이프라인

```
collect  →  normalize  →  validate  →  report
수집         정규화          유효성 판정      근거 수치
```

| 단계 | 하는 일 | 산출물 |
|---|---|---|
| `collect` | 콘텐츠 목록/상세 수집 | `data/raw/*.json` |
| `normalize` | 이용시간·휴무일 자유 문장 → 구조화 시간표, 실내/실외 태깅 | `data/normalized.json` |
| `validate` | 현재 시각·날씨 기준 "지금 갈 수 있는가" 판정 | — |
| `report` | 제안서에 쓸 수치 집계 | `data/report.md` |

## 데이터 소스

공식 API(`api-call.visitseoul.net`)는 `VISITSEOUL-API-KEY` 헤더를 요구한다.
키 발급 전까지는 공개 카탈로그(`api.visitseoul.net/contents/standard`)를 소스로 쓴다.
두 소스는 `sources/` 아래에서 같은 인터페이스를 구현하므로 키가 생기면 교체만 하면 된다.

```bash
# 키 없이 (공개 카탈로그)
python -m weatherfit.collect --source catalog --category 축제

# 키 발급 후
VISITSEOUL_API_KEY=... python -m weatherfit.collect --source api
```

## 사용법

```bash
python -m weatherfit.collect --category 축제      # 수집 (중단 후 재실행하면 이어받음)
python -m weatherfit.collect --all
python -m weatherfit.report --at "2026-09-02 14:00"
python -m weatherfit.report --rain                # 우천 시나리오로 재판정
```

## 상태

- [x] 카테고리별·어권별 건수 실측 (제안서 [도표 1]의 근거)
- [x] 콘텐츠 수집기 (`collect`) — 공개 카탈로그
- [x] 운영정보 정규화 + 신뢰도 측정 (`normalize`)
- [x] 유효성 필터 (`validate`)
- [x] 근거 리포트 생성 (`report`)
- [ ] 전체 카테고리 수집 완료
- [ ] LLM 정규화 어댑터 — 규칙이 못 푸는 구간 담당
- [ ] 공식 API 소스 실검증 (키 발급 대기)

## 지금까지 나온 수치 (축제 338건 표본)

| 항목 | 값 | 의미 |
|---|---:|---|
| 운영시간이 규칙만으로 확정 | **6.8%** | 나머지 93.2%가 LLM의 몫 |
| 실내·실외 판정 불가 | **50.9%** | 날씨 대응의 전제가 비어 있음 |
| 이미 종료된 행사 | **97.3%** | 목록 API에는 기간 필드가 없음 |
| 필터 통과 (9/2 14시, 맑음) | **338 → 6건** | 유효성 레이어의 효과 |

전량 수집이 끝나면 `report`를 다시 돌려 최종 수치를 확정한다.
