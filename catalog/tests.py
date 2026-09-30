import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import Client
from django.urls import reverse

from .isbn import normalize_isbn
from .lookup import lookup_book, lookup_cp, lookup_juhe, lookup_neodb, lookup_showapi, lookup_wuming
from .models import Category, Copy, Edition


class LoginCsrfTests(TestCase):
    def test_duplicate_login_after_success_redirects_to_library(self):
        get_user_model().objects.create_user(username='owner', password='safe-test-password')
        client = Client(enforce_csrf_checks=True)
        login_url = reverse('login')
        client.get(login_url)
        old_token = client.cookies['csrftoken'].value
        credentials = {
            'username': 'owner',
            'password': 'safe-test-password',
            'csrfmiddlewaretoken': old_token,
        }

        first = client.post(login_url, credentials)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first['Location'], reverse('book_list'))
        self.assertNotEqual(client.cookies['csrftoken'].value, old_token)

        repeated = client.post(login_url, credentials)
        self.assertRedirects(repeated, reverse('book_list'))

    def test_invalid_csrf_still_rejected_for_anonymous_user(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('login'))
        response = client.post(reverse('login'), {
            'username': 'owner',
            'password': 'wrong',
            'csrfmiddlewaretoken': 'invalid',
        })
        self.assertEqual(response.status_code, 403)


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

    @patch('catalog.views._book_metadata', return_value={
        'title': '培根随笔全集', 'author': '弗朗西斯·培根',
        'publisher': '商务印书馆', 'published_year': 2021,
        'cover_url': 'https://example.com/cover.jpg', 'source': 'NeoDB',
    })
    def test_scan_auto_save_creates_edition_and_copy(self, metadata):
        response = self.client.post(reverse('scan_save'), {
            'isbn': '9787100202169', 'status': 'read', 'location': '书架 A',
            'acquired_on': '2026-09-30',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], '培根随笔全集')
        self.assertFalse(response.json()['needs_details'])
        self.assertEqual(Edition.objects.get().publisher, '商务印书馆')
        copy = Copy.objects.get()
        self.assertEqual(copy.location, '书架 A')
        self.assertEqual(copy.status, Copy.Status.READ)
        metadata.assert_called_once_with('9787100202169')

    @patch('catalog.views._book_metadata', return_value={})
    def test_scan_auto_save_preserves_isbn_without_metadata(self, metadata):
        response = self.client.post(reverse('scan_save'), {
            'isbn': '9787100202169', 'status': 'unread',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['needs_details'])
        edition = Edition.objects.get()
        self.assertEqual(edition.title, '')
        self.assertEqual(edition.isbn, '9787100202169')
        self.assertContains(self.client.get(reverse('book_list')), '待补充书名')
        self.assertContains(self.client.get(reverse('book_detail', args=[edition.pk])), '9787100202169')

        csv_data = self.client.get(reverse('export_csv')).content
        Copy.objects.all().delete()
        Edition.objects.all().delete()
        imported = self.client.post(reverse('import_csv'), {
            'file': SimpleUploadedFile('books.csv', csv_data, content_type='text/csv')
        })
        self.assertEqual(imported.status_code, 302)
        self.assertEqual(Edition.objects.get(isbn='9787100202169').title, '')
        metadata.assert_called_once()

    @patch('catalog.views._book_metadata')
    def test_scan_existing_isbn_adds_copy_without_overwriting(self, metadata):
        Edition.objects.create(isbn='9787100202169', title='已有书名', author='已有作者')
        first = self.client.post(reverse('scan_save'), {'isbn': '9787100202169', 'status': 'unread'})
        second = self.client.post(reverse('scan_save'), {'isbn': '9787100202169', 'status': 'unread'})
        self.assertEqual(first.json()['copy_count'], 1)
        self.assertEqual(second.json()['copy_count'], 2)
        self.assertEqual(Edition.objects.count(), 1)
        self.assertEqual(Copy.objects.count(), 2)
        self.assertEqual(Edition.objects.get().author, '已有作者')
        metadata.assert_not_called()

    @patch('catalog.views._book_metadata', return_value={'title': '补全书名', 'author': '作者'})
    def test_scan_enriches_existing_isbn_only_record(self, metadata):
        Edition.objects.create(isbn='9787100202169', title='')
        response = self.client.post(reverse('scan_save'), {'isbn': '9787100202169', 'status': 'unread'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Edition.objects.get().title, '补全书名')
        self.assertEqual(Copy.objects.count(), 1)
        metadata.assert_called_once()

    @patch('catalog.views._book_metadata')
    def test_scan_save_rejects_invalid_isbn_and_copy_fields(self, metadata):
        self.assertEqual(self.client.get(reverse('scan_save')).status_code, 405)
        self.assertEqual(self.client.post(reverse('scan_save'), {
            'isbn': 'invalid', 'status': 'unread',
        }).status_code, 400)
        self.assertEqual(self.client.post(reverse('scan_save'), {
            'isbn': '9787100202169', 'status': 'invalid',
        }).status_code, 400)
        self.assertEqual(Edition.objects.count(), 0)
        self.assertEqual(Copy.objects.count(), 0)
        metadata.assert_not_called()

    def test_scan_save_requires_login_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('scan_save'), {
            'isbn': '9787100202169', 'status': 'unread',
        }).status_code, 403)
        page = client.get(reverse('book_new'))
        self.assertContains(page, 'id="auto-save"')
        self.assertContains(page, 'id="continuous-scan"')
        with patch('catalog.views._book_metadata', return_value={}):
            self.assertEqual(client.post(reverse('scan_save'), {
                'isbn': '9787100202169', 'status': 'unread',
                'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
            }).status_code, 200)
        anonymous = Client()
        self.assertEqual(anonymous.post(reverse('scan_save'), {
            'isbn': '9787100202169', 'status': 'unread',
        }).status_code, 302)

    def test_manual_entry_completes_isbn_only_record(self):
        Edition.objects.create(isbn='9787100202169', title='')
        response = self.client.post(reverse('book_new'), {
            'isbn': '9787100202169', 'title': '培根随笔全集', 'status': 'unread',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.get().title, '培根随笔全集')
        self.assertEqual(Copy.objects.count(), 1)

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

    @patch('catalog.lookup.fetch_json')
    def test_neodb_matches_exact_edition_isbn(self, fetch):
        fetch.return_value = {'data': [
            {'isbn': '9787100202176', 'title': '同名的其他版本'},
            {'isbn': '978-7-100-20216-9', 'title': '培根随笔全集',
             'author': ['[英]弗朗西斯·培根'], 'publisher': ['商务印书馆'],
             'pub_year': 2021, 'cover_image_url': 'https://neodb.social/cover.jpg'},
        ]}
        result = lookup_neodb('9787100202169')
        self.assertEqual(result['title'], '培根随笔全集')
        self.assertEqual(result['author'], '[英]弗朗西斯·培根')
        self.assertEqual(result['publisher'], '商务印书馆')
        self.assertEqual(result['published_year'], 2021)
        self.assertEqual(result['source'], 'NeoDB')
        fetch.return_value = {'data': [{'isbn': '9787100202176', 'title': '同名的其他版本'}]}
        self.assertIsNone(lookup_neodb('9787100202169'))

    @patch('catalog.lookup.fetch_html')
    def test_cp_search_checks_detail_isbn(self, fetch):
        fetch.side_effect = [
            '<h2><a href="/book/example-9.html">培根随笔全集</a></h2>',
            '<title>培根随笔全集</title><div class="book_pic"><img src="https://pic.cp.com.cn/cover.jpg"></div>'
            '<li>出版时间：2021年10月</li><li>ISBN：978-7-100-20216-9</li>',
        ]
        result = lookup_cp('9787100202169')
        self.assertEqual(result['title'], '培根随笔全集')
        self.assertEqual(result['publisher'], '商务印书馆')
        self.assertEqual(result['published_year'], 2021)
        self.assertEqual(result['cover_url'], 'https://pic.cp.com.cn/cover.jpg')
        self.assertIn('978-7-100-20216-9', fetch.call_args_list[0].args[0])
        fetch.side_effect = [
            '<h2><a href="/book/example-9.html">培根随笔全集</a></h2>',
            '<title>培根随笔全集</title><li>ISBN：978-7-100-20217-6</li>',
        ]
        self.assertIsNone(lookup_cp('9787100202169'))

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

    @patch('catalog.lookup.lookup_wuming', return_value=None)
    @patch('catalog.lookup.lookup_neodb', return_value={'title': '培根随笔全集', 'source': 'NeoDB'})
    @patch('catalog.lookup.lookup_cp')
    @patch('catalog.lookup.lookup_google')
    def test_neodb_hit_precedes_other_sources(self, google, cp, neodb, wuming):
        self.assertEqual(lookup_book('9787100202169')['title'], '培根随笔全集')
        wuming.assert_called_once_with('9787100202169')
        neodb.assert_called_once_with('9787100202169')
        cp.assert_not_called()
        google.assert_not_called()

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
