import is_palindrome
def test():
    assert is_palindrome.is_palindrome('Race car') == True
    assert is_palindrome.is_palindrome('hello') == False
if __name__ == '__main__':
    test()
