"""Build browser assets and initialize the production PostgreSQL schema."""

import os
import subprocess
from urllib.parse import urlsplit, urlunsplit


def migration_database_url(database_url):
    """Use Supabase's session pooler for schema changes, if given its transaction URL."""
    parts = urlsplit(database_url)
    if parts.hostname and parts.hostname.endswith('.pooler.supabase.com') and parts.port == 6543:
        parts = parts._replace(netloc=parts.netloc.rsplit(':', 1)[0] + ':5432')
        return urlunsplit(parts)
    return database_url


def main():
    subprocess.run(['npm', 'ci'], check=True)
    subprocess.run(['npm', 'run', 'build'], check=True)

    if os.environ.get('VERCEL_ENV') != 'production':
        print('Skipping database migrations outside Production.')
        return

    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise RuntimeError('Production deployment requires DATABASE_URL.')
    environment = os.environ.copy()
    environment['DATABASE_URL'] = os.environ.get('MIGRATION_DATABASE_URL') or migration_database_url(database_url)
    subprocess.run(['python', 'manage.py', 'migrate', '--noinput'], check=True, env=environment)


if __name__ == '__main__':
    main()
