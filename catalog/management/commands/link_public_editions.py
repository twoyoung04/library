from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Subquery

from catalog.models import Edition, PublicEdition


class Command(BaseCommand):
    help = '把已有藏书关联到同 ISBN 的公共书目。只更新外键，不改动书目信息或查询外部资料。'

    def handle(self, *args, **options):
        public = PublicEdition.objects.filter(isbn=OuterRef('isbn'))
        linked = Edition.objects.filter(public_edition__isnull=True).filter(Exists(public)).update(
            public_edition_id=Subquery(public.values('pk')[:1]),
        )
        self.stdout.write(self.style.SUCCESS(f'已关联 {linked} 条藏书记录。'))
