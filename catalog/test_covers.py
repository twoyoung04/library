import io
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from PIL import Image
from .covers import download_image, source_prefix, sync_edition, validate_url
from .models import Edition


@override_settings(COS_ENABLED=True, COS_BUCKET='cover-1307828558', COS_REGION='ap-shanghai', COS_PUBLIC_BASE_URL='https://cover-1307828558.cos.ap-shanghai.myqcloud.com')
class CoverTests(TestCase):
    def test_external_cover_never_appears_in_page_image_url(self):
        e = Edition.objects.create(title='Test', cover_url='https://external.example/cover.jpg')
        self.assertEqual(e.cover_display_url, '')
        e.cover_object_key = source_prefix(e.cover_url) + 'stored.jpg'
        self.assertTrue(e.cover_display_url.startswith('https://cover-1307828558.cos.ap-shanghai.myqcloud.com/covers/'))
        e.cover_url = 'https://other.example/new.jpg'
        self.assertEqual(e.cover_display_url, '')

    @patch('catalog.covers.download_image', return_value=b'jpeg')
    def test_upload_failure_preserves_source_and_does_not_mark_migrated(self, download):
        e = Edition.objects.create(cover_url='https://external.example/cover.jpg')
        storage = Mock()
        storage.put_object.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            sync_edition(e, storage)
        e.refresh_from_db()
        self.assertEqual(e.cover_object_key, '')
        self.assertEqual(e.cover_url, 'https://external.example/cover.jpg')

    @patch('catalog.covers.download_image', return_value=b'jpeg')
    def test_concurrent_edit_does_not_attach_stale_image(self, download):
        e = Edition.objects.create(cover_url='https://external.example/old.jpg')
        storage = Mock()
        storage.put_object.side_effect = lambda **kw: Edition.objects.filter(pk=e.pk).update(cover_url='https://external.example/new.jpg')
        self.assertFalse(sync_edition(e, storage))
        e.refresh_from_db()
        self.assertEqual(e.cover_object_key, '')

    def test_unsafe_urls_rejected(self):
        for url in ['file:///etc/passwd', 'http://user:password@example.com/a', 'http://example.com:22/']:
            with self.assertRaises(ValueError):
                validate_url(url)

    @patch('urllib.request.OpenerDirector.open')
    def test_download_validates_and_normalizes_image(self, open_request):
        image = io.BytesIO()
        Image.new('RGB', (10, 20)).save(image, format='PNG')
        open_request.return_value.__enter__.return_value.read.return_value = image.getvalue()
        data = download_image('https://example.com/cover.png')
        self.assertTrue(data.startswith(b'\xff\xd8'))

    def test_private_ip_is_rejected_before_sending_http_request(self):
        from .covers import PublicHTTPConnection
        connection = PublicHTTPConnection('127.0.0.1')
        connection.sock = Mock()
        connection.sock.getpeername.return_value = ('127.0.0.1', 80)
        with patch('http.client.HTTPConnection.connect'):
            with self.assertRaises(ValueError):
                connection.connect()

    def test_cover_endpoint_requires_login(self):
        e = Edition.objects.create(cover_url='https://external.example/cover.jpg')
        response = self.client.get(f'/books/{e.pk}/cover/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_known_placeholder_key_is_not_considered_a_stored_cover(self):
        from .covers import PLACEHOLDER_HASHES, current_key
        e = Edition(cover_url='https://example.com/cover.jpg')
        e.cover_object_key = source_prefix(e.cover_url) + next(iter(PLACEHOLDER_HASHES)) + '.jpg'
        self.assertFalse(current_key(e))
        self.assertEqual(e.cover_display_url, '')

    @patch('catalog.lookup.fetch_json')
    def test_wuming_prefers_original_large_cover_over_placeholder_route(self, fetch):
        from .lookup import lookup_wuming
        fetch.return_value = {'isbn': '9787513142434', 'title': '1984',
                              'img': '/covers/9787513142434.jpg',
                              'dbImg': 'https://img2.doubanio.com/small.jpg',
                              'doubanExtra': {'coverUrl': 'https://img2.doubanio.com/large.jpg'}}
        self.assertEqual(lookup_wuming('9787513142434')['cover_url'],
                         'https://img2.doubanio.com/large.jpg')

    @patch('catalog.lookup.lookup_wuming', return_value={'cover_url': 'https://img2.doubanio.com/original.jpg'})
    @patch('catalog.covers.download_image', return_value=b'jpeg')
    def test_background_transfer_uses_original_for_wuming(self, download, lookup):
        e = Edition.objects.create(isbn='9787513142434', cover_url='https://www.book345.com/covers/9787513142434.jpg')
        self.assertTrue(sync_edition(e, Mock()))
        download.assert_called_once_with('https://img2.doubanio.com/original.jpg')

    @patch('catalog.lookup.library_cover_candidates')
    @patch('catalog.covers.download_image', side_effect=[ValueError('placeholder'), b'jpeg'])
    def test_sync_refreshes_library_signature_and_tries_next_cover(self, download, lookup):
        source = 'https://tc-cover-prod.oss-cn-shenzhen.aliyuncs.com/cover.jpg'
        fallback = 'https://book-resource.dataesb.com/cover/example.jpg'
        lookup.return_value = iter([(source + '?Expires=123', '成都图书馆', {}),
                                    (fallback, '成都图书馆封面数据源', {})])
        edition = Edition.objects.create(isbn='9787544258975', cover_url=source)
        self.assertTrue(sync_edition(edition, Mock()))
        lookup.assert_called_once_with(edition.isbn)
        self.assertEqual([call.args[0] for call in download.call_args_list],
                         [source + '?Expires=123', fallback])
        edition.refresh_from_db()
        self.assertEqual(edition.cover_url, source)
        self.assertTrue(edition.cover_object_key.startswith(source_prefix(source)))

    @patch('catalog.covers.download_image', return_value=b'jpeg')
    def test_sync_stores_key_and_keeps_original_source(self, download):
        source = 'https://example.com/cover.jpg'
        e = Edition.objects.create(cover_url=source)
        self.assertTrue(sync_edition(e, Mock()))
        e.refresh_from_db()
        self.assertEqual(e.cover_url, source)
        self.assertTrue(e.cover_display_url.endswith(e.cover_object_key))
        e.cover_url = 'https://example.com/new.jpg'
        self.assertEqual(e.cover_display_url, '')

    def test_legacy_cover_endpoint_redirects_to_public_object(self):
        from django.contrib.auth import get_user_model
        e = Edition.objects.create(cover_url='https://example.com/cover.jpg')
        e.cover_object_key = source_prefix(e.cover_url) + 'original.jpg'
        e.save()
        self.client.force_login(get_user_model().objects.create_user(username='cover-reader'))
        r = self.client.get(f'/books/{e.pk}/cover/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], e.cover_display_url)

    def test_switching_public_domain_does_not_change_stored_path(self):
        e = Edition(cover_url='https://example.com/cover.jpg')
        e.cover_object_key = source_prefix(e.cover_url) + 'cover.jpg'
        original_key = e.cover_object_key
        with override_settings(COS_PUBLIC_BASE_URL='https://images.example.com'):
            self.assertEqual(e.cover_display_url, 'https://images.example.com/' + original_key)
        self.assertEqual(e.cover_object_key, original_key)
