"""
Algorithm utilities — classic algorithms implemented with type safety and edge-case handling.

Functions:
    binary_search: Perform binary search on a sorted list.
"""

from typing import List, TypeVar

T = TypeVar("T")


def binary_search(arr: List[T], target: T) -> int:
    """
    Perform binary search on a sorted list to find the target value.

    Args:
        arr: A sorted list of comparable elements (ascending order).
        target: The value to search for.

    Returns:
        The index of the target if found, otherwise -1.

    Raises:
        TypeError: If arr is not a list.

    Examples:
        >>> binary_search([1, 3, 5, 7, 9], 5)
        2
        >>> binary_search([1, 3, 5, 7, 9], 4)
        -1
        >>> binary_search([], 1)
        -1
        >>> binary_search([1], 1)
        0
    """
    if not isinstance(arr, list):
        raise TypeError("arr must be a list")

    left, right = 0, len(arr) - 1

    while left <= right:
        mid = left + (right - left) // 2  # avoids overflow
        mid_val = arr[mid]

        if mid_val == target:
            return mid
        elif mid_val < target:
            left = mid + 1
        else:
            right = mid - 1

    return -1
