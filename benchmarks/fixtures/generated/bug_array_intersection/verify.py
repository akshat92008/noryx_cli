import array_intersection
def test():
    assert sorted(array_intersection.intersection([1, 2, 2, 1], [2, 2])) == [2]
if __name__ == '__main__':
    test()
