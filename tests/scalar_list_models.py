# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared declarations for list persistence and independent native workers."""

from enum import StrEnum

from mccann_hub.yhmf import ChildField, Children, Field, Model


class State(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ListRecord(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    values: list[int | None] | None = Field(default=None, unique=True, max_length=10)
    labels: list[str] = Field(default_factory=list, index=True)
    states: list[State] = Field(default_factory=list)


class BudgetChild(Model):
    id: str = Field(primary_key=True)
    values: list[int] = Field(default_factory=list)


class BudgetRoot(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    children: Children[BudgetChild] = ChildField(key="id")
