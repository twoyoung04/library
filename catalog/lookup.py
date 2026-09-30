import json
import os
import re
from html import unescape
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


def fetch_html(url):
    request = Request(url, headers={'User-Agent': 'PersonalLibrary/1.0 (private catalog)',
                                    'Accept': 'text/html'})
    try:
        with urlopen(request, timeout=5) as response:
            return response.read(300_000).decode(response.headers.get_content_charset() or 'utf-8',
                                                 errors='replace')
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


def lookup_neodb(isbn):
    """Search NeoDB's public catalog and accept only an exact edition ISBN."""
    url = 'https://neodb.social/api/catalog/search?' + urlencode({
        'category': 'book', 'query': isbn,
    })
    response = fetch_json(url)
    if not isinstance(response, dict) or not isinstance(response.get('data'), list):
        return None
    for item in response['data']:
        if not isinstance(item, dict) or not _same_isbn(item.get('isbn'), isbn):
            continue
        title = item.get('title') or item.get('display_title')
        if not isinstance(title, str) or not title.strip():
            continue
        return {
            'title': title.strip(), 'author': _names(item.get('author')),
            'publisher': _names(item.get('pub_house') or item.get('publisher')),
            'published_year': _year(item.get('pub_year')),
            'cover_url': _cover(item.get('cover_image_url')),
            'source': 'NeoDB',
        }
    return None


def lookup_cp(isbn):
    """Search the publisher's public catalog for ISBNs in its 978-7-100 range."""
    if not isbn.startswith('9787100'):
        return None
    formatted = f'978-7-100-{isbn[7:12]}-{isbn[12]}'
    search_url = 'https://www.cp.com.cn/AdvancedSearch_Text.dhtml?' + urlencode({
        'g_fw': '0', 'g_isbn': formatted, 'g_sm': '', 'g_zz': '',
    })
    search_page = fetch_html(search_url) or ''
    paths = re.findall(r'<h2>\s*<a\s+href="(/book/[a-zA-Z0-9-]+\.html)"', search_page)
    for path in paths[:3]:
        page = fetch_html('https://www.cp.com.cn' + path) or ''
        found_isbn = re.search(r'ISBN[：:]\s*([0-9Xx\- ]{10,20})', page)
        if not found_isbn or not _same_isbn(found_isbn.group(1), isbn):
            continue
        title_match = re.search(r'<title>\s*([^<]+)\s*</title>', page, re.I)
        if not title_match or not title_match.group(1).strip():
            continue
        date_match = re.search(r'出版时间[：:]\s*([12]\d{3})', page)
        cover_match = re.search(r'<div class="book_pic">.*?<img\s+src="([^"]+)"', page, re.S)
        return {
            'title': unescape(title_match.group(1)).strip(), 'author': '',
            'publisher': '商务印书馆',
            'published_year': int(date_match.group(1)) if date_match else None,
            'cover_url': _cover(cover_match.group(1)) if cover_match else '',
            'source': '商务印书馆',
        }
    return None


def lookup_douban(isbn):
    """Read an ISBN's public Douban book page when its structured ISBN matches."""
    page = fetch_html('https://book.douban.com/isbn/' + quote(isbn) + '/') or ''
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                          page, re.I | re.S):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get('@type') != 'Book':
            continue
        if not _same_isbn(data.get('isbn'), isbn):
            continue
        title = data.get('name')
        if not isinstance(title, str) or not title.strip():
            continue
        authors = data.get('author') or []
        if not isinstance(authors, list):
            authors = [authors]
        author = '、'.join(name for item in authors
                          if (name := item.get('name') if isinstance(item, dict) else item)
                          and isinstance(name, str))
        cover_match = re.search(r'<meta\b[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
                                page, re.I)
        return {
            'title': title.strip(), 'author': author,
            'publisher': _plain(_labeled_html(page, '出版社')),
            'published_year': _year(_plain(_labeled_html(page, '出版年'))),
            'cover_url': _cover(unescape(cover_match.group(1))) if cover_match else '',
            'source': '豆瓣读书',
        }
    return None


def lookup_cdclib(isbn):
    """Search Chengdu Library's public ISBN catalog for a matching record."""
    url = 'https://opac.cdclib.cn/opac/search?' + urlencode({
        'q': isbn, 'searchWay': 'isbn', 'searchSource': 'reader', 'scWay': 'full',
    })
    page = fetch_html(url) or ''
    records = re.split(r'<li\b[^>]*class=["\'][^"\']*\blibBookLi\b[^"\']*["\'][^>]*>',
                       page, flags=re.I)
    for record in records[1:11]:
        found_isbn = re.search(r'<img\b[^>]*\bisbn=["\']([^"\']+)', record, re.I)
        if not found_isbn or not _same_isbn(found_isbn.group(1), isbn):
            continue
        title_match = re.search(r'<a\b[^>]*class=["\']libBookDetNm["\'][^>]*>(.*?)</a>',
                                record, re.I | re.S)
        title = _plain(title_match.group(1)) if title_match else ''
        if not title:
            continue
        author = _plain(_labeled_html(record, '责任者'))
        publication = _plain(_labeled_html(record, '出版信息'))
        publisher = re.sub(r'[,，]\s*[12]\d{3}.*$', '', publication).strip()
        return {
            'title': title, 'author': author, 'publisher': publisher,
            'published_year': _year(publication), 'cover_url': '',
            'source': '成都图书馆',
        }
    return None


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
    domestic = (lookup_wuming, lookup_neodb, lookup_cp, lookup_douban, lookup_cdclib,
                lookup_showapi, lookup_juhe)
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


def _plain(value):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', value or ''))).strip()


def _labeled_html(page, label):
    match = re.search(r'<span\b[^>]*class=["\'][^"\']*\b(?:pl|libBkDetTit)\b[^"\']*["\'][^>]*>'
                      + r'\s*' + re.escape(label) + r'\s*[：:]?\s*</span>\s*(.*?)(?:<br\s*/?>|</p>)',
                      page, re.I | re.S)
    return match.group(1) if match else ''


def _names(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return '、'.join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ''


def _year(value):
    match = re.search(r'(?<!\d)(?:1[5-9]\d{2}|20\d{2}|2100)', str(value or ''))
    return int(match.group()) if match else None
