#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/library
exec 9>.release.lock
flock -w 600 9
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -xf - -C "$stage" source.tar.gz revision
revision=$(<"$stage/revision")
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
tar -xzf "$stage/source.tar.gz" -C "$stage"
sudo docker cp "$(sudo docker compose ps -q web):/app/catalog/static/catalog/vendor/lang/eng.traineddata.gz" \
    "$stage/frontend/.eng-4.0.0-fast.traineddata.gz"
sudo docker build --pull=false --target app -t library-web:ci "$stage"
sudo docker build --pull=false --target proxy -t library-nginx:ci "$stage"

compose=(sudo docker compose --profile covers)
files=(compose.yaml deploy/nginx.{http,https,ip}.conf.template deploy/{backup,renew-cert}.sh)
for file in "${files[@]}"; do
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
for file in "${files[@]}"; do
    cp "$stage/$file" "$file"
done
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
    "${compose[@]}" exec -T nginx nginx -t || return 1
    local path status
    for path in /login/ /static/catalog/scan.bundle.js; do
        status=$(curl -fsS --retry 6 --retry-all-errors --max-time 10 \
            --resolve "$domain:443:127.0.0.1" "https://$domain$path" -o /dev/null -w '%{http_code}') || return 1
        [[ "$status" == 200 ]] || return 1
    done
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
