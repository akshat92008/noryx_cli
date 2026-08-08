def find_max(arr):
    if not arr:
        return None
    max_val = 0
    for num in arr:
        if num > max_val:
            max_val = num
    return max_val
