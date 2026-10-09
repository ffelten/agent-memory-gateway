"""Trusted provider boundary; implemented by Florian's Senso integration."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Document:
    node_id: str
    content_id: str
    version: str
    ready: bool = False


@dataclass(frozen=True)
class Passage:
    content_id: str
    text: str
    version: str


class Provider(Protocol):
    def ingest(self, *, title: str, text: str, external_id: str) -> Document: ...
    def inspect(self, node_id: str) -> Document: ...
    def search(self, *, query: str, content_ids: list[str], max_results: int,
               require_scoped_ids: bool = True) -> list[Passage]: ...
    def delete(self, node_id: str) -> None: ...
