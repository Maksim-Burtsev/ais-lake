"""The refinery: ais.raw → parsed, validated, deduped rows → Redis + ClickHouse.

Pure logic (parser, validator, dedup, state) lives in its own modules and is
injected into the service; Kafka/Redis/ClickHouse adapters stay thin.
"""
