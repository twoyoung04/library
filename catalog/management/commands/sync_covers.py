import time
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
            for edition in Edition.objects.exclude(cover_url='').iterator(chunk_size=100):
                if current_key(edition):
                    continue
                try:
                    copied += int(sync_edition(edition, storage))
                except Exception as exc:
                    failed += 1
                    # No URLs, credentials or signed request details in logs.
                    self.stderr.write(f'edition={edition.pk} failed: {type(exc).__name__}')
            self.stdout.write(f'copied={copied} failed={failed}')
            if not options['watch']:
                if failed:
                    raise CommandError('Some covers failed; rerun to retry.')
                break
            time.sleep(60)
