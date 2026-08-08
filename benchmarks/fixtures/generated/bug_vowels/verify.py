import count_vowels
def test():
    assert count_vowels.count_vowels('HELLO') == 2
    assert count_vowels.count_vowels('world') == 1
if __name__ == '__main__':
    test()
