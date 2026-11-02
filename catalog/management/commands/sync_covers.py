import time
import threading
from urllib.error import URLError
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from django.core.management.base import BaseCommand, CommandError
from catalog.covers import client, current_key, sync_edition
from catalog.models import Edition


class Command(BaseCommand):
    help = 'Copy external covers to private COS objects; safe to retry.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')

    def handle(self, *args, **options):
        try:
            storage = client()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        while True:
            copied = failed = 0
            host_errors = {}
            host_lock = threading.Lock()
            def transfer(edition):
                host = urlsplit(edition.cover_url).hostname
                with host_lock:
                    deferred = host_errors.get(host, 0) >= 3
                if deferred:
                    return 0, (edition.pk, 'HostTemporarilyUnavailable')
                try:
                    return int(sync_edition(edition, storage)), None
                except Exception as exc:
                    if isinstance(exc, URLError):
                        with host_lock:
                            host_errors[host] = host_errors.get(host, 0) + 1
                    return 0, (edition.pk, type(exc).__name__)
                finally:
                    close_old_connections()
            pending = [e for e in Edition.objects.exclude(cover_url='').iterator(chunk_size=100)
                       if not current_key(e)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                for index, (count, error) in enumerate(pool.map(transfer, pending), 1):
                    copied += count
                    if error:
                        failed += 1
                        self.stderr.write(f'edition={error[0]} failed: {error[1]}')
                    if index % 25 == 0:
                        self.stdout.write(f'processed={index}/{len(pending)} copied={copied} failed={failed}')
            self.stdout.write(f'copied={copied} failed={failed}')
            if not options['watch']:
                if failed:
                    raise CommandError('Some covers failed; rerun to retry.')
                break
            time.sleep(60)
