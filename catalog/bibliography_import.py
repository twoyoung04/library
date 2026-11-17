import csv
import json
import unicodedata
from collections import Counter
from itertools import islice

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .bibliography import edition_fields
from .isbn import normalize_isbn
from .lookup import _names, _year
from .models import BibliographyConflict, BibliographyImport, Edition, PublicEdition


SOURCE = 'Open Library'
SCAN_BATCH_SIZE = 10_000
ISBN_BATCH_SIZE = 100
CHINESE_LANGUAGE_KEYS = {'/languages/chi', '/languages/zho', '/languages/cmn'}


def book_isbn(value):
    if not isinstance(value, str):
        return None
    try:
        isbn = normalize_isbn(value)
    except ValueError:
        return None
    return isbn if isbn.startswith(('978', '979')) else None


def pending_library_isbns():
    editions = Edition.objects.exclude(isbn__isnull=True).exclude(
        Exists(PublicEdition.objects.filter(isbn=OuterRef('isbn'))))
    raw_isbns = editions.order_by().values_list('isbn', flat=True).distinct()
    return {isbn for value in raw_isbns.iterator() if (isbn := book_isbn(value))}


def text_fields_are_valid(fields):
    for value in fields.values():
        if isinstance(value, str):
            if '\0' in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                return False
    return True


def parse_edition(line, library_isbns):
    try:
        record_type, source_key, _, _, payload = line.split(b'\t', 4)
        if record_type != b'/type/edition':
            return None
        source_key = source_key.decode('ascii')
        record = json.loads(payload)
    except (ValueError, UnicodeError):
        return None

    if not isinstance(record, dict) or not source_key.startswith('/books/') or len(source_key) > 100:
        return None
    isbns = set()
    for key in ('isbn_13', 'isbn_10'):
        values = record.get(key)
        if isinstance(values, list):
            isbns.update(isbn for value in values if (isbn := book_isbn(value)))
    if len(isbns) != 1:
        return None
    isbn = isbns.pop()
    languages = record.get('languages')
    language_keys = {
        language['key'] for language in languages
        if isinstance(language, dict) and isinstance(language.get('key'), str)
    } if isinstance(languages, list) else set()
    chinese = bool(language_keys & CHINESE_LANGUAGE_KEYS) or (
        not language_keys and isbn.startswith('9787')
    )
    if not chinese and isbn not in library_isbns:
        return None
    title = record.get('title')
    if not isinstance(title, str) or not title.strip():
        return None
    fields = edition_fields({
        'title': title,
        'author': _names(record.get('by_statement')),
        'publisher': _names(record.get('publishers')),
        'published_year': _year(record.get('publish_date')),
    })
    fields.pop('cover_url')
    if text_fields_are_valid({**fields, 'key': source_key}):
        return isbn, SOURCE, source_key, fields


def feed_rows(stream, offset):
    reader = csv.DictReader(stream, strict=True)
    required = {'isbn', 'title', 'source'}
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError('CSV 必须包含 isbn、title、source 三列')
    for index, row in enumerate(islice(reader, offset, None), start=offset + 1):
        isbn = book_isbn(row.get('isbn'))
        source = (row.get('source') or '').strip()
        candidate = None
        if isbn and 0 < len(source) <= 100 and (row.get('title') or '').strip():
            fields = edition_fields({
                'title': row['title'],
                'author': row.get('author'),
                'publisher': row.get('publisher'),
                'published_year': _year(row.get('published_year')),
            })
            fields.pop('cover_url')
            if text_fields_are_valid({**fields, 'source': source}):
                candidate = isbn, source, isbn, fields
        yield index, candidate


def comparable(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def save_candidate(snapshot, isbn, source, source_key, fields):
    provenance = {'source': source, 'key': source_key, 'snapshot': snapshot}
    incoming_provenance = {field: provenance for field, value in fields.items() if value}
    public, created = PublicEdition.objects.select_for_update().get_or_create(isbn=isbn, defaults={
        **fields, 'source': source, 'collected_fields': incoming_provenance,
    })
    if created:
        return 'created'
    if any(getattr(public, field) and fields[field]
           and comparable(getattr(public, field)) != comparable(fields[field])
           for field in ('title', 'publisher')):
        BibliographyConflict.objects.update_or_create(
            public_edition=public, source=source, source_key=source_key,
            defaults={'snapshot': snapshot, 'candidate': fields},
        )
        return 'conflicts'
    updates = {
        field: value for field, value in fields.items() if value and not getattr(public, field)
    }
    if not updates:
        return 'unchanged'
    for field, value in updates.items():
        setattr(public, field, value)
        public.collected_fields[field] = provenance
    public.save(update_fields=[*updates, 'collected_fields'])
    return 'filled'


@transaction.atomic
def commit_batch(progress, offset, candidates, scanned, *, complete=False):
    current = BibliographyImport.objects.select_for_update().get(pk=progress.pk)
    if current.offset != progress.offset or current.completed_at:
        raise RuntimeError('导入进度已被其他进程推进')
    counts = Counter(current.counts)
    counts['scanned'] += scanned
    for candidate in candidates:
        counts[save_candidate(progress.snapshot, *candidate)] += 1
    counts['skipped'] += scanned - len(candidates)
    progress.offset = offset
    progress.counts = dict(counts)
    progress.completed_at = timezone.now() if complete else None
    progress.save(update_fields=['offset', 'counts', 'completed_at', 'updated_at'])


def edition_rows(stream, offset):
    library_isbns = pending_library_isbns()
    stream.seek(offset)
    for line in stream:
        yield stream.tell(), parse_edition(line, library_isbns)


def import_candidates(rows, progress, report):
    candidates = []
    scanned = 0
    offset = progress.offset
    for offset, candidate in rows:
        scanned += 1
        if candidate:
            candidates.append(candidate)
        if scanned >= SCAN_BATCH_SIZE or len(candidates) >= ISBN_BATCH_SIZE:
            commit_batch(progress, offset, candidates, scanned)
            report(progress)
            candidates.clear()
            scanned = 0
    commit_batch(progress, offset, candidates, scanned, complete=True)
    report(progress)
