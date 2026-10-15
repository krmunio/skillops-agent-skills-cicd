"""Intentionally incomplete batch partitioning; no external queue is used."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    id: str
    dependencies: tuple = ()


def enqueue_many(items, backend):
    ready = [item for item in items if not item.dependencies]
    deferred = [item for item in items if item.dependencies]
    for item in ready:
        backend.save(item, "queued")
    for item in deferred:
        backend.save(item, "deferred")
    return ready + deferred
