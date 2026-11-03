#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/library
exec 9>.release.lock
flock -w 600 9
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -xf - -C "$stage" config.tar images.tar.gz revision
revision=$(<"$stage/revision")
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
tar -xf "$stage/config.tar" -C "$stage"
sudo docker load -i "$stage/images.tar.gz"

compose=(sudo docker compose --profile covers)
for file in compose.yaml deploy/nginx.{http,https,ip}.conf.template deploy/{backup,renew-cert}.sh; do
    mkdir -p "$stage/previous/$(dirname "$file")"
    cp "$file" "$stage/previous/$file"
done
sudo docker tag library-web:latest library-web:previous
sudo docker tag library-nginx:latest library-nginx:previous
sudo ./deploy/backup.sh
sudo docker tag library-web:ci library-web:latest
sudo docker tag library-nginx:ci library-nginx:latest

restore() {
    cp -R "$stage/previous/." .
    sudo docker tag library-web:previous library-web:latest
    sudo docker tag library-nginx:previous library-nginx:latest
}
cp "$stage/compose.yaml" .
cp "$stage"/deploy/* deploy/
if ! "${compose[@]}" config --quiet ||
   ! "${compose[@]}" run --rm -T --no-deps web python manage.py check; then
    restore
    exit 1
fi
migration_needed=0
if ! "${compose[@]}" run --rm -T --no-deps web python manage.py migrate --check; then
    migration_needed=1
    "${compose[@]}" run --rm -T --no-deps web python manage.py migrate --noinput || {
        restore
        exit 1
    }
fi

smoke() {
    local domain
    domain=$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["nginx"]["environment"]["DOMAIN"])')
    "${compose[@]}" exec -T nginx nginx -t &&
      curl -fsS --retry 6 --retry-all-errors --max-time 10 --resolve "$domain:443:127.0.0.1" "https://$domain/login/" -o /dev/null &&
      curl -fsS --max-time 10 --resolve "$domain:443:127.0.0.1" "https://$domain/static/catalog/scan.bundle.js" -o /dev/null
}
if ! "${compose[@]}" up -d --no-build --force-recreate web nginx covers || ! smoke; then
    if (( migration_needed == 0 )); then
        restore
        "${compose[@]}" up -d --no-build --force-recreate web nginx covers
    else
        printf '%s\n' 'Schema migration ran. Inspect the release and backup before rolling back.'
    fi
    exit 1
fi
printf '%s\n' "$revision" > .deployed-revision
printf 'Deployed %s\n' "$revision"
