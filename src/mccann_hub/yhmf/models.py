# SPDX-License-Identifier: GPL-3.0-or-later
"""Pydantic declarations and explicit persistence metadata."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from pydantic_core import PydanticUndefined


class Model(BaseModel):
    """A validated value object. Changes never perform storage operations."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )
    __global__: ClassVar[str | None] = None
    __schema_version__: ClassVar[int] = 1


def Field(
    default: Any = PydanticUndefined,
    *,
    primary_key: bool = False,
    index: bool = False,
    unique: bool = False,
    **kwargs: Any,
) -> Any:
    """Pydantic Field with mapper metadata. Indexes arrive after native CRUD."""
    if index or unique:
        raise NotImplementedError("Secondary indexes and uniqueness are not implemented yet")
    if "json_schema_extra" in kwargs:
        raise ValueError("json_schema_extra is reserved for mapper metadata")
    return PydanticField(
        default,
        json_schema_extra={"yhmf": {"primary_key": primary_key}},
        **kwargs,
    )
