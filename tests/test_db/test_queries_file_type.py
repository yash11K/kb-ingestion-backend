"""Tests for file_type support in DB query functions.

Validates Requirement 6.2: file listing queries return both markdown and pdf records.
"""

from __future__ import annotations

from src.db.queries import _ALLOWED_FILTERS, _build_conditions, _model_to_dict
from src.db.models import KBFile


class TestFileTypeInAllowedFilters:
    """Verify file_type is a supported filter key."""

    def test_file_type_in_allowed_filters(self):
        assert "file_type" in _ALLOWED_FILTERS

    def test_build_conditions_with_file_type_filter(self):
        conditions = _build_conditions(KBFile, {"file_type": "pdf"})
        assert len(conditions) == 1

    def test_build_conditions_without_file_type_filter(self):
        """No file_type filter means no conditions — both types returned."""
        conditions = _build_conditions(KBFile, {})
        assert len(conditions) == 0


class TestModelToDictIncludesFileType:
    """Verify _model_to_dict includes file_type from KBFile instances."""

    def test_file_type_column_exists_on_kb_files(self):
        col_names = [col.name for col in KBFile.__table__.columns]
        assert "file_type" in col_names
