# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned declarations and sample aggregates shared with native migration workers."""

from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference


class Target(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)


class Detail(Model):
    label: str
    weight: Decimal


class Sample(Model):
    key: str = Field(primary_key=True)
    tag: str = Field(unique=True)
    reading: Decimal


class Lot(Model):
    code: int = Field(primary_key=True)
    serial: str | None = Field(default=None, index=True)
    amount: Decimal
    target: Reference[Target] | None = Field(default=None, index=True)
    detail: Detail | None = None
    samples: Children[Sample] = ChildField(key="key")


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    locked: str = Field(unique=True)
    header: Detail | None = None
    lots: Children[Lot] = ChildField(key="code")


class DetailV2(Detail):
    __schema_version__ = 2
    note: bytes = Field(default=b"ready", index=True)
    precision: Decimal = Decimal("1.2300")


class SampleV2(Sample):
    __schema_version__ = 2
    reading: Decimal = Field(unique=True)
    quality: int = Field(default=7, index=True)


class LotV2(Lot):
    __schema_version__ = 2
    serial: str | None = Field(default=None, unique=True)
    amount: Decimal = Field(index=True)
    note: str | None = Field(default=None, index=True)
    detail: DetailV2 | None = None
    samples: Children[SampleV2] = ChildField(key="key")


class WarehouseV2(Warehouse):
    __schema_version__ = 2
    header: DetailV2 | None = None
    lots: Children[LotV2] = ChildField(key="code")
    note: int = Field(default=9, index=True)


def sample_records():
    return [
        Warehouse(
            id="W1",
            locked="L1",
            header=Detail(label="head", weight=Decimal("12.300")),
            lots={
                -1: Lot(
                    code=-1,
                    serial="private-A",
                    amount=Decimal("1.00"),
                    target=Reference[Target](key="T1"),
                    detail=Detail(label="body", weight=Decimal("1.20")),
                    samples={
                        "s/1": Sample(key="s/1", tag="tag-1", reading=Decimal("1.2300")),
                        "μ": Sample(key="μ", tag="tag-2", reading=Decimal("2.50")),
                    },
                ),
                2: Lot(code=2, amount=Decimal("1.000")),
                3: Lot(code=3, amount=Decimal("1.0000")),
            },
        ),
        Warehouse(
            id="W2",
            locked="L2",
            lots={
                0: Lot(
                    code=0,
                    serial="private-B",
                    amount=Decimal("2.000"),
                    target=Reference[Target](key="T1"),
                    samples={"B": Sample(key="B", tag="tag-3", reading=Decimal("3.50"))},
                )
            },
        ),
        Warehouse(id="W3", locked="L3"),
    ]
