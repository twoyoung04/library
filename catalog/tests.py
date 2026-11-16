import os
from datetime import datetime
import json
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .isbn import normalize_isbn
from .lookup import (library_cover_candidates, lookup_book, lookup_cdclib, lookup_cp, lookup_douban, lookup_juhe,
                     lookup_neodb, lookup_showapi, lookup_wuming)
from .models import Copy, Edition, Tag


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


class MobileApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='safe-test-password')
        response = self.client.post('/api/mobile/login/',
                                    data=json.dumps({'username': 'owner', 'password': 'safe-test-password'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer ' + response.json()['token']}

    def test_auth_and_logout(self):
        self.assertEqual(self.client.get('/api/mobile/books/').status_code, 401)
        self.assertEqual(self.client.get('/api/mobile/me/', **self.auth).json()['username'], 'owner')
        self.assertEqual(self.client.post('/api/mobile/logout/', **self.auth).status_code, 200)
        self.assertEqual(self.client.get('/api/mobile/me/', **self.auth).status_code, 401)

    def test_other_users_private_book_tag_and_copy_are_isolated(self):
        other = get_user_model().objects.create_user(username='other')
        edition = Edition.objects.create(owner=other, title='Private book', isbn='9780140328721')
        tag = Tag.objects.create(owner=other, name='Private tag')
        edition.tags.add(tag)
        copy = Copy.objects.create(edition=edition, location='Private shelf')
        self.assertEqual(self.client.get('/api/mobile/snapshot/', **self.auth).json()['items'], [])
        self.assertEqual(self.client.get('/api/mobile/filters/', **self.auth).json(), {'tags': [], 'locations': []})
        self.assertEqual(self.client.get(f'/api/mobile/books/{edition.pk}/', **self.auth).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/mobile/copies/{copy.pk}/', **self.auth).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/books/{edition.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/tags/{tag.pk}/delete/').status_code, 404)
        self.assertNotIn('Private book', self.client.get('/export/').content.decode())
        Edition.objects.create(owner=self.user, title='My book', isbn=edition.isbn)
        Tag.objects.create(owner=self.user, name=tag.name)

    def test_sync_rejects_stale_edits_and_allows_retry_of_applied_edit(self):
        edition = Edition.objects.create(owner=self.user, title='Book')
        copy = Copy.objects.create(edition=edition, location='Old')
        before = self.client.get('/api/mobile/snapshot/', **self.auth).json()['items'][0]['copy']
        url = f'/api/mobile/copies/{copy.pk}/'
        data = json.dumps({'location': 'New'})
        headers = {**self.auth, 'HTTP_IF_MATCH': before['revision']}
        self.assertEqual(self.client.patch(url, data, content_type='application/json', **headers).status_code, 200)
        self.assertEqual(self.client.patch(url, data, content_type='application/json', **headers).status_code, 200)
        stale = self.client.patch(url, json.dumps({'location': 'Stale'}),
                                  content_type='application/json', **headers)
        self.assertEqual(stale.status_code, 412)
        self.assertEqual(self.client.delete(url, **headers).status_code, 412)
        self.assertEqual(Copy.objects.get(pk=copy.pk).location, 'New')

    def test_retry_does_not_restore_a_deleted_copy(self):
        data = json.dumps({'request_id': str(uuid4()), 'title': 'Book'})
        path = '/api/mobile/copies/'
        saved = self.client.post(path, data, content_type='application/json', **self.auth)
        self.assertEqual(saved.status_code, 201)
        copy_id = saved.json()['copy']['id']
        self.assertEqual(self.client.delete(f'{path}{copy_id}/', **self.auth).status_code, 200)
        self.assertEqual(self.client.post(path, data, content_type='application/json', **self.auth).status_code, 410)
        self.assertFalse(Copy.objects.exists())

    @patch('catalog.mobile_api._book_metadata', return_value={})
    def test_create_is_idempotent_and_reuses_edition(self, _lookup):
        first_id = str(uuid4())
        data = {'request_id': first_id, 'isbn': '9780140328721', 'location': '书架 A', 'tags': '小说'}
        first = self.client.post('/api/mobile/copies/', json.dumps(data),
                                 content_type='application/json', **self.auth)
        self.assertEqual(first.status_code, 201)
        repeated = self.client.post('/api/mobile/copies/', json.dumps(data),
                                    content_type='application/json', **self.auth)
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()['replayed'])
        self.assertEqual(Copy.objects.count(), 1)
        data['request_id'] = str(uuid4())
        another = self.client.post('/api/mobile/copies/', json.dumps(data),
                                   content_type='application/json', **self.auth)
        self.assertEqual(another.status_code, 201)
        self.assertEqual(Edition.objects.count(), 1)
        self.assertEqual(Copy.objects.count(), 2)
        detail = self.client.get(f"/api/mobile/books/{first.json()['edition_id']}/", **self.auth).json()
        self.assertEqual(detail['book']['copy_count'], 2)

    def test_search_update_and_delete(self):
        edition = Edition.objects.create(owner=self.user, title='Alpha', isbn='9780140328721')
        copy = Copy.objects.create(edition=edition, location='书架 B')
        self.assertEqual(self.client.get('/api/mobile/books/?q=Alpha', **self.auth).json()['total'], 1)
        self.assertEqual(self.client.get('/api/mobile/books/?location=missing', **self.auth).json()['total'], 0)
        edited = self.client.patch(f'/api/mobile/copies/{copy.pk}/',
                                   json.dumps({'location': '书架 C', 'status': 'reading'}),
                                   content_type='application/json', **self.auth)
        self.assertEqual(edited.json()['copy']['location'], '书架 C')
        self.assertEqual(edited.json()['copy']['status'], 'reading')
        self.assertEqual(self.client.delete(f'/api/mobile/copies/{copy.pk}/', **self.auth).status_code, 200)
        self.assertFalse(Edition.objects.filter(pk=edition.pk).exists())


class LibraryFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='safe-test-password')
        self.client.force_login(self.user)

    def test_isbn_validation_and_normalization(self):
        self.assertEqual(normalize_isbn('0-14-032872-6'), '9780140328721')
        self.assertEqual(normalize_isbn('978-0-14-032872-1'), '9780140328721')
        with self.assertRaises(ValueError):
            normalize_isbn('9780140328722')

    def test_book_list_query_count_stays_bounded(self):
        edition = Edition.objects.create(owner=self.user, isbn='9780140328721', title='A book')
        Copy.objects.bulk_create([Copy(edition=edition, status=Copy.Status.READ if n < 5
                                       else Copy.Status.UNREAD) for n in range(30)])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('book_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_copies'], 30)
        self.assertEqual(response.context['page'].paginator.count, 30)
        self.assertEqual(len(response.context['page'].object_list), 24)
        self.assertContains(response, 'class="book-results"')
        self.assertNotContains(response, 'class="stats"')
        self.assertLessEqual(len(queries), 7)

    def test_book_cards_hide_status_and_empty_tags(self):
        untagged = Edition.objects.create(owner=self.user, title='无标签图书')
        Copy.objects.create(edition=untagged, status=Copy.Status.READ, location='书架 A')
        tagged = Edition.objects.create(owner=self.user, title='有标签图书')
        tagged.tags.add(Tag.objects.create(owner=self.user, name='文学'))
        Copy.objects.create(edition=tagged)

        cards = self.client.get(reverse('book_list'), {'fragment': 'cards'}).json()['cards']
        self.assertNotIn('未加标签', cards)
        self.assertNotIn('已读', cards)
        self.assertIn('<div class="book-meta"><span>书架 A</span></div>', cards)
        self.assertEqual(cards.count('class="book-card-tags"'), 1)

    def test_book_list_sort_options_order_copies_and_keep_missing_metadata_last(self):
        alpha = Edition.objects.create(owner=self.user, title='Alpha', published_year=1999)
        beta = Edition.objects.create(owner=self.user, title='Beta', published_year=2020)
        untitled = Edition.objects.create(owner=self.user, title='')
        alpha_copy = Copy.objects.create(edition=alpha)
        beta_copy = Copy.objects.create(edition=beta)
        untitled_copy = Copy.objects.create(edition=untitled)
        for copy, year in ((alpha_copy, 2023), (beta_copy, 2022), (untitled_copy, 2024)):
            Copy.objects.filter(pk=copy.pk).update(
                created_at=timezone.make_aware(datetime(year, 1, 1, 12, 0)))

        expected = {
            'added_desc': [untitled_copy.pk, alpha_copy.pk, beta_copy.pk],
            'added_asc': [beta_copy.pk, alpha_copy.pk, untitled_copy.pk],
            'title_asc': [alpha_copy.pk, beta_copy.pk, untitled_copy.pk],
            'title_desc': [beta_copy.pk, alpha_copy.pk, untitled_copy.pk],
            'year_desc': [beta_copy.pk, alpha_copy.pk, untitled_copy.pk],
            'year_asc': [alpha_copy.pk, beta_copy.pk, untitled_copy.pk],
            'invalid': [untitled_copy.pk, alpha_copy.pk, beta_copy.pk],
        }
        for sort, copy_ids in expected.items():
            with self.subTest(sort=sort):
                response = self.client.get(reverse('book_list'), {'sort': sort})
                self.assertEqual([copy.pk for copy in response.context['page']], copy_ids)

    def test_book_list_pagination_preserves_sort_and_search(self):
        edition = Edition.objects.create(owner=self.user, title='Alpha')
        Copy.objects.bulk_create([Copy(edition=edition) for _ in range(25)])
        response = self.client.get(reverse('book_list'), {'q': 'Alpha', 'sort': 'title_asc'})
        self.assertContains(response, 'sort=title_asc&amp;page=2')

    def test_book_list_fragment_loads_next_page_with_same_filters(self):
        edition = Edition.objects.create(owner=self.user, title='Alpha')
        copies = Copy.objects.bulk_create([Copy(edition=edition) for _ in range(30)])
        first = self.client.get(reverse('book_list'), {'q': 'Alpha', 'sort': 'added_asc',
                                                       'fragment': 'cards'})
        second = self.client.get(reverse('book_list'), {'q': 'Alpha', 'sort': 'added_asc',
                                                        'page': '2', 'fragment': 'cards'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['page'], 1)
        self.assertEqual(first.json()['next_page'], 2)
        self.assertEqual(first.json()['cards'].count('class="book-card"'), 24)
        self.assertEqual(second.json()['page'], 2)
        self.assertIsNone(second.json()['next_page'])
        self.assertEqual(second.json()['cards'].count('class="book-card"'), 6)
        for copy in copies[24:]:
            self.assertIn(f'data-copy-id="{copy.pk}"', second.json()['cards'])
        for copy in copies[:24]:
            self.assertNotIn(f'data-copy-id="{copy.pk}"', second.json()['cards'])

    def test_book_detail_shows_entry_time_for_each_copy(self):
        edition = Edition.objects.create(owner=self.user, title='Alpha')
        dates = [timezone.make_aware(datetime(2024, 3, 4, 10, 30)),
                 timezone.make_aware(datetime(2025, 5, 6, 14, 45))]
        for created_at in dates:
            copy = Copy.objects.create(edition=edition)
            Copy.objects.filter(pk=copy.pk).update(created_at=created_at)
        response = self.client.get(reverse('book_detail', args=[edition.pk]))
        self.assertContains(response, '录入时间', count=2)
        for created_at in dates:
            self.assertContains(response, created_at.strftime('%Y-%m-%d %H:%M'))

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
        Edition.objects.create(owner=self.user, isbn='9787100202169', title='已有书名', author='已有作者')
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
        Edition.objects.create(owner=self.user, isbn='9787100202169', title='')
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
        Edition.objects.create(owner=self.user, isbn='9787100202169', title='')
        response = self.client.post(reverse('book_new'), {
            'isbn': '9787100202169', 'title': '培根随笔全集', 'status': 'unread',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.get().title, '培根随笔全集')
        self.assertEqual(Copy.objects.count(), 1)

    def test_export_import_preserves_two_copies(self):
        book = Edition.objects.create(owner=self.user, isbn='9780140328721', title='A book', author='An author')
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

    def test_tag_filter_finds_book_with_multiple_tags(self):
        literary = Tag.objects.create(owner=self.user, name='文学')
        novel = Tag.objects.create(owner=self.user, name='小说')
        book = Edition.objects.create(owner=self.user, title='A book')
        book.tags.add(literary, novel)
        Copy.objects.create(edition=book)
        response = self.client.get(reverse('book_list'), {'tag': literary.pk})
        self.assertContains(response, 'A book')
        self.assertContains(response, '小说')

    def test_manual_entry_accepts_any_number_of_tags(self):
        names = [f'标签{number}' for number in range(20)]
        response = self.client.post(reverse('book_new'), {
            'isbn': '9780140328721', 'title': '多标签图书', 'status': 'unread',
            'tags_text': '，'.join([*names, names[0]]),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(Edition.objects.get().tags.values_list('name', flat=True)), set(names))
        self.assertContains(self.client.get(reverse('book_list')), '+17')

    def test_edit_replaces_book_tags(self):
        book = Edition.objects.create(owner=self.user, title='A book')
        book.tags.add(Tag.objects.create(owner=self.user, name='旧标签'))
        response = self.client.post(reverse('book_edit', args=[book.pk]), {
            'title': 'A book', 'tags_text': '新标签一\n新标签二',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(book.tags.values_list('name', flat=True)),
                         {'新标签一', '新标签二'})

    def test_new_copy_adds_tags_without_removing_existing_ones(self):
        book = Edition.objects.create(owner=self.user, isbn='9780140328721', title='已有图书')
        book.tags.add(Tag.objects.create(owner=self.user, name='原标签'))
        response = self.client.post(reverse('book_new'), {
            'isbn': '9780140328721', 'title': '已有图书', 'status': 'unread',
            'tags_text': '新标签',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(book.tags.values_list('name', flat=True)), {'原标签', '新标签'})

    def test_scan_save_applies_tags(self):
        with patch('catalog.views._book_metadata', return_value={}):
            response = self.client.post(reverse('scan_save'), {
                'isbn': '9787100202169', 'status': 'unread',
                'tags_text': '待整理，历史',
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(Edition.objects.get().tags.values_list('name', flat=True)),
                         {'待整理', '历史'})

    def test_tags_manage_create_and_delete(self):
        response = self.client.post(reverse('tags'), {'name': '待整理'})
        self.assertEqual(response.status_code, 302)
        tag = Tag.objects.get(name='待整理')
        book = Edition.objects.create(owner=self.user, title='A book')
        book.tags.add(tag)
        Copy.objects.create(edition=book)
        self.assertContains(self.client.get(reverse('tags')), '1 个版本')
        self.assertRedirects(self.client.get(reverse('categories')), reverse('tags'))
        self.assertEqual(self.client.post(reverse('tag_delete', args=[tag.pk])).status_code, 302)
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
        self.assertTrue(Edition.objects.filter(pk=book.pk).exists())

    def test_legacy_csv_category_imports_as_tag(self):
        csv_data = 'ISBN,书名,分类,标签\n9780140328721,旧版图书,文学,经典、小说\n'.encode()
        response = self.client.post(reverse('import_csv'), {
            'file': SimpleUploadedFile('books.csv', csv_data, content_type='text/csv')
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(Edition.objects.get().tags.values_list('name', flat=True)),
                         {'文学', '经典', '小说'})
        exported = self.client.get(reverse('export_csv')).content.decode('utf-8-sig')
        self.assertIn('标签', exported.splitlines()[0])
        self.assertNotIn('分类', exported.splitlines()[0])

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

    @patch('catalog.lookup.fetch_html')
    def test_douban_reads_matching_structured_book(self, fetch):
        fetch.return_value = '''
            <script type="application/ld+json">{"@type":"Book","name":"爱弥儿",
              "isbn":"9787107313325","author":[{"name":"让-雅克·卢梭"}]}</script>
            <meta property="og:image" content="https://example.com/cover.jpg">
            <span class="pl">出版社:</span> <a>人民教育出版社</a><br>
            <span class="pl">出版年:</span> 2017-5-1<br>'''
        result = lookup_douban('9787107313325')
        self.assertEqual(result['title'], '爱弥儿')
        self.assertEqual(result['author'], '让-雅克·卢梭')
        self.assertEqual(result['publisher'], '人民教育出版社')
        self.assertEqual(result['published_year'], 2017)
        self.assertEqual(result['cover_url'], 'https://example.com/cover.jpg')
        self.assertEqual(result['source'], '豆瓣读书')
        fetch.return_value = fetch.return_value.replace('9787107313325', '9787100202169')
        self.assertIsNone(lookup_douban('9787107313325'))

    @patch('catalog.lookup.fetch_html')
    def test_cdclib_ignores_other_editions(self, fetch):
        fetch.return_value = '''
            <li class="libBookLi notBorder"><a class="libBookDetNm">其他版本</a>
              <img isbn="978-7-100-20216-9"></li>
            <li class="libBookLi notBorder"><a class="libBookDetNm">爱弥儿：论教育．上册</a>
              <img isbn="978-7-107-31332-5">
              <span class="libBkDetTit">责任者</span><a>卢梭著</a></p>
              <span class="libBkDetTit">出版信息</span><a>人民教育出版社</a>,2017</p>
            </li>'''
        result = lookup_cdclib('9787107313325')
        self.assertEqual(result['title'], '爱弥儿：论教育．上册')
        self.assertEqual(result['author'], '卢梭著')
        self.assertEqual(result['publisher'], '人民教育出版社')
        self.assertEqual(result['published_year'], 2017)
        self.assertEqual(result['source'], '成都图书馆')
        self.assertIn('searchWay=isbn', fetch.call_args.args[0])
        self.assertIsNone(lookup_cdclib('9787208061644'))

    @patch('catalog.lookup.fetch_json')
    @patch('catalog.lookup.lookup_wuming', return_value={'title': '已有书名', 'source': '无名图书'})
    def test_library_cover_fills_missing_cover_without_replacing_metadata(self, wuming, fetch):
        isbn = '9787544258975'
        cover = 'https://tc-cover-prod.oss-cn-shenzhen.aliyuncs.com/cover.jpg'
        fetch.return_value = [
            {'isbn': isbn, 'ossUrl': 'default.jpg', 'signUrl': cover, 'isDefaultCover': True},
            {'isbn': '9787107313325', 'ossUrl': 'other.jpg', 'signUrl': cover},
            {'isbn': isbn, 'ossUrl': 'cover.jpg', 'signUrl': cover + '?Expires=123',
             'title': '图书馆书名'},
        ]
        self.assertEqual(lookup_book(isbn), {
            'title': '已有书名', 'source': '无名图书', 'cover_url': cover,
        })
        fetch.assert_called_once()

    @patch('catalog.lookup.fetch_json')
    def test_library_cover_falls_back_to_dataesb(self, fetch):
        isbn = '9787544258975'
        cover = 'https://book-resource.dataesb.com/cover/example.jpg'
        fetch.side_effect = [None, {'result': [
            {'isbn': '9787107313325', 'status': 0, 'resourceLink': cover},
            {'isbn': isbn, 'status': 1, 'resourceLink': cover},
            {'isbn': isbn, 'status': 0, 'resourceLink': cover},
        ]}]
        self.assertEqual(list(library_cover_candidates(isbn)), [
            (cover, '成都图书馆封面数据源', {}),
        ])
        self.assertTrue(fetch.call_args.kwargs['jsonp'])

    @patch('catalog.lookup.lookup_wuming', return_value=None)
    @patch('catalog.lookup.lookup_neodb', return_value=None)
    @patch('catalog.lookup.lookup_cp', return_value=None)
    @patch('catalog.lookup.lookup_douban', return_value=None)
    @patch('catalog.lookup.lookup_cdclib', return_value={'title': '爱弥儿', 'cover_url': 'https://example.com/cover.jpg', 'source': '成都图书馆'})
    @patch('catalog.lookup.lookup_google')
    def test_domestic_library_hit_precedes_google(self, google, cdclib, douban, cp, neodb, wuming):
        self.assertEqual(lookup_book('9787107313325')['source'], '成都图书馆')
        cdclib.assert_called_once_with('9787107313325')
        google.assert_not_called()

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

    @patch('catalog.lookup.lookup_wuming', return_value={'title': '国内书', 'cover_url': 'https://example.com/cover.jpg', 'source': '无名图书'})
    @patch('catalog.lookup.lookup_showapi')
    @patch('catalog.lookup.lookup_juhe')
    def test_chinese_isbn_stops_after_first_domestic_hit(self, juhe, showapi, wuming):
        self.assertEqual(lookup_book('9787544258975')['title'], '国内书')
        wuming.assert_called_once()
        showapi.assert_not_called()
        juhe.assert_not_called()

    @patch('catalog.lookup.lookup_wuming', return_value=None)
    @patch('catalog.lookup.lookup_neodb', return_value={'title': '培根随笔全集', 'cover_url': 'https://example.com/cover.jpg', 'source': 'NeoDB'})
    @patch('catalog.lookup.lookup_cp')
    @patch('catalog.lookup.lookup_google')
    def test_neodb_hit_precedes_other_sources(self, google, cp, neodb, wuming):
        self.assertEqual(lookup_book('9787100202169')['title'], '培根随笔全集')
        wuming.assert_called_once_with('9787100202169')
        neodb.assert_called_once_with('9787100202169')
        cp.assert_not_called()
        google.assert_not_called()

    @patch('catalog.lookup.lookup_wuming', return_value={'title': '国内书', 'cover_url': 'https://example.com/cover.jpg', 'source': '无名图书'})
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
