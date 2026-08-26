# ais-lake — one box, two services, three stores.
# Targets grow per milestone.

.PHONY: dev down nuke migrate test seed e2e

dev:
	docker compose up -d --wait
	docker compose logs -f

down:
	docker compose down

nuke:
	docker compose down -v --remove-orphans

migrate:
	cd pipeline && CLICKHOUSE_MIGRATE_URL=clickhouse://ais:ais-dev@localhost:9000/ais \
		uv run migrator --path db/migrations up

test:
	cd pipeline && uv run ruff check . && uv run mypy ais_pipeline && uv run pytest -q
	cd api && uv run ruff check . && uv run mypy app && uv run pytest -q

# Offline seed: DMA daily dumps -> the same refinery -> ClickHouse + Redis.
# Zips are cached under ops/seed/cache; SEED_DAYS / SEED_STRIDE tune the volume.
seed:
	cd pipeline && CLICKHOUSE_HOST=localhost REDIS_URL=redis://localhost:6379/0 \
		uv run python -m ais_pipeline.seed

e2e:
	@echo "make e2e: Playwright smoke arrives with M2"
