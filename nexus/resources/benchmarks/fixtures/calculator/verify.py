"""Acceptance script for the packaged calculator benchmark."""
try:
    from .calculator import add, multiply, subtract
except ImportError:
    from calculator import add, multiply, subtract

def main():
    assert add(5,4)==9
    assert subtract(5,4)==1
    assert multiply(5,4)==20
    assert multiply(-3,4)==-12
    print("calculator acceptance checks passed")

if __name__=="__main__": main()
