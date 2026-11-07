# SPDX-License-Identifier: GPL-3.0-or-later
"""Compiled finite primitive domains and exact Enum restoration."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, Flag
from types import MappingProxyType
from typing import Any, Literal, get_args, get_origin

from .codecs import canonical_json, encode_scalar
from .errors import SchemaError, StorageError

type Primitive = str | int | bool
type Identity = tuple[type[Any], Primitive]


@dataclass(frozen=True)
class Choices:
    kind: Literal["enum", "literal"]
    values: tuple[Primitive, ...]
    members: Mapping[Identity, Enum] = field(repr=False, compare=False)

    def schema(self) -> dict[str, Any]:
        primitive = type(self.values[0])
        return {
            "codec": f"{self.kind}-{primitive.__name__}-v1",
            "type": {str: "string", int: "integer", bool: "boolean"}[primitive],
            "enum": self.values,
        }

    def primitive(self, value: Any) -> Primitive | None:
        """Refuse mutated members and invalid values before producing native bytes."""
        if value is None:
            return None
        if self.kind == "enum":
            if (
                not isinstance(value, Enum)
                or type(value.value) not in (str, int, bool)
                or self.members.get((type(value.value), value.value)) is not value
            ):
                raise StorageError("Value is outside its compiled Enum choice domain")
            return value.value  # type: ignore[no-any-return]
        if type(value) is not type(self.values[0]) or value not in self.values:
            raise StorageError("Value is outside its compiled Literal choice domain")
        return value

    def restore(self, value: Any) -> Any:
        if value is None:
            return None
        if self.kind == "enum":
            member = self.members.get((type(value), value))
            if member is None:
                raise StorageError("Stored value is outside its declared Enum choice domain")
            self.primitive(member)
            return member
        return self.primitive(value)


def compile_choices(annotation: Any) -> Choices | None:
    is_enum = isinstance(annotation, type) and issubclass(annotation, Enum)
    if not is_enum and get_origin(annotation) is not Literal:
        return None
    if is_enum and issubclass(annotation, Flag):
        raise SchemaError("Flag Enum choices are not supported")
    members = tuple(annotation) if is_enum else ()
    values = tuple(member.value for member in members) if is_enum else get_args(annotation)
    if (
        not values
        or type(values[0]) not in (str, int, bool)
        or any(type(value) is not type(values[0]) for value in values)
    ):
        raise SchemaError("Enum and Literal choices require homogeneous str, int, or bool values")
    for value in values:
        encode_scalar(value)
    ordered = tuple(sorted(set(values), key=canonical_json))
    lookup = MappingProxyType({(type(member.value), member.value): member for member in members})
    return Choices("enum" if is_enum else "literal", ordered, lookup)
