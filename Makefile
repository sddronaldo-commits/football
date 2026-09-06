.PHONY: install demo run score test preview serve clean

install:
	pip install -r pipeline/requirements.txt

# Full pipeline on synthetic data, with a seeded ledger so the site has a record.
demo:
	python -m pipeline.run --backfill

# Live run. Needs FOOTBALL_DATA_TOKEN and data/market_values.json.
run:
	python -m pipeline.run

score:
	python -m pipeline.run --score-only

test:
	python -m pytest tests -q

preview:
	python tools/bundle_preview.py

serve:
	@echo "http://localhost:8000"
	@cd web && python -m http.server 8000

clean:
	rm -rf .cache web/data data/ledger preview.html
