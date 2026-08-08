def flatten(arr):
    res = []
    for item in arr:
        if isinstance(item, list):
            res.extend(item)
        else:
            res.append(item)
    return res
