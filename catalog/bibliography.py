import hashlib
import os

from django.core.cache import cache

from .lookup import lookup_book
from .models import PublicEdition


def edition_fields(metadata):
    year = metadata.get('published_year')
    return {
        'title': str(metadata.get('title') or '').strip()[:300],
        'author': str(metadata.get('author') or '').strip()[:300],
        'publisher': str(metadata.get('publisher') or '').strip()[:200],
        'published_year': year if isinstance(year, int) and 1000 <= year <= 2100 else None,
        'cover_url': str(metadata.get('cover_url') or '').strip()[:1000],
    }


def book_metadata(isbn):
    shareable = isbn.startswith(('978', '979'))
    if shareable:
        public = PublicEdition.objects.filter(isbn=isbn).first()
        if public:
            return _as_metadata(public)

    provider_config = os.environ.get('SHOWAPI_APP_KEY', '') + '|' + os.environ.get('JUHE_ISBN_KEY', '')
    provider_version = hashlib.sha256(provider_config.encode()).hexdigest()[:12]
    key = f'book-lookup:v6:{provider_version}:{isbn}'
    result = cache.get(key)
    if result is None:
        result = lookup_book(isbn) or {}
        cache.set(key, result, 24 * 60 * 60 if result else 60 * 60)

    if shareable and result:
        fields = edition_fields(result)
        source = str(result.get('source') or '').strip()[:100]
        if fields['title'] and source:
            public, _ = PublicEdition.objects.get_or_create(isbn=isbn, defaults={**fields, 'source': source})
            return _as_metadata(public)
    return result


def _as_metadata(public):
    return {
        'title': public.title, 'author': public.author, 'publisher': public.publisher,
        'published_year': public.published_year, 'cover_url': public.cover_url, 'source': public.source,
    }
