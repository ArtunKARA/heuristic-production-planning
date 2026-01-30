# Tests Overview

## Test Suites
- `test_normalizer_and_eval.py` (`normalizer`, `api` markers): canonical JSON normalizer and evaluate_state smoke.
- `test_scenarios.py` (`api`, `normalizer` markers): FastAPI endpoints, reference validation, constraint/evaluation adapters.
- `test_eval_functions.py` (`api` marker): exercises all evaluator functions on a synthetic violation case.
- `test_optimize_api.py` (`api` marker): /optimize endpoint; selects best candidate plan.

## How to Run
```bash
# from repo root
python -m venv .venv
.\.venv\Scripts\activate       # Windows
source .venv/bin/activate      # macOS/Linux
pip install -r requirements-dev.txt

# all tests
python run_tests.py

# specific markers
python run_tests.py -m api
python run_tests.py -m normalizer
```

## Markers
- `api`: end-to-end API + evaluation flows.
- `normalizer`: JSON normalization/adaptation functions.
