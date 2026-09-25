"""Explicit request policy shared by routing, adapters and source caches."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class DataRequestContext:
    config: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "config", MappingProxyType(deepcopy(dict(self.config))))
