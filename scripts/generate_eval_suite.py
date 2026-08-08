#!/usr/bin/env python3
import json
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
FIXTURES_DIR = BENCHMARK_DIR / "fixtures" / "generated"

TASKS = [
    {
        "id": "bug_max_element",
        "category": "bug-repair",
        "prompt": "Fix the bug in find_max.py so it correctly finds the maximum element even if all numbers are negative.",
        "code_file": "find_max.py",
        "code": "def find_max(arr):\n    if not arr:\n        return None\n    max_val = 0\n    for num in arr:\n        if num > max_val:\n            max_val = num\n    return max_val\n",
        "verify_code": "import find_max\ndef test():\n    assert find_max.find_max([-5, -2, -10]) == -2\n    assert find_max.find_max([1, 2, 3]) == 3\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_palindrome",
        "category": "bug-repair",
        "prompt": "Fix is_palindrome.py to properly handle mixed case strings and ignore spaces.",
        "code_file": "is_palindrome.py",
        "code": "def is_palindrome(s):\n    return s == s[::-1]\n",
        "verify_code": "import is_palindrome\ndef test():\n    assert is_palindrome.is_palindrome('Race car') == True\n    assert is_palindrome.is_palindrome('hello') == False\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_factorial",
        "category": "bug-repair",
        "prompt": "Fix factorial.py to return 1 for 0!.",
        "code_file": "factorial.py",
        "code": "def factorial(n):\n    if n == 0:\n        return 0\n    return n * factorial(n-1)\n",
        "verify_code": "import factorial\ndef test():\n    assert factorial.factorial(0) == 1\n    assert factorial.factorial(5) == 120\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_vowels",
        "category": "bug-repair",
        "prompt": "Fix count_vowels.py to also count uppercase vowels.",
        "code_file": "count_vowels.py",
        "code": "def count_vowels(s):\n    return sum(1 for char in s if char in 'aeiou')\n",
        "verify_code": "import count_vowels\ndef test():\n    assert count_vowels.count_vowels('HELLO') == 2\n    assert count_vowels.count_vowels('world') == 1\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_fibonacci",
        "category": "bug-repair",
        "prompt": "Fix fibonacci.py. fib(0) should be 0, fib(1) should be 1.",
        "code_file": "fibonacci.py",
        "code": "def fib(n):\n    if n <= 1:\n        return 1\n    return fib(n-1) + fib(n-2)\n",
        "verify_code": "import fibonacci\ndef test():\n    assert fibonacci.fib(0) == 0\n    assert fibonacci.fib(1) == 1\n    assert fibonacci.fib(5) == 5\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_binary_search",
        "category": "bug-repair",
        "prompt": "Fix binary_search.py so it uses integer division for the mid point calculation.",
        "code_file": "binary_search.py",
        "code": "def binary_search(arr, target):\n    low, high = 0, len(arr) - 1\n    while low <= high:\n        mid = (low + high) / 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            low = mid + 1\n        else:\n            high = mid - 1\n    return -1\n",
        "verify_code": "import binary_search\ndef test():\n    assert binary_search.binary_search([1, 2, 3, 4, 5], 3) == 2\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_fizzbuzz",
        "category": "bug-repair",
        "prompt": "Fix fizzbuzz.py. The order of conditions is wrong so multiples of 15 return Fizz.",
        "code_file": "fizzbuzz.py",
        "code": "def fizzbuzz(n):\n    if n % 3 == 0:\n        return 'Fizz'\n    if n % 5 == 0:\n        return 'Buzz'\n    if n % 15 == 0:\n        return 'FizzBuzz'\n    return str(n)\n",
        "verify_code": "import fizzbuzz\ndef test():\n    assert fizzbuzz.fizzbuzz(15) == 'FizzBuzz'\n    assert fizzbuzz.fizzbuzz(3) == 'Fizz'\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_anagram",
        "category": "bug-repair",
        "prompt": "Fix is_anagram.py to ignore spaces and character casing.",
        "code_file": "is_anagram.py",
        "code": "def is_anagram(s1, s2):\n    return sorted(s1) == sorted(s2)\n",
        "verify_code": "import is_anagram\ndef test():\n    assert is_anagram.is_anagram('Listen', 'Silent') == True\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_gcd",
        "category": "bug-repair",
        "prompt": "Fix gcd.py. The modulo logic is backwards in the recursive call.",
        "code_file": "gcd.py",
        "code": "def gcd(a, b):\n    if b == 0:\n        return a\n    return gcd(b, b % a)\n",
        "verify_code": "import gcd\ndef test():\n    assert gcd.gcd(48, 18) == 6\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_sum_even",
        "category": "bug-repair",
        "prompt": "Fix sum_even.py so it only sums even numbers.",
        "code_file": "sum_even.py",
        "code": "def sum_even(arr):\n    return sum(x for x in arr if x % 2 == 1)\n",
        "verify_code": "import sum_even\ndef test():\n    assert sum_even.sum_even([1, 2, 3, 4]) == 6\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_remove_duplicates",
        "category": "bug-repair",
        "prompt": "Fix remove_duplicates.py to maintain the original order.",
        "code_file": "remove_duplicates.py",
        "code": "def remove_duplicates(arr):\n    return list(set(arr))\n",
        "verify_code": "import remove_duplicates\ndef test():\n    assert remove_duplicates.remove_duplicates([3, 1, 2, 3, 1]) == [3, 1, 2]\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_power",
        "category": "bug-repair",
        "prompt": "Fix power.py so that power(x, 0) returns 1 instead of 0.",
        "code_file": "power.py",
        "code": "def power(base, exp):\n    if exp == 0:\n        return 0\n    return base * power(base, exp - 1)\n",
        "verify_code": "import power\ndef test():\n    assert power.power(2, 0) == 1\n    assert power.power(2, 3) == 8\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_merge_sorted",
        "category": "bug-repair",
        "prompt": "Fix merge_sorted.py so it handles leftover elements from both arrays.",
        "code_file": "merge_sorted.py",
        "code": "def merge(arr1, arr2):\n    res = []\n    i, j = 0, 0\n    while i < len(arr1) and j < len(arr2):\n        if arr1[i] < arr2[j]:\n            res.append(arr1[i])\n            i += 1\n        else:\n            res.append(arr2[j])\n            j += 1\n    return res\n",
        "verify_code": "import merge_sorted\ndef test():\n    assert merge_sorted.merge([1, 3], [2, 4, 5]) == [1, 2, 3, 4, 5]\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_first_unique",
        "category": "bug-repair",
        "prompt": "Fix first_unique.py so it correctly finds the first non-repeating character.",
        "code_file": "first_unique.py",
        "code": "def first_unique(s):\n    for char in s:\n        if s.count(char) > 1:\n            return char\n    return None\n",
        "verify_code": "import first_unique\ndef test():\n    assert first_unique.first_unique('swiss') == 'w'\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_is_prime",
        "category": "bug-repair",
        "prompt": "Fix is_prime.py to correctly identify that 1 is not a prime number.",
        "code_file": "is_prime.py",
        "code": "def is_prime(n):\n    for i in range(2, int(n ** 0.5) + 1):\n        if n % i == 0:\n            return False\n    return True\n",
        "verify_code": "import is_prime\ndef test():\n    assert is_prime.is_prime(1) == False\n    assert is_prime.is_prime(2) == True\n    assert is_prime.is_prime(4) == False\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_title_case",
        "category": "bug-repair",
        "prompt": "Fix title_case.py to capitalize every word, not just the first one.",
        "code_file": "title_case.py",
        "code": "def title_case(s):\n    return s.capitalize()\n",
        "verify_code": "import title_case\ndef test():\n    assert title_case.title_case('hello world') == 'Hello World'\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_list_flatten",
        "category": "bug-repair",
        "prompt": "Fix list_flatten.py so it recursively flattens deeply nested lists.",
        "code_file": "list_flatten.py",
        "code": "def flatten(arr):\n    res = []\n    for item in arr:\n        if isinstance(item, list):\n            res.extend(item)\n        else:\n            res.append(item)\n    return res\n",
        "verify_code": "import list_flatten\ndef test():\n    assert list_flatten.flatten([1, [2, [3, 4]]]) == [1, 2, 3, 4]\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_count_words",
        "category": "bug-repair",
        "prompt": "Fix count_words.py to ignore punctuation.",
        "code_file": "count_words.py",
        "code": "def count_words(s):\n    return len(s.split())\n",
        "verify_code": "import count_words\ndef test():\n    # Wait, simple split on space doesn't ignore punctuation, but we want the count. Oh wait, if it's count, split() might be fine for just word count but maybe we want len(words)? Wait, prompt says ignore punctuation, maybe string has trailing punctuation that shouldn't make empty words? Let's just do len(re.findall(r'\\w+', s))\n    assert count_words.count_words('Hello, world!  ') == 2\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_find_missing",
        "category": "bug-repair",
        "prompt": "Fix find_missing.py. The formula for the expected sum is missing the +1.",
        "code_file": "find_missing.py",
        "code": "def find_missing(arr):\n    n = len(arr)\n    expected = n * (n - 1) // 2\n    return expected - sum(arr)\n",
        "verify_code": "import find_missing\ndef test():\n    assert find_missing.find_missing([0, 1, 3]) == 2\nif __name__ == '__main__':\n    test()\n"
    },
    {
        "id": "bug_array_intersection",
        "category": "bug-repair",
        "prompt": "Fix array_intersection.py to return a list of unique elements present in both arrays.",
        "code_file": "array_intersection.py",
        "code": "def intersection(arr1, arr2):\n    return arr1 + arr2\n",
        "verify_code": "import array_intersection\ndef test():\n    assert sorted(array_intersection.intersection([1, 2, 2, 1], [2, 2])) == [2]\nif __name__ == '__main__':\n    test()\n"
    }
]

def generate():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    
    manifest = {
        "schema_version": "nexus.benchmark.v1",
        "name": "nexus-generated-eval-suite",
        "tasks": []
    }
    
    for task in TASKS:
        task_dir = FIXTURES_DIR / task["id"]
        task_dir.mkdir(exist_ok=True)
        
        # Write code file
        code_path = task_dir / task["code_file"]
        code_path.write_text(task["code"])
        
        # Write verify file
        verify_path = task_dir / "verify.py"
        verify_path.write_text(task["verify_code"])
        
        # Add to manifest
        manifest["tasks"].append({
            "id": task["id"],
            "category": task["category"],
            "repository": f"fixtures/generated/{task['id']}",
            "prompt": task["prompt"],
            "allowed_paths": [task["code_file"], "verify.py"],
            "expected_changed_files": [task["code_file"]],
            "verification": [["python3", "verify.py"]],
            "timeout_seconds": 60
        })
        
    manifest_path = BENCHMARK_DIR / "generated_suite.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Generated 20 benchmark tasks at {manifest_path}")

if __name__ == "__main__":
    generate()
