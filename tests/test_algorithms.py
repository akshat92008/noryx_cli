"""
Tests for nexus.algorithms — covers binary_search with edge cases.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.algorithms import binary_search


class TestBinarySearch:
    """Comprehensive tests for binary_search."""

    # ── Happy path ──────────────────────────────────────────────────────────

    def test_found_at_beginning(self):
        """Target at index 0."""
        assert binary_search([1, 2, 3, 4, 5], 1) == 0

    def test_found_at_end(self):
        """Target at last index."""
        assert binary_search([1, 2, 3, 4, 5], 5) == 4

    def test_found_in_middle(self):
        """Target somewhere in the middle."""
        assert binary_search([1, 3, 5, 7, 9], 5) == 2

    def test_found_with_duplicates(self):
        """With duplicates, returns one of the valid indices."""
        idx = binary_search([1, 2, 2, 2, 3], 2)
        assert idx in (1, 2, 3)

    # ── Not found ───────────────────────────────────────────────────────────

    def test_not_found_too_small(self):
        """Target smaller than all elements."""
        assert binary_search([10, 20, 30], 5) == -1

    def test_not_found_too_large(self):
        """Target larger than all elements."""
        assert binary_search([10, 20, 30], 50) == -1

    def test_not_found_in_gap(self):
        """Target falls between two elements."""
        assert binary_search([1, 3, 5, 7], 4) == -1

    # ── Edge cases ──────────────────────────────────────────────────────────

    def test_empty_list(self):
        """Empty list returns -1."""
        assert binary_search([], 1) == -1

    def test_single_element_found(self):
        """Single-element list with match."""
        assert binary_search([42], 42) == 0

    def test_single_element_not_found(self):
        """Single-element list without match."""
        assert binary_search([42], 7) == -1

    def test_two_elements_first(self):
        """Two-element list, target is first."""
        assert binary_search([10, 20], 10) == 0

    def test_two_elements_second(self):
        """Two-element list, target is second."""
        assert binary_search([10, 20], 20) == 1

    def test_large_list(self):
        """Large list — stress test."""
        arr = list(range(0, 100_000, 2))  # 50k even numbers
        assert binary_search(arr, 88_888) == 44_444
        assert binary_search(arr, 88_889) == -1

    # ── Type safety ─────────────────────────────────────────────────────────

    def test_strings(self):
        """Works with strings."""
        assert binary_search(["apple", "banana", "cherry"], "banana") == 1

    def test_floats(self):
        """Works with floats."""
        assert binary_search([1.0, 2.5, 3.7, 5.0], 3.7) == 2

    def test_type_error_on_non_list(self):
        """Raises TypeError for non-list input."""
        with pytest.raises(TypeError, match="arr must be a list"):
            binary_search("not a list", 1)  # type: ignore[arg-type]

    def test_type_error_on_tuple(self):
        """Raises TypeError for tuple input."""
        with pytest.raises(TypeError, match="arr must be a list"):
            binary_search((1, 2, 3), 2)  # type: ignore[arg-type]
