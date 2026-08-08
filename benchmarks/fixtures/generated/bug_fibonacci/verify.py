import fibonacci
def test():
    assert fibonacci.fib(0) == 0
    assert fibonacci.fib(1) == 1
    assert fibonacci.fib(5) == 5
if __name__ == '__main__':
    test()
