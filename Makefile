.PHONY: up seed curl-complete traces test

up:
	docker compose up --build

seed:
	docker compose exec api python -m helios.cli create-api-key --tenant acme --app support

curl-complete:
	curl -s -X POST http://localhost:8000/v1/ai/complete \
	  -H "X-Helios-API-Key: $(KEY)" \
	  -H "Content-Type: application/json" \
	  -d '{"input": "Hello Project Helios"}'

traces:
	curl -s http://localhost:8000/v1/traces -H "X-Helios-API-Key: $(KEY)"

test:
	PYTHONPATH=src python -m pytest -q
