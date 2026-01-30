# heuristic-production-planning
A modular heuristic production planning framework. Models the problem as ProblemData (facts) + ScenarioConfig (constraints/weights) + State (solution), then searches for the best plan using plug-in optimizers (Genetic Algorithm, Tabu Search, hybrid). Focus: lot sizing, capacity &amp; shift-aware scheduling.

## How to Run (EN)
- Requirements: Python 3.11+
- Install deps: `python -m pip install -r requirements.txt`
- Start API: `uvicorn app.main:app --reload --port 8000`
- Health check: `curl http://127.0.0.1:8000/health`
- Create frame: `curl -X POST -H "Content-Type: application/json" --data-binary @DataFormat/problemFrame.json http://127.0.0.1:8000/frame`

## API Endpoints (EN)
- `POST /frame` create a Problem Frame
- `GET /frame/{id}` fetch a stored frame
- `POST /frame/{id}/validate` run consistency checks
- `POST /frame/{id}/evaluate` compute KPI placeholders and validity
- `POST /frame/{id}/state` update state only
- `POST /frame/{id}/optimize` evaluate multiple candidate plans, pick best, and return iterations (feasible flag, total_cost, hard_total, full evaluation snapshot).
  - Body: `{"candidates": [{"state": {...}}, ...]}`; if no feasible plan, picks minimum hard violation.
  - ID behavior: if `problem_meta.problem_code` already exists, a unique suffix is appended (e.g. `PLAN_01_ab12cd34`).

## Kurulum ve Çalıştırma (TR)
- Gereksinimler: Python 3.11+ (Anaconda uygundur)
- Kurulum: `python.exe -m pip install -r requirements.txt`
- API başlat: `python.exe -m uvicorn app.main:app --reload --port 8000`
- Sağlık kontrolü: `curl http://127.0.0.1:8000/health`
- Örnek POST: `curl -X POST -H "Content-Type: application/json" --data-binary @DataFormat/problemFrame.json http://127.0.0.1:8000/frame`

## API Endpointleri (TR)
- `POST /frame` Problem Çerçevesi oluştur
- `GET /frame/{id}` kayıtlı çerçeveyi getir
- `POST /frame/{id}/validate` tutarlılık kontrolleri
- `POST /frame/{id}/evaluate` KPI ve geçerlilik hesapla (placeholder)
- `POST /frame/{id}/state` sadece state güncelle
- `POST /frame/{id}/optimize` optimizasyon stub (501)
  - ID davranışı: `problem_meta.problem_code` mevcutsa benzersiz bir ek eklenir (ör. `PLAN_01_ab12cd34`).

## Project Structure / Proje Yapısı
- `app/main.py`: FastAPI giriş noktası
- `app/api/`: API rotaları (`/frame`, `/evaluate`, `/optimize`)
- `app/frame/models/`: Problem çerçevesi modelleri
- `app/frame/ingest/`: Harici JSON → iç model normalizasyonu
- `app/frame/services/`: Frame yönetimi (save/get/update_state)
- `app/frame/repositories/`: Disk persist (`data/{id}.json`)
- `app/evaluation/`: Doğrulama ve KPI değerlendirme katmanı
- `app/optimization/`: Optimizasyon plug-in girişi (stub)
- `DataFormat/`: Örnek giriş verileri
- `tests/`: Test senaryoları ve örnek data
- `data/`: API tarafından yazılan çıktılar

## Testing

Quick start:

```bash
python -m venv .venv
.\.venv\Scripts\activate       # Windows
source .venv/bin/activate      # macOS/Linux
pip install -r requirements-dev.txt

# run all tests
python run_tests.py

# only API/integration tests
python run_tests.py -m api

# only normalization/unit tests
python run_tests.py -m normalizer
```

- Test marker explanations and file list: see `tests/README.md`.
- `requirements-dev.txt` includes test-only deps (pytest, httpx) on top of `requirements.txt`.
