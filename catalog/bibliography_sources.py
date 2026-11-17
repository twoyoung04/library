import json
import re
from time import sleep
from urllib.error import HTTPError
from urllib.parse import urlencode

from .bibliography import edition_fields
from .bibliography_import import book_isbn, text_fields_are_valid
from .lookup import cp_book_detail, cp_book_paths, fetch_html, fetch_json, wuming_metadata


WUMING = 'https://www.book345.com'
COMMERCIAL_PRESS = 'https://www.cp.com.cn'
PUBLISHERS = (
    ('rww', '人民文学出版社'),
    ('yilin', '译林出版社'),
    ('shanghai-translation', '上海译文出版社'),
    ('sdx', '生活·读书·新知三联书店'),
    ('cp', '商务印书馆'),
    ('zhonghua', '中华书局'),
    ('citic', '中信出版社'),
)


def metadata_candidate(isbn, source_key, metadata):
    if not metadata:
        return None
    fields = edition_fields(metadata)
    fields.pop('cover_url')
    source = metadata.get('source', '')
    if (fields['title'] and source
            and text_fields_are_valid({**fields, 'source': source, 'key': source_key})):
        return isbn, source, source_key, fields


def wuming_rows(books, publisher=None):
    if not isinstance(books, list):
        raise ValueError('未找到无名图书书目列表')
    for book in books:
        if not isinstance(book, dict):
            continue
        isbn = book_isbn(book.get('isbn'))
        if not isbn or not isbn.startswith('9787'):
            continue
        if publisher and book.get('publisher') != publisher:
            continue
        yield metadata_candidate(isbn, f'{WUMING}/api/detail/{isbn}', wuming_metadata(book))


def wuming_toplist(kind):
    url = f'{WUMING}/api/toplist?' + urlencode({'type': kind, 'count': 50})
    data = fetch_json(url, raise_errors=True)
    yield from wuming_rows(data.get('books') if isinstance(data, dict) else None)


def wuming_publisher(publisher):
    url = f'{WUMING}/list?' + urlencode({'publisher': publisher})
    page = fetch_html(url, raise_errors=True)
    payload = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                        page, re.S)
    if not payload:
        raise ValueError('无名图书出版社目录缺少书目数据')
    try:
        books = json.loads(payload[1])['props']['pageProps']['booksArr']
    except (KeyError, TypeError) as exc:
        raise ValueError('无名图书出版社目录格式已变更') from exc
    yield from wuming_rows(books, publisher)


def commercial_press_books():
    page = fetch_html(f'{COMMERCIAL_PRESS}/book/new.dhtml', raise_errors=True)
    paths = cp_book_paths(page)
    if not paths:
        raise ValueError('商务印书馆新书目录缺少图书链接')
    for path in paths[:15]:
        sleep(1)
        url = COMMERCIAL_PRESS + path
        try:
            detail = cp_book_detail(fetch_html(url, raise_errors=True))
        except HTTPError as exc:
            if exc.code not in (404, 410):
                raise
            detail = None
        yield metadata_candidate(detail[0], url, detail[1]) if detail else None
