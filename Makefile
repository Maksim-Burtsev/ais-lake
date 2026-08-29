# ais-lake — one box, two services, three stores.
# Targets grow per milestone.

.PHONY: dev dev-web down nuke migrate geo test seed e2e

dev:
	docker compose up -d --wait
	docker compose logs -f

# Vite dev server for the web shell (npm ci once: cd web && npm install).
dev-web:
	cd web && npm run dev

down:
	docker compose down

nuke:
	docker compose down -v --remove-orphans

migrate:
	cd pipeline && CLICKHOUSE_MIGRATE_URL=clickhouse://ais:ais-dev@localhost:9000/ais \
		uv run migrator --path db/migrations up
	cd api && DATABASE_URL=postgresql+asyncpg://ais:ais-dev@localhost:5432/ais \
		uv run alembic upgrade head

# The port polygons into PostGIS. Idempotent; run it after `migrate`.
# Runs in api's venv — that is where asyncpg lives.
geo:
	cd api && POSTGRES_URL=postgresql://ais:ais-dev@localhost:5432/ais \
		uv run python ../ops/geo/load_ports.py

test:
	cd pipeline && uv run ruff check . && uv run mypy ais_pipeline && uv run pytest -q
	cd api && uv run ruff check . && uv run mypy app && uv run pytest -q
	cd web && (test -d node_modules || npm ci) && npm run check

# Offline seed: DMA daily dumps -> the same refinery -> ClickHouse + Redis.
# Zips are cached under ops/seed/cache; SEED_DAYS / SEED_STRIDE tune the volume.
seed:
	cd pipeline && CLICKHOUSE_HOST=localhost REDIS_URL=redis://localhost:6379/0 \
		uv run python -m ais_pipeline.seed

e2e:
	cd web && npx playwright test
