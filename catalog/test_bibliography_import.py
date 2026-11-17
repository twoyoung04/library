import gzip
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .bibliography_import import save_candidate
from .isbn import isbn13_check_digit
from .models import BibliographyConflict, BibliographyImport, BibliographyLookup, Edition, PublicEdition


class BibliographyCollectionTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.reader = get_user_model().objects.create_user(username='reader')

    def collect(self, **options):
        call_command('collect_bibliography', stdout=io.StringIO(), **options)

    def test_feed_fills_missing_fields_and_retains_conflicts(self):
        isbn = '9780140328721'
        periodical_stem = '977100536000'
        periodical = periodical_stem + str(isbn13_check_digit(periodical_stem))
        existing = PublicEdition.objects.create(isbn=isbn, title='旧书名', publisher='原出版社', source='扫码')
        feed = self.root / 'publisher.csv'
        feed.write_text(
            'isbn,title,source,author,publisher,published_year\n'
            '0-14-032872-6,旧书名,出版社目录,作者,原出版社,2009\n'
            '9780140328721,错误书名,另一来源,另一作者,其他出版社,2010\n'
            '9780140328720,校验位错误,出版社目录,,,,\n'
            f'{periodical},期刊,出版社目录,,,,\n',
            encoding='utf-8-sig',
        )
        self.collect(feed_dir=self.root)
        feed.rename(self.root / 'copied.csv')
        self.collect(feed_dir=self.root)

        existing.refresh_from_db()
        self.assertEqual((existing.title, existing.author, existing.published_year), ('旧书名', '作者', 2009))
        self.assertEqual(existing.source, '扫码')
        self.assertEqual(existing.collected_fields['author']['source'], '出版社目录')
        self.assertEqual(BibliographyConflict.objects.get().candidate['title'], '错误书名')
        self.assertEqual(PublicEdition.objects.count(), 1)
        self.assertEqual(BibliographyImport.objects.get().counts, {
            'scanned': 4, 'filled': 1, 'conflicts': 1, 'skipped': 2,
        })

    def test_failed_batch_rolls_back_and_resumes_at_last_committed_row(self):
        feed = self.root / 'publisher.csv'
        feed.write_text('isbn,title,source\n'
                        '9780140328721,第一本,出版社目录\n'
                        '9780140328721,第二本,出版社目录\n', encoding='utf-8')
        def interrupt_second_batch(snapshot, isbn, source, source_key, fields):
            result = save_candidate(snapshot, isbn, source, source_key, fields)
            if result == 'conflicts':
                raise OSError('模拟中断')
            return result

        with (patch('catalog.bibliography_import.ISBN_BATCH_SIZE', 1),
              patch('catalog.bibliography_import.save_candidate', side_effect=interrupt_second_batch)):
            with self.assertRaisesMessage(CommandError, '模拟中断'):
                self.collect(file=feed)
        progress = BibliographyImport.objects.get()
        self.assertEqual(progress.offset, 1)
        self.assertFalse(BibliographyConflict.objects.exists())
        self.assertIsNone(progress.completed_at)

        self.collect(file=feed)
        progress.refresh_from_db()
        self.assertEqual(progress.offset, 2)
        self.assertEqual(progress.counts['created'], 1)
        self.assertEqual(progress.counts['conflicts'], 1)
        self.assertIsNotNone(progress.completed_at)

    @patch('catalog.management.commands.collect_bibliography.build_opener')
    def test_remote_feed_retries_and_deduplicates_against_local_feed(self, build_opener):
        contents = 'isbn,title,source\n9780140328721,中文样书,出版社目录\n'.encode()
        local = self.root / 'publisher.csv'
        local.write_bytes(contents)
        response = io.BytesIO(contents)
        response.headers = {'Content-Length': str(len(contents))}
        opener = build_opener.return_value
        opener.open.side_effect = [URLError('临时故障'), response]
        url = 'https://publisher.example/feed.csv'
        with self.assertRaisesMessage(CommandError, '临时故障'):
            self.collect(feed_dir=self.root, feed_url=url)
        self.collect(feed_dir=self.root, feed_url=url)

        self.assertEqual(PublicEdition.objects.get().source, '出版社目录')
        self.assertEqual(BibliographyImport.objects.get().counts['created'], 1)
        self.assertEqual(opener.open.call_count, 2)

    @patch('catalog.management.commands.collect_bibliography.download_snapshot')
    @patch('catalog.management.commands.collect_bibliography.latest_snapshot', return_value='2026-10-31')
    def test_automated_snapshot_downloads_each_release_once(self, latest, download):
        stem = '978750000001'
        isbn = stem + str(isbn13_check_digit(stem))
        payload = json.dumps({'isbn_13': [isbn], 'title': '中文样书'})
        lines = f'/type/edition\t/books/OL1M\t1\t2026-01-01\t{payload}\n'
        download.side_effect = lambda date, path: path.write_bytes(gzip.compress(lines.encode()))
        self.collect(open_library=True, cache_dir=self.root)
        self.collect(open_library=True, cache_dir=self.root)

        self.assertEqual(latest.call_count, 2)
        download.assert_called_once()
        self.assertEqual(PublicEdition.objects.get().isbn, isbn)
        self.assertEqual(BibliographyImport.objects.get().snapshot, 'openlibrary:2026-10-31')
        self.assertEqual(list(self.root.iterdir()), [])

    @patch('catalog.bibliography.lookup_book')
    def test_snapshot_prioritizes_chinese_and_backfills_foreign_library_books(self, lookup):
        chinese_stem = '978750000001'
        chinese = chinese_stem + str(isbn13_check_digit(chinese_stem))
        english_stem = '978750000002'
        english = english_stem + str(isbn13_check_digit(english_stem))
        private = Edition.objects.create(owner=self.reader, isbn='9780140328721', title='私人书名')
        input_dump = self.root / 'edition.gz'
        records = [
            {'isbn_13': [chinese], 'title': '中文样书', 'publishers': ['示例出版社']},
            {'isbn_10': ['0140328726'], 'title': 'Foreign library book'},
            {'isbn_13': [english], 'title': 'English in China', 'languages': [{'key': '/languages/eng'}]},
            {'isbn_13': ['9780141182803'], 'title': 'Unrelated foreign book'},
            {'isbn_13': ['9780141182803', chinese], 'title': 'Ambiguous ISBN'},
        ]
        with gzip.open(input_dump, 'wt', encoding='utf-8') as stream:
            for index, record in enumerate(records):
                stream.write(f'/type/edition\t/books/OL{index}M\t1\t2026-01-01\t{json.dumps(record)}\n')
        self.collect(file=input_dump)
        self.collect(file=input_dump)

        private.refresh_from_db()
        self.assertEqual(private.title, '私人书名')
        self.assertEqual(set(PublicEdition.objects.values_list('isbn', flat=True)), {chinese, private.isbn})
        self.client.force_login(self.reader)
        web = self.client.get(reverse('lookup_isbn'), {'isbn': chinese}).json()
        mobile = self.client.get('/api/mobile/metadata/', {'isbn': chinese}).json()
        self.assertEqual(web['title'], '中文样书')
        self.assertEqual(mobile['metadata']['title'], '中文样书')
        lookup.assert_not_called()

    @patch('catalog.management.commands.collect_bibliography.lookup_book', return_value=None)
    @patch('catalog.bibliography_sources.sleep')
    @patch('catalog.bibliography_sources.fetch_html')
    @patch('catalog.bibliography_sources.fetch_json')
    def test_discovery_uses_live_source_shapes_and_isolates_failures(self, fetch_json, fetch_html,
                                                                  pause, lookup):
        missing = '9780140328721'
        Edition.objects.create(owner=self.reader, isbn=missing, title='个人书名')
        cp_isbn = '9787100268691'
        wuming_isbn = '9787020182220'
        sources = {
            '人民文学出版社': {
                'isbn': wuming_isbn, 'title': '中文文学样书', 'publisher': '人民文学出版社',
                'author': '示例作者', 'pubDate': '2025-04',
            },
            '译林出版社': {
                'isbn': '9787544785327', 'title': '中文译著样书', 'publisher': '译林出版社',
            },
        }

        def html(url, **kwargs):
            if url.endswith('/book/new.dhtml'):
                return '<h2><a href="/book/example-1.html">书目</a></h2>'
            if url.endswith('/book/example-1.html'):
                return ('<title>中文学术样书</title><li>ISBN：978-7-100-26869-1</li>'
                        '<li>出版时间：2026年10月</li>'
                        '<li class="zuozhe">著者：<script>zith2(\'|示例作者|0,\')</script></li>')
            publisher = parse_qs(urlsplit(url).query)['publisher'][0]
            book = sources.get(publisher)
            books = [book, sources['译林出版社']] if book else []
            payload = {'props': {'pageProps': {'booksArr': books}}}
            return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

        fetch_html.side_effect = html
        fetch_json.side_effect = [URLError('暂时失败'), {'books': [{
            'isbn': cp_isbn, 'title': '中文学术样书', 'publisher': '商务印书馆', 'pub_date': '2026-10',
        }]}, {'books': []}]
        with self.assertRaisesMessage(CommandError, 'wuming:new'):
            self.collect(discover=True)
        self.assertEqual(PublicEdition.objects.get(isbn=cp_isbn).source, '商务印书馆')
        self.assertEqual(PublicEdition.objects.get(isbn=cp_isbn).author, '示例作者')
        self.assertEqual(PublicEdition.objects.get(isbn=wuming_isbn).published_year, 2025)
        self.assertEqual(PublicEdition.objects.count(), 3)
        pause.assert_called_once_with(1)
        lookup.assert_called_once_with(missing, domestic_metadata_only=True)
        self.assertTrue(BibliographyLookup.objects.filter(isbn=missing).exists())
        previous_requests = fetch_html.call_count
        self.collect(discover=True)
        self.assertEqual(fetch_html.call_count, previous_requests)
        self.assertEqual(fetch_json.call_count, 3)
        self.assertEqual(PublicEdition.objects.count(), 3)
