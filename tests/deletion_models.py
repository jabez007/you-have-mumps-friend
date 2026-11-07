# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy declarations shared by tests and independent native workers."""

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference


def declarations():
    class Target(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class Detail(Model):
        link: Reference[Target] | None = Field(default=None, on_delete="set_null", index=True)

    class Line(Model):
        id: str = Field(primary_key=True)
        link: Reference[Target] | None = Field(default=None, on_delete="cascade", index=True)
        detail: Detail = Field(default_factory=Detail)

    class Source(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        protected: Reference[Target] | None = Field(default=None, index=True)
        optional: Reference[Target] | None = Field(default=None, on_delete="set_null", index=True)
        cascading: Reference[Target] | None = Field(default=None, on_delete="cascade", index=True)
        detail: Detail = Field(default_factory=Detail)
        lines: Children[Line] = ChildField(key="id")

    return Target, Source, Detail, Line


def cycle_declaration():
    class Node(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        cascading: "Reference[Node] | None" = Field(default=None, on_delete="cascade", index=True)
        protected: "Reference[Node] | None" = Field(default=None, index=True)
        optional: "Reference[Node] | None" = Field(default=None, on_delete="set_null", index=True)

    Node.model_rebuild(_types_namespace={"Node": Node})
    return Node
