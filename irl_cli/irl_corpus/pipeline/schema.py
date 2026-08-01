"""Annotation schema import and management."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from irl_corpus.db.models import AnnotationSchema
from irl_corpus.db.queries import get_schema_by_version, list_schemas


def import_schema(session: Session, json_path: Path) -> AnnotationSchema:
    """Import an annotation schema JSON file into the database.

    If a schema with the same version already exists, it is returned unchanged.
    The imported schema is NOT automatically set as current.
    """
    with open(json_path, encoding="utf-8") as json_file:
        data = json.load(json_file)

    version = data.get("version")
    if not version:
        raise ValueError("Schema JSON must have a top-level 'version' field.")

    existing = get_schema_by_version(session, version)
    if existing:
        return existing

    schema = AnnotationSchema(
        version=version,
        date=data.get("date", ""),
        content=json.dumps(data),
        is_current=False,
    )
    session.add(schema)
    session.flush()
    return schema


def set_current(session: Session, schema_id: str) -> AnnotationSchema:
    """Mark one schema as current, clearing the flag on all others."""
    schemas = list_schemas(session)
    target = None
    for schema in schemas:
        if schema_id in (schema.id, schema.version):
            schema.is_current = True
            target = schema
        else:
            schema.is_current = False
    if target is None:
        raise ValueError(f"Schema '{schema_id}' not found.")
    session.flush()
    return target


def get_tier_names(schema: AnnotationSchema) -> list[str]:
    """Return the list of tier names defined in the schema."""
    data = json.loads(schema.content)
    return list(data.get("tiers", {}).keys())


def get_tier_values(schema: AnnotationSchema, tier_name: str) -> dict[str, str]:
    """Return the allowed values dict for a given tier."""
    data = json.loads(schema.content)
    tier = data.get("tiers", {}).get(tier_name, {})
    return tier.get("values", {})
