"""Validate tier_test_catalog.yaml references resolve to real tests."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


CATALOG_PATH = Path(__file__).parent / "tier_test_catalog.json"


def _load_catalog() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_test_path(dotted: str):
    parts = dotted.split("::")
    if len(parts) < 2:
        pytest.fail(f"Invalid test path (expected module::Class::test_name): {dotted}")
    module = importlib.import_module(parts[0])
    obj = module
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


@pytest.mark.meta
def test_catalog_loads():
    catalog = _load_catalog()
    assert catalog["version"]
    assert len(catalog["test_cases"]) >= 20


@pytest.mark.meta
def test_automated_catalog_entries_resolve():
    catalog = _load_catalog()
    missing = []
    for case in catalog["test_cases"]:
        if not case.get("automated"):
            continue
        target = case.get("test")
        if not target:
            missing.append(f"{case['id']}: missing test path")
            continue
        try:
            _resolve_test_path(target)
        except Exception as exc:  # noqa: BLE001 — collect all failures
            missing.append(f"{case['id']}: {target} -> {exc}")
    assert not missing, "Broken catalog references:\n" + "\n".join(missing)


@pytest.mark.meta
def test_catalog_ids_are_unique():
    catalog = _load_catalog()
    ids = [case["id"] for case in catalog["test_cases"]]
    assert len(ids) == len(set(ids)), "Duplicate TC IDs in catalog"
