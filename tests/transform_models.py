# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared deterministic declarations for native and offline transform workloads."""

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference, Transform


class Destination(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)


class OtherDestination(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)


class Common(Model):
    id: str = Field(primary_key=True)
    amount: int = 2
    serial: str = Field(unique=True)
    related: Reference[Destination] | None = None


class Source(Common):
    __global__ = "^YHMFTestProduct"
    name: str = Field(index=True)


class Renamed(Common):
    __global__ = Source.__global__
    __schema_version__ = 2
    label: str = Field(index=True)


class Removed(Common):
    __global__ = Source.__global__
    __schema_version__ = 2


class ChangedType(Source):
    __schema_version__ = 2
    amount: str


class WithFactory(Source):
    __schema_version__ = 2
    token: str = Field(default_factory=lambda: "reproducible", index=True)


class Detail(Model):
    text: str


class Sample(Model):
    id: int = Field(primary_key=True)
    value: int = Field(index=True)


class WithOwned(Source):
    __schema_version__ = 2
    detail: Detail
    samples: Children[Sample] = ChildField(key="id")


class Retargeted(Source):
    __schema_version__ = 2
    related: Reference[OtherDestination] | None = None


class Swapped(Source):
    __schema_version__ = 2


KINDS = ("rename", "remove", "type", "factory", "owned", "reference", "swap")
RELATED = (Destination, OtherDestination)


def case(kind):
    target = {
        "rename": Renamed,
        "remove": Removed,
        "type": ChangedType,
        "factory": WithFactory,
        "owned": WithOwned,
        "reference": Retargeted,
        "swap": Swapped,
    }[kind]

    def convert(source):
        values = source.model_dump()
        if kind == "rename":
            values["label"] = values.pop("name")
        elif kind == "remove":
            values.pop("name")
        elif kind == "type":
            values["amount"] = str(source.amount)
        elif kind == "owned":
            values.update(
                detail=Detail(text=source.name), samples={0: Sample(id=0, value=source.amount)}
            )
        elif kind == "reference":
            values["related"] = (
                Reference[OtherDestination](key=source.related.key) if source.related else None
            )
        elif kind == "swap":
            values["serial"] = f"S{(int(source.id) + 1) % 3}"
        return target.model_validate(values)

    return target, Transform(f"test.{kind}.v1", convert)
