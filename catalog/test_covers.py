import io
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from PIL import Image
from .covers import download_image, source_prefix, sync_edition, validate_url
from .models import Edition


@override_settings(COS_ENABLED=True, COS_BUCKET='cover-1307828558')
class CoverTests(TestCase):
    def test_external_cover_never_appears_in_page_image_url(self):
        e = Edition.objects.create(title='Test', cover_url='https://external.example/cover.jpg')
        self.assertEqual(e.cover_display_url, '')
        e.cover_object_key = source_prefix(e.cover_url) + 'stored.jpg'
        self.assertIn(f'/books/{e.pk}/cover/', e.cover_display_url)
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
