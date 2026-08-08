import count_words
def test():
    # Wait, simple split on space doesn't ignore punctuation, but we want the count. Oh wait, if it's count, split() might be fine for just word count but maybe we want len(words)? Wait, prompt says ignore punctuation, maybe string has trailing punctuation that shouldn't make empty words? Let's just do len(re.findall(r'\w+', s))
    assert count_words.count_words('Hello, world!  ') == 2
if __name__ == '__main__':
    test()
