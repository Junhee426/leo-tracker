# 서비스 확장 검토 (2026-09-13)

## 1. 측정한 문제: 매일 커밋되는 관측 파일의 무한 누적

`updater/update_data.py`는 GitHub Actions cron(`.github/workflows/update-data.yml`,
매일 UTC 21:00, `contents: write`)으로 실행되며 매 회 다음을 모두 커밋합니다.

- `data/snapshots/YYYY-MM-DD.json` — 집계 스냅샷 1개/일 (현재 저장소 기준 18개 파일, 224KB → 파일당 약 12KB)
- `data/observations/YYYY-MM-DD/<constellation>.json.gz` — CelesTrak 수집이 `fresh`로 성공한
  위성망마다 그날의 전체 궤도요소 원자료를 압축 저장 (`tracker/history.py:record_observations`)

이 저장소를 clone한 시점에는 `data/observations/`, `data/catalogs/`가 아직 한 번도
커밋되지 않은 상태였습니다(git log에 해당 경로 커밋 0건, 모든 위성망의
`observation.status`가 `legacy`). 즉 실제 CelesTrak `fresh` 수집이 아직 발생하지
않아 문제가 "아직" 리포지토리 크기로는 드러나지 않았을 뿐, 코드 경로는 이미
매일 파일을 계속 추가하도록 완전히 연결되어 있었습니다 — README가 지적한
"압축 관측 파일은 저장소에 누적됩니다"는 잠재 위험이 아니라 예정된 사실입니다.

### 증가율 추정

현재 `data/current.json`의 실측 추적 수(2026-09-11 기준): Starlink 11,130 /
OneWeb 651 / Kuiper 391 / 궈왕 199 / 첸판 238 = 총 12,609개 객체.
`plans.yaml`의 계획치까지 성장하면 Starlink 15,000 + OneWeb 648 + Kuiper 3,236 +
궈왕 13,000 + 첸판 15,000 ≈ 46,900개 객체.

실제 레코드 스키마(`norad_cat_id`, `object_name`, `object_id`, `epoch`,
`altitude_km`, `inclination_deg`)로 동일 규모 더미 데이터를 만들어
`gzip.compress`로 직접 측정한 결과:

| 시점 | 총 객체 수 | 1일치 gzip 합계 | 연간 누적(gzip) |
|---|---|---|---|
| 현재 규모 | 12,609 | 약 160KB | 약 58MB/년 |
| 계획 완료 규모(선형 추정) | 46,900 | 약 600KB | 약 220MB/년 |

여기에 위성망이 추가되거나(계획 파일 상 5개 CelesTrak 그룹 외에도 lightspeed,
iris2 등 수동 대상이 향후 그룹을 얻을 수 있음) 재시도·부분 실패로 특정 날짜가
빠지지 않는 한 이 증가는 매일, 무기한 반복됩니다. `data/snapshots/`(집계본)는
파일당 수 KB 수준이라 상대적으로 부담이 작고, 실제 압박은 `data/observations/`의
원자료 아카이브에 집중됩니다.

## 2. 코드 확인: 이미 90일 이상은 아무도 읽지 않는다

- `tracker/history.py:object_history()` — `data/observations/`를 읽는 유일한
  소비자이며, `days` 인자로 조회 범위를 제한합니다.
- `app.py`의 `/api/.../history` 라우트는 `bounded_int("days", 90, 1, 90)`로
  이 값을 **최대 90일**로 강제 클램프합니다(README에도 "개별 NORAD 이력 API는
  90일로 제한"이라 명시).
- 월별 순증감(`trend_data`)과 최근 활동(`recent_activity`)은 `data/snapshots/`
  (집계 스냅샷)만 읽고 `data/observations/`는 전혀 참조하지 않습니다.
- `data/catalogs/<id>.json`(`record_observations`가 갱신)은 각 객체의
  `first_seen_at`/`last_seen_at`/`present`/`missing_since`를 **영구** 보관하므로,
  일별 원자료 아카이브가 없어져도 "언제 처음/마지막으로 보였는가"라는 핵심
  이력은 사라지지 않습니다.

즉 90일(+여유분)보다 오래된 `data/observations/<date>/` 폴더는 어떤 API도
읽지 않는 죽은 데이터였습니다. 이는 리스크가 낮고 근거가 분명한 정리
대상입니다.

## 3. 변경 사항

**`tracker/history.py`**
- `MAX_HISTORY_DAYS = 90`, `OBSERVATION_RETENTION_DAYS = 100`(90일 API 한도 +
  10일 여유) 상수를 추가하고, `object_history()`의 기본값을 하드코딩된 `90`에서
  `MAX_HISTORY_DAYS`로 교체해 두 값이 어긋나지 않게 했습니다.
- `prune_observations(data_dir, now=None, retention_days=OBSERVATION_RETENTION_DAYS)`
  함수를 추가했습니다. `data/observations/` 아래 `YYYY-MM-DD` 형식 폴더 중
  `cutoff = now.date() - retention_days`보다 오래된 폴더만 `shutil.rmtree`로
  삭제합니다. 날짜 형식이 아닌 항목(폴더/파일 무관)은 건드리지 않고, 디렉터리
  자체가 없으면 조용히 빈 리스트를 반환합니다. 삭제된 폴더명 목록을 반환해
  테스트와 향후 로깅에 사용할 수 있게 했습니다.

**`updater/update_data.py`**
- `update()`가 실제 수집 실행(스냅샷·변경 이력을 저장하는 `refresh_derived`가
  아닌 경로)에서 스냅샷 저장 직후 `prune_observations(data_dir, now)`를 한 번
  호출하도록 연결했습니다. `--refresh-derived` 모드(외부 호출 없이 정의만
  재계산)에서는 실행되지 않아 그 모드의 "신규 관측을 만들지 않는다"는 보장을
  그대로 유지합니다.

이 변경은 90일 API 계약, 월별 순증감 계산, `last_success_at` 등 README에 문서화된
지표 정의를 전혀 건드리지 않습니다 — `data/snapshots/`와 `data/catalogs/`는
그대로 영구 보관되며, `object_history`가 참조 가능한 범위(최대 90일) 안의
`data/observations/` 폴더는 삭제 대상에서 항상 제외됩니다.

## 4. 테스트

`tests/test_tracker.py`에 추가:

- `HistoryTests.test_prune_removes_only_archives_past_the_history_window` —
  cutoff 기준 하루 전/당일 폴더의 삭제 여부 경계값과, 날짜 형식이 아닌
  폴더·파일이 보존되는지 확인.
- `HistoryTests.test_prune_is_a_noop_when_observations_directory_is_absent` —
  디렉터리가 없을 때 예외 없이 빈 리스트를 반환하는지 확인.
- `DataFixture.test_collection_run_prunes_stale_observation_archives` —
  실제 `update()` 실행 경로에서 200일 전 아카이브가 정리되고 당일 아카이브는
  남는지 확인하는 통합 테스트.

기존 `test_valid_collection_creates_real_object_history`,
`test_missing_and_reappearing_object_is_observation_history` 등은 수정 없이
그대로 통과해, 90일 이내 이력 조회 동작이 보존됨을 확인했습니다.

실행 결과:

```
python -m unittest discover -s tests -v   → Ran 48 tests, OK
node --test tests/frontend.test.cjs       → tests 9, pass 9, fail 0
node --check static/*.js                  → 통과
```

## 5. 이번에 하지 않은 것 (후속 과제로 명시)

- **실제 오브젝트 스토리지 이전(S3 등)**: 이번 검토의 범위 밖으로 명시적으로
  제외했습니다. 저장소를 flat file + git commit 구조로 유지한 채 압축 아카이브의
  수명만 유한하게 만드는 선까지만 처리했습니다.
- **일별 원자료의 월간 롤업(compaction)**: 90일 초과 시 삭제 대신 월 단위로
  합쳐서 보관하는 방식도 고려했으나, 현재 어떤 기능도 90일 초과 원자료를
  요구하지 않는 것을 확인했으므로(섹션 2) "합쳐서 보관"보다 "필요 없어지면
  삭제"가 더 단순하고 리스크가 낮다고 판단했습니다. 향후 90일 초과 이력이
  필요한 기능(예: 연 단위 트렌드에 원자료 정밀도가 필요해지는 경우)이 생기면
  롤업 방식으로 전환을 재검토해야 합니다.
- **저장소 I/O 추상화 계층**: `data/observations`를 읽고 쓰는 지점은 이미
  `tracker/history.py`의 `record_observations`/`object_history`/
  `prune_observations` 세 함수로 좁게 모여 있어(추가 추상화 없이도) 향후
  객체 스토리지로 옮길 때 손댈 지점이 이 파일 하나로 한정됩니다. 별도의
  스토리지 백엔드 인터페이스는 실제 이전 시점에 만드는 편이 낫다고 보고
  지금은 만들지 않았습니다.
- **위성망(constellation)별 샤딩**: 현재는 위성망 수가 7개로 적어 시급하지
  않습니다. 위성망 수가 늘어나 `plans.yaml` 검증이나 `update()`의 순차 처리
  시간이 문제가 되면 별도 검토가 필요합니다.
- **`data/snapshots/` 정리**: 집계 스냅샷은 파일당 수 KB로 영향이 작고,
  `trend_data`가 전체 기간을 다 읽으므로 임의로 지우면 월별 순증감 계산이
  깨집니다. 이번에는 손대지 않았습니다.
