import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .isbn import normalize_isbn


def fetch_json(url, form=None):
    headers = {'User-Agent': 'PersonalLibrary/1.0 (private catalog)', 'Accept': 'application/json'}
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = Request(url, data=urlencode(form).encode() if form is not None else None,
                      headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def lookup_wuming(isbn):
    """Read only the bibliographic fields from the public Wuming Books API."""
    data = fetch_json('https://www.book345.com/api/detail/' + quote(isbn))
    if not isinstance(data, dict) or not data.get('title') or not _same_isbn(data.get('isbn'), isbn):
        return None
    image = data.get('img') or ''
    if image.startswith('/covers/'):
        image = 'https://www.book345.com' + image
    return {
        'title': data['title'], 'author': data.get('author') or '',
        'publisher': data.get('publisher') or '',
        'published_year': _year(data.get('pubDate')),
        'cover_url': _cover(image), 'source': '无名图书',
    }


def lookup_showapi(isbn):
    key = os.environ.get('SHOWAPI_APP_KEY', '').strip()
    if not key or not isbn.startswith('978'):
        return None
    url = 'https://route.showapi.com/1626-1?' + urlencode({'appKey': key})
    response = fetch_json(url, {'isbn': isbn})
    if not isinstance(response, dict) or response.get('showapi_res_code') != 0:
        return None
    body = response.get('showapi_res_body') or {}
    if body.get('ret_code') != 0:
        return None
    data = body.get('data') or {}
    if not data.get('title') or not _same_isbn(data.get('isbn'), isbn):
        return None
    return {
        'title': data['title'], 'author': data.get('author') or '',
        'publisher': data.get('publisher') or '',
        'published_year': _year(data.get('pubdate')),
        'cover_url': _cover(data.get('img')), 'source': '万维易源',
    }


def lookup_juhe(isbn):
    key = os.environ.get('JUHE_ISBN_KEY', '').strip()
    if not key:
        return None
    response = fetch_json('https://apis.juhe.cn/isbn/query', {'key': key, 'isbn': isbn})
    if not isinstance(response, dict) or response.get('error_code') != 0:
        return None
    data = (response.get('result') or {}).get('data') or {}
    if not data.get('title') or not any(_same_isbn(value, isbn) for value in
                                        (data.get('isbn'), data.get('isbn10'))):
        return None
    return {
        'title': data['title'], 'author': data.get('author') or '',
        'publisher': data.get('publisher') or '',
        'published_year': _year(data.get('pubDate')),
        'cover_url': _cover(data.get('img') or data.get('smallImg')),
        'source': '聚合数据',
    }


def lookup_book(isbn):
    domestic = (lookup_wuming, lookup_showapi, lookup_juhe)
    international = (lookup_google, lookup_openlibrary)
    for provider in domestic + international:
        result = provider(isbn)
        if result and result.get('title'):
            return result
    return None


def lookup_google(isbn):
    data = fetch_json('https://www.googleapis.com/books/v1/volumes?q=isbn:' + quote(isbn) + '&maxResults=5')
    for item in (data or {}).get('items', []):
        info = item.get('volumeInfo') or {}
        ids = [entry.get('identifier', '') for entry in info.get('industryIdentifiers', [])]
        if not any(_same_isbn(value, isbn) for value in ids):
            continue
        image = (info.get('imageLinks') or {}).get('thumbnail', '').replace('http://', 'https://')
        return {
            'title': info.get('title', ''), 'author': '、'.join(info.get('authors') or []),
            'publisher': info.get('publisher', ''),
            'published_year': _year(info.get('publishedDate')),
            'cover_url': image, 'source': 'Google Books',
        }
    return None


def lookup_openlibrary(isbn):
    data = fetch_json('https://openlibrary.org/isbn/' + quote(isbn) + '.json')
    if not data:
        return None
    ids = (data.get('isbn_13') or []) + (data.get('isbn_10') or [])
    if ids and not any(_same_isbn(value, isbn) for value in ids):
        return None
    authors = []
    for author in data.get('authors') or []:
        key = author.get('key', '') if isinstance(author, dict) else ''
        if key.startswith('/authors/'):
            author_data = fetch_json('https://openlibrary.org' + key + '.json')
            if author_data and author_data.get('name'):
                authors.append(author_data['name'])
    covers = data.get('covers') or []
    cover = f'https://covers.openlibrary.org/b/id/{covers[0]}-M.jpg' if covers else ''
    return {
        'title': data.get('title', ''), 'author': '、'.join(authors),
        'publisher': '、'.join(data.get('publishers') or []),
        'published_year': _year(data.get('publish_date')),
        'cover_url': cover, 'source': 'Open Library',
    }


def _same_isbn(value, target):
    try:
        return normalize_isbn(value) == target
    except ValueError:
        return False


def _cover(value):
    if not isinstance(value, str):
        return ''
    if value.startswith('http://'):
        value = 'https://' + value[7:]
    return value if value.startswith('https://') else ''


def _year(value):
    match = re.search(r'(?<!\d)(?:1[5-9]\d{2}|20\d{2}|2100)', str(value or ''))
    return int(match.group()) if match else None
