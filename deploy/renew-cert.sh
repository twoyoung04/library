#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker compose run --rm certbot renew --webroot -w /var/www/acme --quiet
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
