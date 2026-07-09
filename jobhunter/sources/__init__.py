"""Job source adapters. Each hits a public, ToS-permitted JSON API."""
from __future__ import annotations

from .base import Source
from .greenhouse import Greenhouse
from .lever import Lever
from .ashby import Ashby

REGISTRY: dict[str, type[Source]] = {
    "greenhouse": Greenhouse,
    "lever": Lever,
    "ashby": Ashby,
}


def build(source_type: str, company: str) -> Source:
    try:
        cls = REGISTRY[source_type]
    except KeyError:
        raise ValueError(f"unknown source type '{source_type}'. known: {sorted(REGISTRY)}")
    return cls(company)
