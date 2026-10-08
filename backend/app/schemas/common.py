"""Shared schema primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base for response models read straight off SQLAlchemy objects."""

    model_config = {"from_attributes": True}


class Page(BaseModel, Generic[T]):
    """Envelope for list endpoints."""

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total
