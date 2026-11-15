# Garmin dashboards. Daily workflow: make all && make serve  (or open build/site/index.html directly)
PY := .venv/bin/python
CHROME := /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

.PHONY: setup inventory metrics site all test serve verify review-exercises screenshots clean

setup:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt

inventory:
	@mkdir -p build
	@$(PY) -m src.inventory | tee build/inventory.md

metrics:
	$(PY) -m src.build_metrics

site:
	$(PY) -m src.build_site

all: metrics site

test:
	$(PY) -m pytest -q tests

serve:
	@echo "http://localhost:8765/index.html"
	cd build/site && ../../$(PY) -m http.server 8765 --bind 127.0.0.1

verify:
	$(PY) -m src.verify

review-exercises:
	$(PY) -m src.review_exercises

screenshots:
	$(PY) -m src.screenshots

clean:
	rm -rf build/site build/data build/metrics.duckdb
