.PHONY: up seed curl-complete traces worker test tui demo

GATEWAY ?= helios

tui:
	PYTHONPATH=src python -m helios.tui --gateway $(GATEWAY)

demo:
	PYTHONPATH=src python -m helios.cli demo

up:
	docker compose up --build

worker:
	PYTHONPATH=src python -m helios.worker

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
