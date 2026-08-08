def power(base, exp):
    if exp == 0:
        return 0
    return base * power(base, exp - 1)
