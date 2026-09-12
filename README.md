# Global LEO Constellation Tracker V1.2.0

최근 동향 비교 표에서 7·30·90일 기간을 선택하면 위성망별 추적 순증감,
변화율, 실제 비교 시작·종료일, 관측 일수와 공백을 확인할 수 있습니다.
`/api/activity?days=30`은 같은 비교 결과를 제공합니다 (`days`: 1~90).
기간 시작 경계와 오늘을 모두 포함하므로 7일 비교의 기대 관측일은 8개입니다.
관측이 두 날짜 미만이거나 기간 중 집계 범위가 바뀌면 계산을 보류합니다.
첫 관측이 0이면 변화율은 표시하지 않습니다. 일부 기간의 증감을 전체 기간으로 환산하지 않습니다.

글로벌 저궤도 위성통신망의 관측 현황, 월별 추적 순증감, NORAD별 관측 이력,
발사 기록, 계획 변경과 출처를 관리하는 Flask 대시보드입니다.

## V1.2 변경 내용

- 빈 응답·필수 항목 누락·수량 급변을 검증하고 마지막 정상 관측값 보존
- NORAD ID 중복 제거, 궤도요소 범위·기준시각 검증
- 정상 / 저장값 사용 / 일부 실패 / 전체 실패와 위성망별 마지막 수집 성공 시각 표시
- 공급자 오류 이후 같은 실행에서 추가 요청 중단, 정상 수집 후 2시간 동안 저장값 사용
- 수집 실패 시에도 최신 계획·일정 반영
- 지표·대상 범위·기준일·원천 출처를 구분하는 공통 교차검증
- 대상 범위가 다른 구축률 계산 보류
- 기존 집계 스냅샷 기반 일별 추적 수 차트와 월별 순증감, 수집 공백 표시
- NORAD 번호/이름 검색, 페이지 이동, 최근 90일의 개별 관측 이력
- 발사 기록 수록 범위와 수록된 임무 기준 연간 발사 위성 수 API
- 월·분기·연도 단위 발사 일정을 원래 정밀도로 표시
- 화면 영역별 로딩·오류·재시도, 동일 지표를 사용하는 CSV/Excel 내보내기
- 데이터·서버·화면 로직 회귀 테스트 및 GitHub Actions 검사

## 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
python app.py
```

Windows에서는 가상환경 활성화에 `.venv\Scripts\activate`를 사용합니다.
브라우저에서 `http://127.0.0.1:5000`을 엽니다. `PORT` 환경변수로 포트를 변경할 수 있습니다.
Render 설정은 `render.yaml`에 유지되며 실행 명령은 `gunicorn app:app`입니다.

## 데이터 갱신과 V1.1 전환

```bash
python updater/update_data.py
```

기존 V1.1 집계값을 새 지표 구조로만 변환하거나 `plans.yaml`을 수정한 뒤 재계산하려면:

```bash
python updater/update_data.py --refresh-derived
```

이 옵션은 외부 호출, 관측 이력 생성, 수집 성공 시각 갱신을 하지 않습니다.
제공된 V1.2 `current.json`은 기존 실제 집계값을 새 정의로 변환한 자료입니다.
개별 NORAD 이력을 과거 합계에서 만들어내지 않습니다. 첫 정상 수집 이후 해당 화면에
개별 위성이 나타나고, 이후 수집부터 미수록·재등장 이력이 쌓입니다.

`current.json`의 `generated_at`은 마지막 갱신 실행 시각입니다. 실제 최신성은
각 행의 `observation.last_success_at`, `epoch_min`, `epoch_max`로 확인합니다.
UI는 마지막 수집 성공 후 48시간이 지나면 오래된 값으로 표시합니다.

### 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 정상, 저장값 사용, 수동 대상만 존재, 또는 파생값 재계산 완료 |
| 1 | 일부 위성망 수집 실패 |
| 2 | 모든 자동 수집 대상 갱신 실패 |

오류 발생 시 이전 관측값과 실패 상태를 저장합니다. GitHub Actions는 그 상태를
커밋한 뒤 실행을 실패로 표시합니다. 관측 실패를 정상 성공으로 감추지 않습니다.
디스크·설정 오류 등 프로그램 자체의 오류도 비정상 종료합니다.

## 지표 정의

| 필드 | 의미 |
|---|---|
| `tracked_in_orbit` | CelesTrak에서 수집·검증한 고유 NORAD ID 수. 미관측은 `null` |
| `tracking_year` | `tracked_launched_this_year`가 지칭하는 연도 |
| `tracked_launched_this_year` | 그 연도에 발사된 것으로 식별되는 **현재 추적체** 수. 연간 누적 발사 수가 아님 |
| `reference_count`, `reference_metric`, `reference_date` | 별도 사업자·정부 발표 수, 지표, 기준일 |
| `planned_satellites`, `plan_metric`, `plan_scope` | 계획·승인 수와 종류·대상 범위 |
| `deployment_pct` | 명시적으로 허용하고 분자·분모 범위가 같은 경우에만 산출. 그 외 `null` |
| `crosscheck` | 서버의 공통 비교 결과. 화면과 Excel이 함께 사용 |
| `observation` | 수집 상태, 마지막 시도/성공 시각, 오류, 관측 대상 집합 변화 |

누락값을 0으로 대체하지 않습니다. 카탈로그에서 새로 확인되거나 사라진 것은
발사·운용 개시·퇴역·재진입과 동일한 사건이 아닙니다. 최초 관측일도 발사일이 아닙니다.
고도는 평균운동으로 계산한 장반경에서 지구 적도반경을 뺀 근사값이며 순간 위치가 아닙니다.

V1.1의 `launched_this_year`는 `tracked_launched_this_year`와 `tracking_year`로
교체했습니다. 기존 API 소비자는 이 변경과 `tracked_in_orbit`의 `null`을 처리해야 합니다.
기존 상세 API의 `launches`, `changes`, `roadmap`, `sources` 배열은 유지되며,
보조 파일을 읽지 못하면 `section_errors`에 해당 영역을 기록합니다.

### 계획과 교차검증

`data/plans.yaml`에서 계획·기준 발표·비교 범위를 관리합니다.
`data/sources.json`의 `origin_id`는 정보의 원천입니다. 다른 URL이라는 이유로 독립 출처로
취급하지 않습니다. CelesTrak과 Space-Track GP는 `18sds_gp`로 같은 원천을 사용합니다.
원천을 확인하지 못한 자료에는 `null`을 사용합니다. `plan_source_id: null`인 기존 계획은
출처·기준일을 추가 검토하기 전까지 자동 비교 주장으로 만들지 않습니다.

일치 여부는 동일 지표·범위·일자·독립 원천의 정확한 수치끼리만 판단합니다.
근사값, 하한값, 날짜가 다른 자료, 같은 사업자의 반복 발표는 직접 일치 판정을 보류합니다.
여러 자료 중 일부만 비교 가능한 경우 이를 별도 표시합니다.

구축 비율을 허용하려면 아래 조건을 모두 충족해야 합니다.

```yaml
count_scope: initial_production
plan_scope: initial_production
progress_comparable: true
progress_metric: tracked
```

자동 수집기의 현재 범위는 `all_catalogued`입니다. 다른 범위를 사용하려면 먼저 수집기를
해당 범위의 위성만 집계하도록 구현해야 합니다. 설정 문자열만 바꾸어 비율을 활성화하지 마세요.
제공되는 7개 위성망의 비율은 범위 확인 전까지 보류합니다.

수량 급변 기본 기준은 이전 정상 수 대비 감소 `max(10기, 20%)`, 증가 `max(100기, 50%)`입니다.
정상적인 대규모 배치를 확인한 경우 원자료 검토 후 해당 계획의 `quality.max_drop_fraction`,
`quality.max_rise_fraction`을 조정할 수 있습니다. 오류 응답은 자동 재시도하지 않습니다.

[CelesTrak GP 형식·이용 지침](https://celestrak.org/NORAD/documentation/gp-data-formats.php)을
따릅니다. Space-Track 인증 수집기는 이번 버전에 포함하지 않습니다.

## 관측 이력과 월별 추세

- `data/catalogs/<id>.json`: 지금까지 관측한 ID별 첫/마지막 관측, 최근 요소, 최근 카탈로그 수록 여부
- `data/observations/YYYY-MM-DD/<id>.json.gz`: UTC 일자별 마지막 정상 수집의 개별 위성 목록
- `data/snapshots/YYYY-MM-DD.json`: 위성망별 집계 스냅샷

같은 날 재수집하면 일별 파일은 마지막 정상 결과로 교체합니다. 모든 실행을 남기는
고빈도 관측 저장소가 아닙니다. 성공한 관측만 개별 이력에 반영하고 수집 실패일을
위성 미수록일로 만들지 않습니다. 파일 저장은 임시 파일을 완성한 뒤 원자적으로 교체합니다.

월별 순증감은 해당 월의 마지막 유효 관측값에서 직전 월 마지막 유효 관측값을 뺍니다.
직전 관측값이 없으면 해당 월 첫 관측값을 기준으로 사용하고 일부 기간으로 표시합니다.
비교할 서로 다른 관측일이 없으면 순증감은 `null`입니다. 전월 말 기준점, 그 달 매일의
관측값, 월말 값이 모두 있는 지난달만 완전한 월로 표시합니다.
발표값→카탈로그 전환과 실패 시 유지된 값은 신규 추세 표본으로 사용하지 않습니다.

압축 관측 파일은 저장소에 누적됩니다. 장기 운용으로 용량이 커지면 별도 객체 저장소로
옮기는 것이 후속 과제입니다. 현재 API의 개별 위성 이력 조회는 최대 90일로 제한됩니다.

## 발사 기록

`data/launches.json`은 수동 검토한 임무 목록입니다. 월 단위 계획에는
`"date": "2026-12"`를 사용합니다. 분기는 `"2026-Q4"`, 연도는 `"2026"`로 표현합니다.
정렬용 기간 경계와 표시 일자를 분리해 임의의 1일을 발사일로 표시하지 않습니다.

`data/launch_coverage.json`에는 위성망별 `status`, `from`, `through`, `source_ids`, `note`를
기록합니다. `partial`은 일부 기록, `not_collected`는 완료 임무 미수록을 뜻합니다.
`complete_for_period`는 해당 기간의 전체 임무를 확인했을 때만 사용합니다.
제공 자료는 일부 임무 목록이며 이후 발사까지 포함한다고 보장하지 않습니다.
`/api/launch-coverage`의 `yearly`는 수록된 완료 임무만의 연간 위성 수입니다.

## API 및 다운로드

기존 경로 외에 다음을 제공합니다.

| 경로 | 내용 |
|---|---|
| `/api/quality` | 위성망별 수집 상태와 데이터 나이 |
| `/api/trends?constellation_id=starlink&months=12` | 일별 관측과 월별 순증감. 1~36개월 |
| `/api/objects/starlink?q=100000&page=1&per_page=25&presence=all` | NORAD/이름 검색. 페이지당 최대 100개 |
| `/api/objects/starlink/100000?days=90` | 개별 관측 이력. 최대 90일 |
| `/api/launch-coverage` | 발사 기록 범위와 수록 임무 기준 연간 집계 |
| `/download/trends.csv?constellation_id=starlink&months=12` | 선택된 위성망·기간의 추세 CSV |
| `/download/constellations.csv` | 지표·범위·수집 상태 포함 현황 CSV |
| `/download/tracker.xlsx` | 현황, 발사, 변경, 출처, 교차검증, 월별 추세, 수록 범위의 7개 시트 |
| `/health` | 프로세스 생존 확인 |
| `/ready` | 관측값 부재/노후 여부 확인. 데이터가 부족하면 503 |

Render의 health check는 `/health`를 유지합니다. 데이터 공급자 장애로 불필요하게
서버를 재시작하지 않도록 `/ready`와 역할을 분리했습니다.

## 검증과 자동 갱신

```bash
python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
node --check static/common.js
node --check static/app.js
node --check static/detail.js
```

테스트는 임시 데이터와 모의 응답을 사용해 외부 공급자를 호출하지 않습니다.
데이터 테스트 외에 현재 수록 데이터의 API·내보내기 계약도 확인합니다.
브라우저를 띄우는 테스트는 포함하지 않습니다.

`.github/workflows/test.yml`은 PR과 코드 변경을 검사합니다.
`.github/workflows/update-data.yml`은 UTC 21:00(한국시간 다음날 06:00) 일정으로 실행되며,
GitHub의 실제 시작 시각은 지연될 수 있습니다. 같은 브랜치의 수집 작업은 직렬 실행됩니다.
저장소 Actions에 `contents: write`가 필요합니다. 별도 Space-Track 계정이나 API 키는 필요하지 않습니다.
