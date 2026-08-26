# ais-lake — one box, two services, three stores.
# Targets grow per milestone.

.PHONY: dev down nuke test seed e2e

dev:
	docker compose up -d --wait
	docker compose logs -f

down:
	docker compose down

nuke:
	docker compose down -v --remove-orphans

test:
	@echo "make test: test suites arrive with M0-T2"

seed:
	@echo "make seed: offline seed arrives with M1-T3"

e2e:
	@echo "make e2e: Playwright smoke arrives with M2"
