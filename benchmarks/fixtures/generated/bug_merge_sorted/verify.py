import merge_sorted
def test():
    assert merge_sorted.merge([1, 3], [2, 4, 5]) == [1, 2, 3, 4, 5]
if __name__ == '__main__':
    test()
