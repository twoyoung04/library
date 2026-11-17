import io
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .isbn import isbn13_check_digit
from .models import Copy, Edition, PublicEdition, Tag


class PublicBibliographyTests(TestCase):
    isbn = '9780140328721'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model().objects
        self.first = users.create_user(username='first')
        self.second = users.create_user(username='second', password='safe-test-password')

    @patch('catalog.bibliography.lookup_book', return_value={
        'title': '公共书名', 'author': '作者', 'publisher': '出版社', 'source': '公开目录',
    })
    def test_web_and_ios_share_bibliography_but_keep_independent_books(self, lookup):
        self.client.force_login(self.first)
        saved = self.client.post(reverse('scan_save'), {
            'isbn': '0-14-032872-6', 'status': 'read', 'location': '个人书架', 'tags_text': '私有标签',
        })
        self.assertEqual(saved.status_code, 200)
        first_book = Edition.objects.get(owner=self.first)
        first_book.title = '个人改名'
        first_book.save(update_fields=['title'])
        own_hit = self.client.get(reverse('lookup_isbn'), {'isbn': self.isbn}).json()
        self.assertEqual(own_hit['title'], '个人改名')

        self.client.logout()
        cache.clear()
        public_hit = self.client.get('/api/mobile/metadata/', {'isbn': self.isbn}).json()
        self.assertEqual(public_hit['metadata'], {
            'title': '公共书名', 'author': '作者', 'publisher': '出版社',
            'published_year': None, 'cover_url': '',
        })
        self.assertFalse(public_hit['existing'])
        logged_in = self.client.post('/api/mobile/login/', {
            'username': self.second.username, 'password': 'safe-test-password',
        }, content_type='application/json')
        token = logged_in.json()['token']
        created = self.client.post('/api/mobile/copies/', {
            'request_id': str(uuid4()), 'isbn': self.isbn,
        }, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(created.status_code, 201)
        second_book = Edition.objects.get(owner=self.second)
        public = PublicEdition.objects.get(isbn=self.isbn)
        self.assertEqual(first_book.public_edition_id, public.pk)
        self.assertEqual(second_book.public_edition_id, public.pk)
        self.assertEqual(second_book.title, '公共书名')
        self.assertEqual(second_book.copies.get().location, '')
        self.assertEqual(second_book.copies.get().status, Copy.Status.UNREAD)
        self.assertEqual(second_book.tags.count(), 0)
        lookup.assert_called_once_with(self.isbn)

    @patch('catalog.bibliography.lookup_book', return_value={})
    def test_private_isbn_and_missing_metadata_do_not_enter_public_catalog(self, lookup):
        edition = Edition.objects.create(owner=self.first, isbn=self.isbn, title='未公开的书名')
        Copy.objects.create(edition=edition, notes='私有备注')
        edition.tags.add(Tag.objects.create(owner=self.first, name='私有标签'))
        self.client.force_login(self.second)
        seen = self.client.get(reverse('lookup_isbn'), {'isbn': self.isbn})
        self.assertEqual(seen.json(), {'existing': False, 'isbn': self.isbn})
        self.assertFalse(PublicEdition.objects.exists())
        lookup.assert_called_once_with(self.isbn)

    @patch('catalog.bibliography.lookup_book', return_value={'title': '公开的期刊', 'source': 'ISSN 目录'})
    def test_periodical_lookup_does_not_create_public_isbn_entry(self, lookup):
        stem = '977123456700'
        barcode = stem + str(isbn13_check_digit(stem))
        hit = self.client.get('/api/mobile/metadata/', {'isbn': barcode}).json()
        self.assertEqual(hit['metadata']['title'], '公开的期刊')
        self.assertFalse(PublicEdition.objects.exists())
        lookup.assert_called_once_with(barcode)

    def test_linking_historical_records_does_not_modify_private_fields(self):
        edition = Edition.objects.create(owner=self.first, isbn=self.isbn, title='历史记录')
        public = PublicEdition.objects.create(isbn=self.isbn, title='公共书名', source='公开目录')
        original_updated = edition.updated_at
        call_command('link_public_editions', stdout=io.StringIO())
        edition.refresh_from_db()
        self.assertEqual(edition.public_edition_id, public.pk)
        self.assertEqual(edition.title, '历史记录')
        self.assertEqual(edition.updated_at, original_updated)

        edition.isbn = None
        edition.save(update_fields=['isbn'])
        call_command('link_public_editions', stdout=io.StringIO())
        edition.refresh_from_db()
        self.assertIsNone(edition.public_edition_id)
