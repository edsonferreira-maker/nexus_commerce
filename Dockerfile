FROM ghcr.io/cirruslabs/flutter:3.35.4 AS web-build
WORKDIR /src
COPY . .
RUN flutter pub get && flutter build web --release --dart-define=NEXO_SELF_HOSTED=true

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NEXO_DATA_DIR=/data NEXO_WEB_DIR=/app/web PORT=8080
WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && useradd --system --uid 10001 --home /app nexo \
    && mkdir -p /data /app/web \
    && chown -R nexo:nexo /data /app
COPY server/ /app/server/
COPY --from=web-build /src/build/web/ /app/web/
USER nexo
EXPOSE 8080
WORKDIR /app/server
CMD ["python", "-u", "app.py"]
