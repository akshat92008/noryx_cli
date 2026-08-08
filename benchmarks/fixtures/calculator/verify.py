"""Acceptance script for the calculator regression benchmark."""

from calculator import add, multiply, subtract

assert add(5, 4) == 9
assert subtract(5, 4) == 1
assert multiply(5, 4) == 20
assert multiply(-3, 4) == -12
print("calculator acceptance checks passed")
