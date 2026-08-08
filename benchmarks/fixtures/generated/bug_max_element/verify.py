import find_max
def test():
    assert find_max.find_max([-5, -2, -10]) == -2
    assert find_max.find_max([1, 2, 3]) == 3
if __name__ == '__main__':
    test()
