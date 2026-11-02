"""Private COS cover storage and bounded downloads from public image hosts."""
import hashlib
import http.client
import io
import ipaddress
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

from django.conf import settings
from PIL import Image

MAX_BYTES = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


def source_prefix(url):
    return 'covers/' + hashlib.sha256(url.encode()).hexdigest() + '/'


def current_key(edition):
    return bool(edition.cover_object_key and edition.cover_object_key.startswith(source_prefix(edition.cover_url)))


def client():
    from qcloud_cos import CosConfig, CosS3Client
    if not settings.COS_REGION or not settings.COS_SECRET_ID or not settings.COS_SECRET_KEY:
        raise ValueError('Configure COS_REGION, COS_SECRET_ID and COS_SECRET_KEY')
    return CosS3Client(CosConfig(Region=settings.COS_REGION, SecretId=settings.COS_SECRET_ID,
                               SecretKey=settings.COS_SECRET_KEY, Scheme='https', Timeout=15))


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Invalid cover URL')
    if parts.port not in (None, 80, 443):
        raise ValueError('Invalid cover port')


class PublicConnection:
    def connect(self):
        super().connect()
        if not ipaddress.ip_address(self.sock.getpeername()[0]).is_global:
            self.close()
            raise ValueError('Cover host must be public')


class PublicHTTPConnection(PublicConnection, http.client.HTTPConnection):
    pass


class PublicHTTPSConnection(PublicConnection, http.client.HTTPSConnection):
    pass


class PublicHTTPHandler(HTTPHandler):
    def http_open(self, request):
        validate_url(request.full_url)
        return self.do_open(PublicHTTPConnection, request)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        validate_url(request.full_url)
        return self.do_open(PublicHTTPSConnection, request)


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_image(url):
    from urllib.request import ProxyHandler, Request
    validate_url(url)
    opener = build_opener(ProxyHandler({}), PublicHTTPHandler(), PublicHTTPSHandler(), PublicRedirectHandler())
    with opener.open(Request(url, headers={'User-Agent': 'PersonalLibrary/1.0'}), timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Cover exceeds 8 MB')
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('JPEG', 'PNG', 'WEBP', 'GIF'):
            raise ValueError('Unsupported cover format')
        image.load()
        image = image.convert('RGB')
        image.thumbnail((1600, 2400))
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=88)
    return output.getvalue()


def sync_edition(edition, storage):
    from .models import Edition
    if not edition.cover_url or current_key(edition):
        return False
    source = edition.cover_url
    body = download_image(source)
    key = source_prefix(source) + hashlib.sha256(body).hexdigest() + '.jpg'
    storage.put_object(Bucket=settings.COS_BUCKET, Key=key, Body=body,
                       ContentType='image/jpeg', CacheControl='private, max-age=86400', EnableMD5=True)
    # A concurrent edit must not attach an old cover to the new source URL.
    return bool(Edition.objects.filter(pk=edition.pk, cover_url=source).update(cover_object_key=key))
