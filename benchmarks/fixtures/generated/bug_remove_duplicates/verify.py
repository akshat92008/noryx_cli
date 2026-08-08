import remove_duplicates
def test():
    assert remove_duplicates.remove_duplicates([3, 1, 2, 3, 1]) == [3, 1, 2]
if __name__ == '__main__':
    test()
