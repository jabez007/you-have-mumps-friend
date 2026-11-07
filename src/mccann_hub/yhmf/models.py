# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic declarations and explicit persistence metadata."""

from decimal import Decimal
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
    ordered: bool = False,
    sum_of: str | None = None,
    **kwargs: Any,
) -> Any:
    """A parent-owned dictionary keyed by the child model's primary-key field."""
    if type(ordered) is not bool:
        raise SchemaError("ordered must be a boolean")
    if sum_of is not None:
        raise SchemaError("Stored sums must be declared as Decimal fields")
    if index or unique or ordered:
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
    ordered: bool = False,
    on_delete: str | None = None,
    sum_of: str | None = None,
    **kwargs: Any,
) -> Any:
    """Pydantic Field with explicit persistence, index, and relationship metadata."""
    if type(ordered) is not bool:
        raise SchemaError("ordered must be a boolean")
    if on_delete is not None and on_delete not in ("restrict", "set_null", "cascade"):
        raise NotImplementedError("on_delete supports restrict, set_null, or cascade")
    if "json_schema_extra" in kwargs:
        raise ValueError("json_schema_extra is reserved for mapper metadata")
    metadata: dict[str, Any] = {"primary_key": primary_key}
    if sum_of is not None:
        metadata["sum_of"] = sum_of
        if default is PydanticUndefined and "default_factory" not in kwargs:
            default = Decimal(0)
    if index or unique or ordered:
        metadata["index"] = True
    if ordered:
        metadata["index_codec"] = "ordered-v1"
    if unique:
        metadata["unique"] = True
    if on_delete is not None:
        metadata["on_delete"] = on_delete
    return PydanticField(
        default,
        json_schema_extra={"yhmf": metadata},
        **kwargs,
    )
