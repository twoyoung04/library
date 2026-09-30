import re


def normalize_isbn(raw):
    value = re.sub(r'[^0-9Xx]', '', raw or '').upper()
    if len(value) == 10 and valid_isbn10(value):
        stem = '978' + value[:9]
        return stem + str(isbn13_check_digit(stem))
    if len(value) == 13 and value.startswith(('978', '979')) and valid_isbn13(value):
        return value
    raise ValueError('请输入有效的 ISBN-10 或 ISBN-13')


def isbn13_check_digit(stem):
    return (-sum(int(digit) * (1 if i % 2 == 0 else 3)
                 for i, digit in enumerate(stem))) % 10


def valid_isbn13(value):
    return value.isdigit() and len(value) == 13 and isbn13_check_digit(value[:12]) == int(value[12])


def valid_isbn10(value):
    if len(value) != 10 or not value[:9].isdigit() or value[-1] not in '0123456789X':
        return False
    total = sum((10 - i) * int(value[i]) for i in range(9))
    total += 10 if value[-1] == 'X' else int(value[-1])
    return total % 11 == 0
