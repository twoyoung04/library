FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package*.json ./
RUN npm ci
COPY frontend ./frontend
RUN if [ -s frontend/.eng-4.0.0-fast.traineddata.gz ]; then \
      mkdir -p catalog/static/catalog/vendor/lang && \
      cp frontend/.eng-4.0.0-fast.traineddata.gz catalog/static/catalog/vendor/lang/eng.traineddata.gz; \
    fi && npm run build

FROM python:3.12-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home library
COPY manage.py ./
COPY library ./library
COPY catalog ./catalog
COPY templates ./templates
COPY --from=frontend /build/catalog/static/catalog/ ./catalog/static/catalog/
RUN DATABASE_URL=postgresql://build:build@localhost/build DJANGO_SECRET_KEY=build-only DJANGO_ENV=production python manage.py collectstatic --noinput
USER library
EXPOSE 8000
CMD ["gunicorn", "library.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]

FROM nginx:stable-alpine AS proxy
COPY --from=app /app/staticfiles/ /srv/static/
