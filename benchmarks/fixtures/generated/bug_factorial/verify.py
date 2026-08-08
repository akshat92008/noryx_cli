import factorial
def test():
    assert factorial.factorial(0) == 1
    assert factorial.factorial(5) == 120
if __name__ == '__main__':
    test()
