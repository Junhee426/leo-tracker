# Global LEO Constellation Tracker V1.1.0

글로벌 저궤도 위성통신망의 **현재 궤도 배치 현황, 발사이력, 개발계획, 일정·규모 변경 이력**을 함께 관리하는 경량 웹 대시보드입니다.

V1.1은 V1.0 구조를 유지하면서 다음 기능을 추가합니다.

- 위성망별 상세 페이지: `/constellation/<id>`
- Launch History 탭 및 `data/launches.json`
- 초기 계획과 최신 계획 비교: `data/roadmap_history.json`
- 일정 지연 / 계획 확대 / On-track 구분
- CSV 다운로드: `/download/constellations.csv`
- Excel(.xlsx) 다운로드: `/download/tracker.xlsx`
  - Constellations / Launches / Changes 3개 시트
- 상세 API: `/api/constellation/<id>`
- Launch API: `/api/launches`
- Plan-vs-Current API: `/api/roadmap-history`

## 1. 데이터 원칙

### 자동 추적 데이터

CelesTrak OMM JSON 그룹을 이용합니다.

- `STARLINK`
- `ONEWEB`
- `KUIPER`
- `QIANFAN`
- `HULIANWANG`

`updater/update_data.py`가 현재 catalog object 수, 당해연도 object 수, 평균 고도·경사각을 갱신합니다.

> `tracked_in_orbit`는 **catalog에서 추적되는 object 수**이며 서비스 가능한 operational satellite 수와 같다고 가정하지 않습니다.

갱신 시 CelesTrak 카탈로그 수와 `plans.yaml`의 날짜가 명시된 사업자·정부 발표 수를
`crosscheck` 필드에서 비교합니다. `matched`, `close`, `review`는 정의와 발표 시점 차이를
검토하기 위한 신호이며 어느 한 출처가 틀렸다는 판정이 아닙니다. Space-Track SATCAT은
NORAD 식별자와 현재 상태를, UNOOSA Online Index는 등록·식별 정보를 수동 확인하는 보조
레퍼런스로 Sources에 포함합니다.

### 계획 데이터

`data/plans.yaml`에서 사람이 검증한 공식/규제/정부 발표를 관리합니다.

### 발사 데이터

`data/launches.json`은 mission-level 발사 기록입니다. V1.1 seed에는 공식 자료로 확인 가능한 Amazon Leo 발사 14회, Qianfan 2026-07-04 배치, Telesat Lightspeed 첫 생산위성 계획을 포함합니다.

대규모 Starlink 등의 모든 발사 임무를 자동으로 채우지는 않습니다. 이는 추적 데이터와 mission metadata의 출처가 다르기 때문입니다.

### 계획 변경 데이터

`data/roadmap_history.json`의 형식:

```json
{
  "constellation_id": "lightspeed",
  "category": "schedule",
  "milestone": "Global service",
  "baseline": "Late 2027",
  "current": "Q1 2028",
  "delta_months": 3,
  "trend": "delayed",
  "source_id": "telesat_aug_2026"
}
```

`trend` 권장값:

- `delayed`
- `expanded`
- `on_track`
- `completed`

## 2. 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

브라우저:

```text
http://127.0.0.1:5000
```

## 3. 데이터 갱신

```bash
python updater/update_data.py
```

갱신 파일:

- `data/current.json`
- `data/changes.json`
- `data/snapshots/YYYY-MM-DD.json`

CelesTrak 호출 실패 시 기존 live row가 있으면 이를 보존합니다.

## 4. GitHub Actions

`.github/workflows/update-data.yml`은 매일 UTC 21:00, 즉 한국시간 오전 06:00에 실행됩니다.

GitHub 저장소에서 다음을 허용하세요.

**Settings → Actions → General → Workflow permissions → Read and write permissions**

첫 배포 후에는 Actions 탭에서 `Update LEO tracker data`를 수동 실행해 최신 catalog snapshot을 생성하는 것을 권장합니다.

## 5. Render 배포

### Blueprint

1. GitHub에 프로젝트 업로드
2. Render → **New → Blueprint**
3. 저장소 연결
4. `render.yaml` 인식 후 배포

### 직접 Web Service

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app
Health Check: /health
```

## 6. 주요 API

```text
GET /api/status
GET /api/launches
GET /api/roadmap-history
GET /api/changes
GET /api/sources
GET /api/constellation/<id>
GET /health
```

## 7. 다운로드

```text
GET /download/constellations.csv
GET /download/tracker.xlsx
```

Excel은 서버에서 OOXML 형식으로 생성하므로 별도의 Excel Python 패키지를 요구하지 않습니다.

## 8. 파일 구조

```text
global-leo-tracker-v1.1/
├─ app.py
├─ requirements.txt
├─ render.yaml
├─ Procfile
├─ data/
│  ├─ plans.yaml
│  ├─ current.json
│  ├─ launches.json
│  ├─ roadmap_history.json
│  ├─ changes.json
│  ├─ sources.json
│  └─ snapshots/
├─ updater/
│  └─ update_data.py
├─ templates/
│  ├─ index.html
│  └─ detail.html
├─ static/
│  ├─ app.js
│  ├─ detail.js
│  └─ style.css
└─ .github/workflows/update-data.yml
```

## 9. V1.2 확장 후보

- Starlink/Guowang/Qianfan mission metadata 자동 수집
- 발사 계획 대비 실제 발사일 자동 지연 계산
- 월별/연도별 발사 차트
- 궤도 shell별 위성 수
- 과거 snapshot 기반 배치속도 추세
- 사용자 정의 K-LEO benchmark 행
- 출처 변경 감지 및 `REVIEW REQUIRED` 큐
