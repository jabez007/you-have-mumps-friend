# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic declarations and explicit persistence metadata."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from pydantic_core import PydanticUndefined

from .errors import SchemaError


class Model(BaseModel):
    """A validated value object. Changes never perform storage operations."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        validate_default=True,
        hide_input_in_errors=True,
    )
    __global__: ClassVar[str | None] = None
    __schema_version__: ClassVar[int] = 1


type Children[T: Model] = dict[str | int, T]


class Reference[T: Model](BaseModel):
    """An immutable key for an independently persisted, declared target model."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )
    key: str | int


def ChildField(
    default: Any = PydanticUndefined,
    *,
    key: str,
    index: bool = False,
    unique: bool = False,
    **kwargs: Any,
) -> Any:
    """A parent-owned dictionary keyed by the child model's primary-key field."""
    if index or unique:
        raise SchemaError("Index a scalar or Reference field inside the child model")
    if "json_schema_extra" in kwargs:
        raise ValueError("json_schema_extra is reserved for mapper metadata")
    metadata: dict[str, Any] = {"yhmf": {"children_key": key}}
    if default is PydanticUndefined:
        return PydanticField(default_factory=dict, json_schema_extra=metadata, **kwargs)
    return PydanticField(default, json_schema_extra=metadata, **kwargs)


def Field(
    default: Any = PydanticUndefined,
    *,
    primary_key: bool = False,
    index: bool = False,
    unique: bool = False,
    on_delete: str | None = None,
    **kwargs: Any,
) -> Any:
    """Pydantic Field with explicit persistence, index, and relationship metadata."""
    if on_delete is not None and on_delete != "restrict":
        raise NotImplementedError("Only on_delete='restrict' is implemented")
    if "json_schema_extra" in kwargs:
        raise ValueError("json_schema_extra is reserved for mapper metadata")
    metadata: dict[str, Any] = {"primary_key": primary_key}
    if index or unique:
        metadata["index"] = True
    if unique:
        metadata["unique"] = True
    if on_delete is not None:
        metadata["on_delete"] = on_delete
    return PydanticField(
        default,
        json_schema_extra={"yhmf": metadata},
        **kwargs,
    )
