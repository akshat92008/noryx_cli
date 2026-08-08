def find_missing(arr):
    n = len(arr)
    expected = n * (n - 1) // 2
    return expected - sum(arr)
