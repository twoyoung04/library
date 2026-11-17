import csv
import gzip
import hashlib
import http.client
import io
import os
import re
import shutil
import time
import zlib
from datetime import timedelta
from functools import partial
from itertools import islice
from pathlib import Path
from tempfile import TemporaryFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from catalog.bibliography_import import edition_rows, feed_rows, import_candidates, pending_library_isbns
from catalog.bibliography_sources import (
    PUBLISHERS, commercial_press_books, metadata_candidate, wuming_publisher, wuming_toplist,
)
from catalog.lookup import lookup_book
from catalog.models import BibliographyImport, BibliographyLookup


USER_AGENT = 'PersonalLibrary Bibliography Import'
OPEN_LIBRARY_PREFIX = 'openlibrary:'
DAILY_ISBN_LOOKUPS = 10
INPUT_ERRORS = (OSError, ValueError, EOFError, csv.Error, zlib.error, http.client.HTTPException)


class HTTPSFeedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_feed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_feed_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None:
        raise ValueError('中文书目源须使用 HTTPS，且地址不能包含用户名或密码')


def latest_snapshot():
    upstream = http.client.HTTPSConnection('openlibrary.org', timeout=30)
    try:
        upstream.request('HEAD', '/data/ol_dump_editions_latest.txt.gz',
                         headers={'User-Agent': USER_AGENT})
        response = upstream.getresponse()
        location = response.getheader('Location', '')
        match = re.fullmatch(
            r'https://archive\.org/download/ol_dump_(\d{4}-\d{2}-\d{2})/ol_dump_editions_\1\.txt\.gz',
            location,
        )
        if not (300 <= response.status < 400 and match):
            raise ValueError(f'无法识别 Open Library 最新快照（HTTP {response.status}）')
        return match[1]
    finally:
        upstream.close()


def download_snapshot(date, destination):
    partial = destination.with_suffix('.part')
    url = f'https://archive.org/download/ol_dump_{date}/ol_dump_editions_{date}.txt.gz'
    try:
        with (urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=60) as response,
              partial.open('wb') as output):
            size = response.headers.get('Content-Length')
            if size and int(size) > shutil.disk_usage(destination.parent).free:
                raise OSError('书目缓存空间不足')
            if response.read(2) != b'\x1f\x8b':
                raise ValueError('Open Library 返回的文件不是 gzip')
            output.write(b'\x1f\x8b')
            shutil.copyfileobj(response, output, length=1024 * 1024)
            if size and output.tell() != int(size):
                raise OSError('Open Library 快照下载不完整')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


class Command(BaseCommand):
    help = '自动发现中文书目、补查藏书 ISBN 并写入公共书目'

    def add_arguments(self, parser):
        parser.add_argument('--discover', action='store_true', help='采集中文新书及出版社目录')
        parser.add_argument('--file', type=Path, help='导入单个 CSV 或 Open Library .gz 文件')
        parser.add_argument('--feed-dir', type=Path, help='定期读取此目录下的中文书目 CSV')
        parser.add_argument('--feed-url', default=os.environ.get('BIBLIOGRAPHY_FEED_URL', ''),
                            help='已获授权的中文书目 HTTPS CSV 地址')
        parser.add_argument('--open-library', action='store_true',
                            default=os.environ.get('BIBLIOGRAPHY_OPEN_LIBRARY') == '1')
        parser.add_argument('--cache-dir', type=Path, default=Path.home() / 'bibliography')
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=int, default=86400, help='正常轮询间隔（秒）')

    def handle(self, *args, **options):
        if not any(options[key] for key in ('discover', 'file', 'feed_dir', 'feed_url', 'open_library')):
            raise CommandError('请指定 --discover、--file、--feed-dir、--feed-url 或 --open-library')
        if options['interval'] < 1:
            raise CommandError('--interval 必须为正整数')
        retry = min(60, options['interval'])
        while True:
            try:
                self.run_once(options)
            except (CommandError, OperationalError) as exc:
                if not options['watch']:
                    raise
                self.stderr.write(f'书目采集失败，{retry} 秒后重试。{exc}')
                time.sleep(retry)
                retry = min(retry * 2, 21600, options['interval'])
            else:
                if not options['watch']:
                    return
                retry = min(60, options['interval'])
                time.sleep(options['interval'])

    def run_once(self, options):
        postgres = connection.vendor == 'postgresql'
        try:
            if postgres:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', [170143, 11401])
                    if not cursor.fetchone()[0]:
                        self.stdout.write('已有书目采集进程正在执行')
                        return
            inputs = [options['file']] if options['file'] else []
            failures = []
            try:
                if options['feed_dir']:
                    inputs.extend(sorted(options['feed_dir'].glob('*.csv')))
            except OSError as exc:
                failures.append(str(exc))
            for path in inputs:
                try:
                    self.import_file(path)
                except INPUT_ERRORS as exc:
                    failures.append(f'{path.name}：{exc}')
            if options['feed_url']:
                try:
                    self.import_feed_url(options['feed_url'])
                except INPUT_ERRORS as exc:
                    failures.append(f'中文书目 HTTPS 源：{exc}')
            if options['discover']:
                failures.extend(self.discover_chinese_books())
            if options['open_library']:
                try:
                    self.import_open_library(options['cache_dir'])
                except INPUT_ERRORS as exc:
                    failures.append(f'Open Library：{exc}')
            if failures:
                raise CommandError('；'.join(failures))
        finally:
            if postgres:
                connection.close()

    def import_file(self, path):
        if path.suffix.lower() not in {'.csv', '.gz'}:
            raise ValueError('只支持 CSV 和 Open Library gzip 快照')
        with path.open('rb') as raw:
            if path.suffix.lower() == '.csv':
                self.import_csv(raw)
            else:
                key = f'local-dump:{hashlib.file_digest(raw, "sha256").hexdigest()}'
                raw.seek(0)
                with gzip.GzipFile(fileobj=raw) as stream:
                    self.import_stream(key, stream, edition_rows)

    def import_feed_url(self, url):
        validate_feed_url(url)
        opener = build_opener(HTTPSFeedRedirect)
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/csv'})
        with opener.open(request, timeout=30) as response, TemporaryFile() as raw:
            shutil.copyfileobj(response, raw, length=1024 * 1024)
            size = response.headers.get('Content-Length')
            if size and raw.tell() != int(size):
                raise OSError('中文书目 CSV 下载不完整')
            raw.seek(0)
            self.import_csv(raw)

    def import_csv(self, raw):
        key = f'csv:{hashlib.file_digest(raw, "sha256").hexdigest()}'
        raw.seek(0)
        with io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as stream:
            self.import_stream(key, stream, feed_rows)

    def discover_chinese_books(self):
        today = timezone.localdate()
        sources = [
            ('cp:new', commercial_press_books),
            ('wuming:new', partial(wuming_toplist, 'new')),
            ('wuming:weekly', partial(wuming_toplist, 'weekly')),
            *((f'wuming:{key}', partial(wuming_publisher, publisher)) for key, publisher in PUBLISHERS),
        ]
        failures = []
        for name, fetch in sources:
            key = f'{name}:{today}'
            if not BibliographyImport.objects.filter(snapshot=key, completed_at__isnull=False).exists():
                try:
                    candidates = list(fetch())
                    self.import_stream(key, candidates, self.numbered_candidates)
                except INPUT_ERRORS as exc:
                    failures.append(f'{name}：{exc}')
        self.backfill_library(f'lookup:{today}')
        return failures

    def backfill_library(self, key):
        if BibliographyImport.objects.filter(snapshot=key, completed_at__isnull=False).exists():
            return
        now = timezone.now()
        attempts = dict(BibliographyLookup.objects.values_list('isbn', 'retry_after'))
        missing = sorted(pending_library_isbns(), key=lambda isbn: (
            not isbn.startswith('9787'), isbn in attempts, isbn,
        ))
        eligible = (isbn for isbn in missing if isbn not in attempts or attempts[isbn] <= now)
        candidates = []
        missed = []
        for isbn in islice(eligible, DAILY_ISBN_LOOKUPS):
            metadata = lookup_book(isbn, domestic_metadata_only=True)
            candidate = metadata_candidate(isbn, isbn, metadata)
            candidates.append(candidate)
            if not candidate:
                missed.append(BibliographyLookup(isbn=isbn, retry_after=now + timedelta(days=7)))
        with transaction.atomic():
            BibliographyLookup.objects.bulk_create(
                missed, update_conflicts=True, unique_fields=['isbn'], update_fields=['retry_after'],
            )
            self.import_stream(key, candidates, self.numbered_candidates)

    @staticmethod
    def numbered_candidates(candidates, offset):
        return enumerate(candidates[offset:], start=offset + 1)

    def import_open_library(self, cache_dir):
        pending = BibliographyImport.objects.filter(
            snapshot__startswith=OPEN_LIBRARY_PREFIX, completed_at__isnull=True,
        ).order_by('id').values_list('snapshot', flat=True).first()
        date = pending.removeprefix(OPEN_LIBRARY_PREFIX) if pending else latest_snapshot()
        key = OPEN_LIBRARY_PREFIX + date
        path = cache_dir / f'ol_dump_editions_{date}.txt.gz'
        if BibliographyImport.objects.filter(snapshot=key, completed_at__isnull=False).exists():
            path.unlink(missing_ok=True)
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self.stdout.write(f'正在下载 Open Library 快照 {date}')
            download_snapshot(date, path)
        try:
            with gzip.open(path, 'rb') as stream:
                self.import_stream(key, stream, edition_rows)
        except (gzip.BadGzipFile, EOFError, zlib.error):
            path.unlink(missing_ok=True)
            raise
        path.unlink()

    def import_stream(self, key, stream, rows):
        progress, _ = BibliographyImport.objects.get_or_create(snapshot=key)
        if progress.completed_at:
            return
        next_report = 0

        def report(state):
            nonlocal next_report
            now = time.monotonic()
            if state.completed_at or now >= next_report:
                status = '完成' if state.completed_at else '进行中'
                self.stdout.write(f'{key} {status} {state.counts}')
                next_report = now + 30

        import_candidates(rows(stream, progress.offset), progress, report)
