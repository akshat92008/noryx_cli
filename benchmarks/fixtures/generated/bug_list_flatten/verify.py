import list_flatten
def test():
    assert list_flatten.flatten([1, [2, [3, 4]]]) == [1, 2, 3, 4]
if __name__ == '__main__':
    test()
