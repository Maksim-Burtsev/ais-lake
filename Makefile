# ais-lake — one box, two services, three stores.
# Real targets land with M0-T1 (compose) and grow per milestone.

.PHONY: dev down nuke test seed e2e

dev:
	@echo "make dev: compose.yaml arrives with task M0-T1"

down:
	@echo "make down: compose.yaml arrives with task M0-T1"

nuke:
	@echo "make nuke: compose.yaml arrives with task M0-T1"

test:
	@echo "make test: test suites arrive with M0-T2"

seed:
	@echo "make seed: offline seed arrives with M1-T3"

e2e:
	@echo "make e2e: Playwright smoke arrives with M2"
