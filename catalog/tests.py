import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .isbn import normalize_isbn
from .lookup import lookup_book, lookup_juhe, lookup_showapi, lookup_wuming
from .models import Category, Copy, Edition


class LibraryFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='safe-test-password')
        self.client.force_login(self.user)

    def test_isbn_validation_and_normalization(self):
        self.assertEqual(normalize_isbn('0-14-032872-6'), '9780140328721')
        self.assertEqual(normalize_isbn('978-0-14-032872-1'), '9780140328721')
        with self.assertRaises(ValueError):
            normalize_isbn('9780140328722')

    def test_duplicate_isbn_adds_copy_without_overwriting_edition(self):
        url = reverse('book_new')
        response = self.client.post(url, {'isbn': '9780140328721', 'title': 'First title',
                                          'author': 'First author', 'status': 'unread',
                                          'location': 'A1'})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(url, {'isbn': '9780140328721', 'title': 'Different title',
                                          'status': 'read', 'location': 'B2'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.count(), 1)
        self.assertEqual(Copy.objects.count(), 2)
        self.assertEqual(Edition.objects.get().title, 'First title')

    def test_export_import_preserves_two_copies(self):
        book = Edition.objects.create(isbn='9780140328721', title='A book', author='An author')
        Copy.objects.create(edition=book, location='A1')
        Copy.objects.create(edition=book, location='B2', status='read')
        content = self.client.get(reverse('export_csv')).content
        Copy.objects.all().delete()
        Edition.objects.all().delete()
        response = self.client.post(reverse('import_csv'), {
            'file': SimpleUploadedFile('books.csv', content, content_type='text/csv')
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.count(), 1)
        self.assertEqual(set(Copy.objects.values_list('location', flat=True)), {'A1', 'B2'})

    def test_category_filter_includes_child(self):
        parent = Category.objects.create(name='文学')
        child = Category.objects.create(name='小说', parent=parent)
        book = Edition.objects.create(title='A book', category=child)
        Copy.objects.create(edition=book)
        response = self.client.get(reverse('book_list'), {'category': parent.pk})
        self.assertContains(response, 'A book')

    def test_pages_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('book_list')).status_code, 302)
        self.assertEqual(self.client.get(reverse('export_csv')).status_code, 302)

    def test_failed_import_rolls_back_all_rows(self):
        content = 'ISBN,书名\n9780140328721,Good\ninvalid,Bad\n'.encode()
        response = self.client.post(reverse('import_csv'), {
            'file': SimpleUploadedFile('books.csv', content, content_type='text/csv')
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Edition.objects.count(), 0)

    @patch('catalog.lookup.fetch_json')
    def test_wuming_lookup_uses_only_matching_isbn(self, fetch):
        fetch.return_value = {'isbn': '9787544258975', 'title': '霍乱时期的爱情',
                              'author': '加西亚·马尔克斯', 'publisher': '南海出版公司',
                              'pubDate': '2012-08-31', 'img': '/covers/9787544258975.jpg'}
        result = lookup_wuming('9787544258975')
        self.assertEqual(result['source'], '无名图书')
        self.assertEqual(result['published_year'], 2012)
        self.assertEqual(result['cover_url'], 'https://www.book345.com/covers/9787544258975.jpg')
        self.assertIsNone(lookup_wuming('9787208061644'))

    @patch.dict(os.environ, {'SHOWAPI_APP_KEY': 'test-key'})
    @patch('catalog.lookup.fetch_json')
    def test_showapi_response_mapping(self, fetch):
        fetch.return_value = {'showapi_res_code': 0, 'showapi_res_body': {
            'ret_code': 0, 'data': {'isbn': '9787208061644', 'title': '追风筝的人',
                                   'author': '卡勒德·胡赛尼', 'publisher': '上海人民出版社',
                                   'pubdate': '2006-05', 'img': 'http://example.com/cover.jpg'}}}
        result = lookup_showapi('9787208061644')
        self.assertEqual(result['title'], '追风筝的人')
        self.assertEqual(result['published_year'], 2006)
        self.assertEqual(result['cover_url'], 'https://example.com/cover.jpg')
        self.assertEqual(fetch.call_args.args[1], {'isbn': '9787208061644'})

    @patch.dict(os.environ, {'JUHE_ISBN_KEY': 'test-key'})
    @patch('catalog.lookup.fetch_json')
    def test_juhe_response_mapping_and_mismatch(self, fetch):
        fetch.return_value = {'error_code': 0, 'result': {'data': {
            'isbn': '9787544258975', 'title': '霍乱时期的爱情',
            'author': '加西亚·马尔克斯', 'publisher': '南海出版社',
            'pubDate': '201209', 'img': 'https://example.com/cover.jpg'}}}
        result = lookup_juhe('9787544258975')
        self.assertEqual(result['published_year'], 2012)
        self.assertEqual(result['source'], '聚合数据')
        self.assertIsNone(lookup_juhe('9787208061644'))

    @patch('catalog.lookup.lookup_wuming', return_value={'title': '国内书', 'source': '无名图书'})
    @patch('catalog.lookup.lookup_showapi')
    @patch('catalog.lookup.lookup_juhe')
    def test_chinese_isbn_stops_after_first_domestic_hit(self, juhe, showapi, wuming):
        self.assertEqual(lookup_book('9787544258975')['title'], '国内书')
        wuming.assert_called_once()
        showapi.assert_not_called()
        juhe.assert_not_called()

    @patch('catalog.lookup.lookup_wuming', return_value={'title': '国内书', 'source': '无名图书'})
    @patch('catalog.lookup.lookup_showapi')
    @patch('catalog.lookup.lookup_juhe')
    @patch('catalog.lookup.lookup_google')
    @patch('catalog.lookup.lookup_openlibrary')
    def test_non_chinese_isbn_also_prefers_domestic_sources(
            self, openlibrary, google, juhe, showapi, wuming):
        self.assertEqual(lookup_book('9780140328721')['title'], '国内书')
        wuming.assert_called_once_with('9780140328721')
        showapi.assert_not_called()
        juhe.assert_not_called()
        google.assert_not_called()
        openlibrary.assert_not_called()

    @patch('catalog.views.lookup_book', return_value={'title': '国内书', 'source': '无名图书'})
    def test_lookup_endpoint_caches_result(self, lookup):
        for _ in range(2):
            response = self.client.get(reverse('lookup_isbn'), {'isbn': '9787544258975'})
            self.assertEqual(response.json()['title'], '国内书')
        lookup.assert_called_once()
