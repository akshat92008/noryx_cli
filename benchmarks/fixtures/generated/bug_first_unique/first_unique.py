def first_unique(s):
    for char in s:
        if s.count(char) > 1:
            return char
    return None
