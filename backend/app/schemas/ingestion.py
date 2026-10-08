"""Ingestion response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestionError(BaseModel):
    row: int
    message: str
    data: dict[str, str] = Field(default_factory=dict)


class IngestionResult(BaseModel):
    dataset: str
    rows_read: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[IngestionError] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class IngestionSummary(BaseModel):
    results: dict[str, IngestionResult]
