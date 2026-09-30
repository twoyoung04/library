import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '安全备份正在使用的 SQLite 藏书数据库'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='备份文件路径，默认放入 backups 目录')

    def handle(self, *args, **options):
        if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('此命令仅支持本机 SQLite；Supabase 请使用数据库导出或 pg_dump。')
        source = Path(settings.DATABASES['default']['NAME'])
        if not source.exists():
            raise FileNotFoundError('数据库不存在，请先运行 migrate。')
        target = Path(options['output'] or settings.BASE_DIR / 'backups' /
                      f'library-{datetime.now():%Y%m%d-%H%M%S}.sqlite3').expanduser().resolve()
        if target == source.resolve():
            raise ValueError('备份文件不能覆盖正在使用的数据库。')
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as original, sqlite3.connect(target) as backup:
            original.backup(backup)
        self.stdout.write(self.style.SUCCESS(f'已备份到 {target}'))
