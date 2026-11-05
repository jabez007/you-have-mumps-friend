# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Field,
    Model,
    Reference,
    Registry,
    SchemaSnapshot,
    compare_schemas,
)


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    name: str


def snapshot(model, *targets):
    return SchemaSnapshot(Registry(model, *targets).spec(model).snapshot)


def change_kinds(before, after):
    return {(change.kind, change.path) for change in compare_schemas(before, after)}


def edited(original, **updates):
    document = original.to_dict()
    document.update(updates)
    return SchemaSnapshot(
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "ascii"
        )
    )


def test_identical_snapshots_have_no_changes():
    original = snapshot(Product)
    assert compare_schemas(original, SchemaSnapshot(original.canonical)) == ()


def test_added_optional_field_and_index_have_explicit_defaults():
    class New(Product):
        __schema_version__ = 2
        note: str | None = Field(default=None, index=True)

    changes = compare_schemas(snapshot(Product), snapshot(New))
    assert {(change.kind, change.path) for change in changes} == {
        ("schema_version_changed", ()),
        ("field_added", ("note",)),
        ("index_added", ("note",)),
    }
    added = next(change for change in changes if change.kind == "field_added")
    assert added.default_json == b"null"
    assert not added.destructive and not added.requires_manual_review


def test_removed_fields_are_destructive_and_not_guessed_as_renames():
    class Renamed(Model):
        __global__ = "^Product"
        id: str = Field(primary_key=True)
        description: str

    changes = compare_schemas(snapshot(Product), snapshot(Renamed))
    removed = next(change for change in changes if change.kind == "field_removed")
    assert removed.path == ("name",) and removed.destructive and removed.requires_manual_review
    assert ("field_added", ("description",)) in {(c.kind, c.path) for c in changes}


def test_required_field_without_a_default_requires_manual_values():
    class New(Product):
        code: str

    added = next(
        c for c in compare_schemas(snapshot(Product), snapshot(New)) if c.kind == "field_added"
    )
    assert added.requires_manual_review and added.default_json is None


def test_default_factories_are_unavailable_backfill_values():
    calls = []

    def factory():
        calls.append("called")
        return "dynamic"

    class New(Product):
        code: str = Field(default_factory=factory)

    added = next(
        c for c in compare_schemas(snapshot(Product), snapshot(New)) if c.kind == "field_added"
    )
    assert added.requires_manual_review and added.default_json is None
    assert calls == []


def test_validation_changes_are_separate_from_storage_types():
    class Constrained(Product):
        name: str = Field(max_length=20)

    changes = compare_schemas(snapshot(Product), snapshot(Constrained))
    assert [(c.kind, c.path) for c in changes] == [("validation_changed", ("name",))]
    assert not changes[0].requires_manual_review


def test_type_changes_require_manual_transformation():
    class Numeric(Product):
        name: int

    changes = compare_schemas(snapshot(Product), snapshot(Numeric))
    changed = next(c for c in changes if c.kind == "type_changed")
    assert changed.path == ("name",) and changed.destructive and changed.requires_manual_review


def test_primary_key_type_changes_affect_the_whole_identity():
    class Numeric(Product):
        id: int = Field(primary_key=True)
        name: str = Field(index=True)

    class Indexed(Product):
        name: str = Field(index=True)

    kinds = change_kinds(snapshot(Indexed), snapshot(Numeric))
    assert ("identity_changed", ()) in kinds
    assert ("index_rebuild_required", ("name",)) in kinds


def test_presentation_changes_do_not_become_data_transformations():
    class Presentation(Product):
        name: str = Field(description="A revised label")

    assert change_kinds(snapshot(Product), snapshot(Presentation)) == {("metadata_changed", ())}


def test_existing_default_changes_do_not_become_field_additions():
    class Before(Product):
        name: str = "Old default"

    class After(Product):
        name: str = "New default"

    assert change_kinds(snapshot(Before), snapshot(After)) == {("default_changed", ("name",))}


def test_nullable_changes_are_reported_without_a_type_conversion():
    class Nullable(Product):
        name: str | None

    assert change_kinds(snapshot(Product), snapshot(Nullable)) == {
        ("nullability_changed", ("name",))
    }


def test_nested_additions_resolve_changed_pydantic_definition_names():
    class Lot(Model):
        number: str = Field(primary_key=True)

    class Before(Model):
        __global__ = "^Warehouse"
        id: int = Field(primary_key=True)
        lots: Children[Lot] = ChildField(key="number")

    class NewLot(Lot):
        note: str | None = Field(default=None, index=True)

    class After(Before):
        lots: Children[NewLot] = ChildField(key="number")

    assert change_kinds(snapshot(Before), snapshot(After)) == {
        ("field_added", ("lots", "note")),
        ("index_added", ("lots", "note")),
    }


def test_reference_retargeting_requires_reverse_and_secondary_rebuilds():
    class Other(Product):
        __global__ = "^OtherProduct"

    class Before(Model):
        __global__ = "^Order"
        id: int = Field(primary_key=True)
        product: Reference[Product] = Field(index=True)

    class After(Before):
        product: Reference[Other] = Field(index=True)

    kinds = change_kinds(snapshot(Before, Product), snapshot(After, Other))
    assert ("relationship_changed", ("product",)) in kinds
    assert ("reference_rebuild_required", ("product",)) in kinds
    assert ("index_rebuild_required", ("product",)) in kinds


def test_unknown_layout_and_schema_constructs_require_manual_review():
    original = snapshot(Product)
    changes = compare_schemas(original, edited(original, layout=2))
    assert any(c.kind == "layout_changed" and c.requires_manual_review for c in changes)
    declaration = original.to_dict()["declaration"]
    declaration["properties"]["name"] = {"allOf": [{"type": "string"}]}
    changes = compare_schemas(original, edited(original, declaration=declaration))
    assert any(c.kind == "unsupported_schema" and c.requires_manual_review for c in changes)


def test_external_json_references_are_not_followed():
    original = snapshot(Product)
    declaration = original.to_dict()["declaration"]
    declaration["properties"]["name"] = {"$ref": "https://example.invalid/schema.json"}
    changes = compare_schemas(original, edited(original, declaration=declaration))
    assert any(c.kind == "unsupported_schema" for c in changes)


def test_diffs_are_deterministic_and_defaults_are_hidden_in_representations():
    class New(Product):
        note: str = "private-default-marker"

    before, after = snapshot(Product), snapshot(New)
    first = compare_schemas(before, after)
    assert first == compare_schemas(before, after)
    assert "private-default-marker" not in repr(first)


@pytest.mark.parametrize(
    "property_schema",
    [
        [],
        {"type": []},
        {"anyOf": [{"type": []}]},
        {"$ref": 42},
        {"anyOf": False},
        {"type": "string", "yhmf": {"index": "yes"}},
    ],
)
def test_unfamiliar_field_shapes_become_manual_changes(property_schema):
    original = snapshot(Product)
    declaration = original.to_dict()["declaration"]
    declaration["properties"]["name"] = property_schema
    changes = compare_schemas(original, edited(original, declaration=declaration))
    assert any(c.kind == "unsupported_schema" and c.requires_manual_review for c in changes)
