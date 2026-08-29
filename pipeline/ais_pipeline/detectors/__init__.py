"""The detectors: ais.raw → a per-ship state machine → `events`.

A sibling of the refinery, not a part of it — same topic, its own consumer
group. The machine (machine.py) is pure and synchronous; the Kafka consumer,
the tick and the two sinks are the only I/O, so every decision is testable
without a network.
"""
