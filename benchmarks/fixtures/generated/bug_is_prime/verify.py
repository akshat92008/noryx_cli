import is_prime
def test():
    assert is_prime.is_prime(1) == False
    assert is_prime.is_prime(2) == True
    assert is_prime.is_prime(4) == False
if __name__ == '__main__':
    test()
