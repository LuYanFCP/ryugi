FROM python:3.13-alpine

WORKDIR /app
RUN pip install --no-cache-dir waitress==3.0.2 \
    && addgroup -S share && adduser -S -G share share \
    && mkdir /data && chown share:share /data
COPY LICENSE ./
COPY self-host/server.py self-host/snapshot.py ./
ENV PYTHONDONTWRITEBYTECODE=1 RYUGI_SELF_HOST_DB=/data/shares.sqlite3
USER share
EXPOSE 8787
CMD ["waitress-serve", "--listen=0.0.0.0:8787", "--max-request-body-size=67108864", "--call", "server:create_app"]
