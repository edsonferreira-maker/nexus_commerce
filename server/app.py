"""Self-hosted Nexo web server and anonymous, first-party traffic collector."""

from __future__ import annotations

import base64
import gzip
import hashlib
import html
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mercadolivre import (
    MercadoLivreError,
    exchange_code,
    fetch_product,
    item_id_from_url,
    is_catalog_product_url,
    selected_item_from_url,
    refresh_token,
)
from page_import import ImportErrorMessage, extract_product, fetch_html, fetch_rendered_html, market_for_url

try:
    import maxminddb
except ImportError:  # Allows local tests without installing the GeoIP reader.
    maxminddb = None


ACTIVE_SECONDS = 5 * 60


def import_public_product(source_url: str, supplied_html: str | None = None) -> dict:
    if supplied_html:
        return extract_product(source_url, supplied_html)
    product = None
    first_error = None
    try:
        product = extract_product(source_url, fetch_html(source_url))
    except ImportErrorMessage as error:
        first_error = error
    if product is None or product['importNotice']:
        try:
            rendered = extract_product(source_url, fetch_rendered_html(source_url))
            fields = ('name', 'price', 'images', 'reviews')
            if product is None or sum(bool(rendered.get(key)) for key in fields) > sum(
                bool(product.get(key)) for key in fields
            ):
                product = rendered
        except ImportErrorMessage as error:
            if product is None:
                raise error from first_error
    if product is None:
        raise first_error or ImportErrorMessage('Não foi possível importar o produto.')
    return product


SESSION_RE = re.compile(r"^[a-f0-9]{32}$")
ADMIN_SESSION_RE = re.compile(r"^[0-9]{10}\.[a-f0-9]{32}\.[a-f0-9]{64}$")
ADMIN_SESSION_SECONDS = 30 * 24 * 60 * 60
ML_OAUTH_STATE_RE = re.compile(r"^[0-9]{10}\.[a-f0-9]{32}\.[a-f0-9]{64}$")
ML_OAUTH_STATE_SECONDS = 30 * 60
# Acesso temporário para concluir a primeira configuração no Render. Remover
# assim que NEXO_ADMIN_PASSWORD estiver salvo como secret no serviço.
TEMPORARY_ADMIN_PASSWORD_SHA256 = '2f2bf584bb6f7c89335ca5b01823de8f0cae2733424531396ca474e3b0c398bd'
# Credenciais temporárias autorizadas para concluir a integração no host atual.
# Remover e revogar no Mercado Livre assim que o ambiente definitivo estiver pronto.
TEMPORARY_ML_CONFIG = {
    'ML_CLIENT_ID': '6912158999502788',
    'ML_CLIENT_SECRET': 'yYJiQiaAo9HgRGFgLWtzr4gQDImXXaDh',
    'ML_REDIRECT_URI': 'https://nexus-commerce-1n5m.onrender.com/api/mercadolivre/callback',
}
PRODUCT_SLUG_RE = re.compile(r'(?:mlb[0-9]{7,15}|p-[a-f0-9]{16})')
PRODUCT_ID_RE = re.compile(r'(?:MLB[0-9]{7,15}|SRC[A-F0-9]{16})')
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".ico": "image/x-icon",
}


class TrafficStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    last_seen INTEGER NOT NULL,
                    page TEXT NOT NULL,
                    country TEXT,
                    state TEXT,
                    city TEXT,
                    lat REAL,
                    lon REAL
                );
                CREATE INDEX IF NOT EXISTS sessions_last_seen ON sessions(last_seen);
                CREATE TABLE IF NOT EXISTS page_views (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    viewed_at INTEGER NOT NULL,
                    page TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS page_views_time ON page_views(viewed_at);
                CREATE TABLE IF NOT EXISTS products (
                    slug TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    published_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_drafts (
                    item_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    imported_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS integrations (
                    name TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                """
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, session_id: str, page: str, kind: str, location: dict, now: int | None = None):
        now = int(time.time()) if now is None else now
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO sessions (id, last_seen, page, country, state, city, lat, lon)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    page=excluded.page,
                    country=COALESCE(excluded.country, sessions.country),
                    state=COALESCE(excluded.state, sessions.state),
                    city=COALESCE(excluded.city, sessions.city),
                    lat=COALESCE(excluded.lat, sessions.lat),
                    lon=COALESCE(excluded.lon, sessions.lon)
                """,
                (
                    session_id,
                    now,
                    page,
                    location.get("country"),
                    location.get("state"),
                    location.get("city"),
                    location.get("lat"),
                    location.get("lon"),
                ),
            )
            if kind == "view":
                db.execute(
                    "INSERT INTO page_views (session_id, viewed_at, page) VALUES (?, ?, ?)",
                    (session_id, now, page),
                )
            # Keep 30 days of aggregates. No IP addresses or user agents are stored.
            if now % 97 == 0:
                db.execute("DELETE FROM sessions WHERE last_seen < ?", (now - 30 * 86400,))
                db.execute("DELETE FROM page_views WHERE viewed_at < ?", (now - 30 * 86400,))

    def live(self, now: int | None = None) -> dict:
        now = int(time.time()) if now is None else now
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT country, state, city, lat, lon FROM sessions
                WHERE last_seen >= ?
                """,
                (now - ACTIVE_SECONDS,),
            ).fetchall()
            first_hour = now // 3600 - 23
            hourly_rows = db.execute(
                "SELECT viewed_at / 3600 AS hour, COUNT(*) AS views FROM page_views WHERE viewed_at >= ? GROUP BY hour",
                (first_hour * 3600,),
            ).fetchall()
        brazil = [row for row in rows if row["country"] == "BR"]
        unknown = sum(1 for row in rows if not row["country"])
        by_city: Counter[tuple] = Counter()
        coordinates = {}
        for row in brazil:
            if row["city"] and row["lat"] is not None and row["lon"] is not None:
                key = (row["city"], row["state"] or "")
                by_city[key] += 1
                coordinates.setdefault(key, (row["lat"], row["lon"]))
        cities = [
            {"city": key[0], "state": key[1], "lat": coordinates[key][0], "lon": coordinates[key][1], "visitors": count}
            for key, count in by_city.most_common(100)
        ]
        counts = {int(row['hour']): row['views'] for row in hourly_rows}
        hourly = [counts.get(hour, 0) for hour in range(first_hour, first_hour + 24)]
        return {
            "activeVisitors": len(brazil),
            "unlocatedVisitors": unknown,
            "cities": cities,
            "hourlyViews": hourly,
            "views24h": sum(hourly),
            "updatedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }

    def save_product(self, product: dict):
        with self.connect() as db:
            db.execute(
                """INSERT INTO products (slug, item_id, data_json, published_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET data_json=excluded.data_json, published_at=excluded.published_at""",
                (product['slug'], product['id'], json.dumps(product, ensure_ascii=False), int(time.time())),
            )

    def save_draft(self, product: dict):
        with self.connect() as db:
            db.execute(
                """INSERT INTO product_drafts (item_id, data_json, imported_at) VALUES (?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET data_json=excluded.data_json, imported_at=excluded.imported_at""",
                (product['id'], json.dumps(product, ensure_ascii=False), int(time.time())),
            )

    def get_draft(self, item_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT data_json FROM product_drafts WHERE item_id = ?', (item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_product(self, slug: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT data_json FROM products WHERE slug = ?', (slug,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_products(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute('SELECT data_json FROM products ORDER BY published_at DESC, slug LIMIT 100').fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_token(self, payload: dict):
        access_token = payload.get('access_token')
        refresh_value = payload.get('refresh_token') or ''
        try:
            expires_in = int(payload.get('expires_in') or 0)
        except (TypeError, ValueError):
            expires_in = 0
        if not isinstance(access_token, str) or not access_token or expires_in <= 0:
            raise MercadoLivreError('O Mercado Livre retornou um token incompleto. Conecte a conta novamente.')
        if not isinstance(refresh_value, str):
            refresh_value = ''
        with self.connect() as db:
            db.execute(
                """INSERT INTO integrations (name, access_token, refresh_token, expires_at) VALUES ('mercadolivre', ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token, expires_at=excluded.expires_at""",
                (access_token, refresh_value, int(time.time()) + expires_in),
            )

    def get_token(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT access_token, refresh_token, expires_at FROM integrations WHERE name = 'mercadolivre'"
            ).fetchone()
        return dict(zip(('access_token', 'refresh_token', 'expires_at'), row)) if row else None

    def create_oauth_state(self) -> str:
        state = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM oauth_states WHERE expires_at < ?', (int(time.time()),))
            db.execute('INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)', (state, int(time.time()) + 600))
        return state

    def consume_oauth_state(self, state: str) -> bool:
        with self.connect() as db:
            row = db.execute('SELECT expires_at FROM oauth_states WHERE state = ?', (state,)).fetchone()
            db.execute('DELETE FROM oauth_states WHERE state = ?', (state,))
        return bool(row and row[0] >= time.time())


class GeoLocator:
    def __init__(self, path: Path):
        self.path = path
        self._reader = None
        self._mtime = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.path.is_file() and maxminddb is not None

    def locate(self, address: str) -> dict:
        try:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or not self.ready:
                return {}
            with self._lock:
                mtime = self.path.stat().st_mtime_ns
                if self._reader is None or self._mtime != mtime:
                    if self._reader is not None:
                        self._reader.close()
                    self._reader = maxminddb.open_database(str(self.path))
                    self._mtime = mtime
                result = self._reader.get(str(ip)) or {}
            country = result.get("country", {}).get("iso_code")
            if country != "BR":
                return {"country": country} if country else {}
            subdivisions = result.get("subdivisions") or []
            state = subdivisions[0].get("iso_code", "") if subdivisions else ""
            names = result.get("city", {}).get("names", {})
            point = result.get("location", {})
            lat, lon = point.get("latitude"), point.get("longitude")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                lat = lon = None
            return {
                "country": "BR",
                "state": state,
                "city": names.get("pt-BR") or names.get("en") or "",
                "lat": lat,
                "lon": lon,
            }
        except (OSError, ValueError, TypeError):
            return {}


def update_geoip(path: Path):
    """Fetch the current free DB-IP city database to the server's own volume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    current = datetime.now(timezone.utc)
    months = [(current.year, current.month)]
    previous = current.replace(day=1).timestamp() - 86400
    previous_date = datetime.fromtimestamp(previous, timezone.utc)
    months.append((previous_date.year, previous_date.month))
    marker = path.with_suffix(".release")
    if path.exists() and marker.exists() and marker.read_text().strip() == f"{current.year}-{current.month:02d}":
        return
    for year, month in months:
        url = f"https://download.db-ip.com/free/dbip-city-lite-{year}-{month:02d}.mmdb.gz"
        temporary = path.with_suffix(".download")
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'NexoCommerce/1.0'})
            with urllib.request.urlopen(request, timeout=30) as source, gzip.GzipFile(fileobj=source) as compressed, temporary.open("wb") as output:
                while chunk := compressed.read(1024 * 1024):
                    output.write(chunk)
            if temporary.stat().st_size < 1024 * 1024:
                raise ValueError("GeoIP database is too small")
            temporary.replace(path)
            marker.write_text(f"{year}-{month:02d}")
            print(f"DB-IP city database updated: {year}-{month:02d}", flush=True)
            return
        except (OSError, ValueError, urllib.error.URLError) as error:
            temporary.unlink(missing_ok=True)
            print(f"GeoIP download failed ({year}-{month:02d}): {error}", flush=True)


def geoip_update_loop(path: Path):
    while True:
        update_geoip(path)
        time.sleep(24 * 3600)


class NexoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, web_root: Path, data_root: Path, password: str, user: str = "admin", trust_proxy: bool = False, geo_locator=None):
        self.web_root = web_root.resolve()
        self.data_root = data_root.resolve()
        self.media_root = self.data_root / 'media'
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.store = TrafficStore(data_root / "traffic.sqlite3")
        self.geo = geo_locator or GeoLocator(data_root / "dbip-city-lite.mmdb")
        self.admin_user = user
        self.admin_password = password
        session_secret = os.environ.get('NEXO_SESSION_SECRET') or (
            f'nexo-session:{self.admin_user}:{TEMPORARY_ADMIN_PASSWORD_SHA256}'
        )
        self.admin_session_secret = hashlib.sha256(session_secret.encode('utf-8')).digest()
        self.revoked_admin_sessions: dict[str, int] = {}
        self.admin_sessions_lock = threading.Lock()
        self.ml_last_error = ''
        self.trust_proxy = trust_proxy
        super().__init__(address, NexoHandler)

    def create_admin_session(self) -> str:
        expiry = str(int(time.time()) + ADMIN_SESSION_SECONDS)
        nonce = secrets.token_hex(16)
        payload = f'{expiry}.{nonce}'
        signature = hmac.new(
            self.admin_session_secret,
            f'{payload}.{self.admin_user}'.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return f'{payload}.{signature}'

    def has_admin_session(self, token: str) -> bool:
        if not ADMIN_SESSION_RE.fullmatch(token):
            return False
        expiry, nonce, signature = token.split('.')
        if int(expiry) <= int(time.time()):
            return False
        payload = f'{expiry}.{nonce}'
        expected = hmac.new(
            self.admin_session_secret,
            f'{payload}.{self.admin_user}'.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        now = int(time.time())
        with self.admin_sessions_lock:
            self.revoked_admin_sessions = {
                value: valid_until
                for value, valid_until in self.revoked_admin_sessions.items()
                if valid_until > now
            }
            return token not in self.revoked_admin_sessions

    def delete_admin_session(self, token: str):
        if not ADMIN_SESSION_RE.fullmatch(token):
            return
        expiry = int(token.split('.', 1)[0])
        with self.admin_sessions_lock:
            self.revoked_admin_sessions[token] = expiry

    def valid_admin_login(self, username: str, password: str) -> bool:
        if not hmac.compare_digest(username, self.admin_user):
            return False
        configured = hmac.compare_digest(password, self.admin_password)
        temporary_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
        temporary = hmac.compare_digest(temporary_hash, TEMPORARY_ADMIN_PASSWORD_SHA256)
        return configured or temporary

    def create_ml_oauth_state(self) -> str:
        expiry = str(int(time.time()) + ML_OAUTH_STATE_SECONDS)
        nonce = secrets.token_hex(16)
        payload = f'{expiry}.{nonce}'
        signature = hmac.new(
            self.admin_session_secret,
            f'mercadolivre:{payload}'.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return f'{payload}.{signature}'

    def valid_ml_oauth_state(self, state: str) -> bool:
        if not ML_OAUTH_STATE_RE.fullmatch(state):
            return False
        expiry, nonce, signature = state.split('.')
        if int(expiry) <= int(time.time()):
            return False
        payload = f'{expiry}.{nonce}'
        expected = hmac.new(
            self.admin_session_secret,
            f'mercadolivre:{payload}'.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    @property
    def ml_configured(self) -> bool:
        return all(os.environ.get(name) for name in ('ML_CLIENT_ID', 'ML_CLIENT_SECRET', 'ML_REDIRECT_URI'))

    def ml_access_token(self) -> str:
        if not self.ml_configured:
            raise MercadoLivreError('Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_REDIRECT_URI no servidor.')
        stored = self.store.get_token()
        if not stored:
            raise MercadoLivreError('Conecte sua conta do Mercado Livre antes de importar.')
        if stored['expires_at'] <= time.time() + 60:
            if not stored['refresh_token']:
                raise MercadoLivreError(
                    'O acesso temporário do Mercado Livre expirou. Ative a permissão offline_access '
                    'no aplicativo e conecte a conta novamente.'
                )
            updated = refresh_token(
                os.environ['ML_CLIENT_ID'], os.environ['ML_CLIENT_SECRET'], stored['refresh_token']
            )
            updated.setdefault('refresh_token', stored['refresh_token'])
            self.store.save_token(updated)
            return updated['access_token']
        return stored['access_token']

    def cache_images(self, product: dict):
        cached = []
        for index, source_url in enumerate(product['images']):
            parsed = urllib.parse.urlsplit(source_url)
            hostname = (parsed.hostname or '').lower()
            if parsed.scheme != 'https' or not (hostname == 'mlstatic.com' or hostname.endswith('.mlstatic.com')):
                cached.append(source_url)
                continue
            try:
                request = urllib.request.Request(source_url, headers={'User-Agent': 'NexoCommerce/1.0'})
                with urllib.request.urlopen(request, timeout=15) as response:
                    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}.get(response.headers.get_content_type())
                    if extension is None:
                        cached.append(source_url)
                        continue
                    data = response.read(10 * 1024 * 1024 + 1)
                if not data or len(data) > 10 * 1024 * 1024:
                    cached.append(source_url)
                    continue
                target = self.media_root / f"{product['id'].lower()}-{index}.{extension}"
                target.write_bytes(data)
                cached.append(f'/media/{target.name}')
            except (OSError, urllib.error.URLError):
                cached.append(source_url)
                continue
        product['images'] = cached


class NexoHandler(BaseHTTPRequestHandler):
    server: NexoServer

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            return self.send_json({"ok": True, "geoipReady": self.server.geo.ready})
        if path == '/api/auth/session':
            if not self.authorized():
                return self.send_json_error('Sessão não autenticada.', HTTPStatus.UNAUTHORIZED)
            return self.send_json({'authenticated': True, 'user': self.server.admin_user})
        if path == '/api/mercadolivre/status':
            if not self.authorized():
                return self.require_auth()
            token = self.server.store.get_token()
            return self.send_json({
                'configured': self.server.ml_configured,
                'connected': token is not None,
                'refreshable': bool(token and token.get('refresh_token')),
                'warning': (
                    'Conta conectada com acesso temporário. Ative a permissão offline_access '
                    'no aplicativo do Mercado Livre para renovar o acesso automaticamente.'
                    if token and not token.get('refresh_token') else ''
                ),
                'error': self.server.ml_last_error,
            })
        if path == '/api/mercadolivre/connect':
            if not self.authorized():
                return self.require_auth()
            if not self.server.ml_configured:
                return self.send_json_error('Configure as credenciais do Mercado Livre no servidor.', HTTPStatus.SERVICE_UNAVAILABLE)
            self.server.ml_last_error = ''
            state = self.server.create_ml_oauth_state()
            query = urllib.parse.urlencode({
                'response_type': 'code',
                'client_id': os.environ['ML_CLIENT_ID'],
                'redirect_uri': os.environ['ML_REDIRECT_URI'],
                'state': state,
            })
            return self.redirect(f'https://auth.mercadolivre.com.br/authorization?{query}')
        if path == '/api/mercadolivre/callback':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            state = (query.get('state') or [''])[0]
            code = (query.get('code') or [''])[0]
            oauth_error = (query.get('error_description') or query.get('error') or [''])[0]
            if not state or not self.server.valid_ml_oauth_state(state):
                self.server.ml_last_error = 'A autorização expirou antes de retornar ao Nexo. Tente conectar novamente.'
                return self.send_oauth_result(False, self.server.ml_last_error)
            if oauth_error:
                self.server.ml_last_error = 'A autorização foi cancelada ou recusada no Mercado Livre.'
                return self.send_oauth_result(False, self.server.ml_last_error)
            if not code:
                self.server.ml_last_error = 'A autorização expirou antes de retornar ao Nexo. Tente conectar novamente.'
                return self.send_oauth_result(False, self.server.ml_last_error)
            try:
                token = exchange_code(
                    os.environ['ML_CLIENT_ID'], os.environ['ML_CLIENT_SECRET'],
                    os.environ['ML_REDIRECT_URI'], code,
                )
                self.server.store.save_token(token)
            except (MercadoLivreError, KeyError) as error:
                self.server.ml_last_error = str(error)
                return self.send_oauth_result(False, self.server.ml_last_error)
            self.server.ml_last_error = ''
            return self.send_oauth_result(True, 'Conta do Mercado Livre conectada com sucesso.')
        if path == "/api/traffic/live":
            if not self.authorized():
                return self.require_auth()
            result = self.server.store.live()
            result["geoipReady"] = self.server.geo.ready
            return self.send_json(result)
        if path == '/api/products':
            if not self.authorized():
                return self.require_auth()
            return self.send_json({'products': self.server.store.list_products()})
        if path.startswith('/api/products/'):
            slug = path[len('/api/products/'):]
            if not PRODUCT_SLUG_RE.fullmatch(slug):
                return self.send_error(HTTPStatus.NOT_FOUND)
            product = self.server.store.get_product(slug)
            if product is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_json(product)
        if path.startswith("/api/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if path.startswith('/media/'):
            name = path[len('/media/'):]
            if not re.fullmatch(r'(?:mlb[0-9]{7,15}-[0-9]+|upload-[a-f0-9]{32})\.(jpg|png|webp)', name):
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.serve_file(self.server.media_root / name)
        if path in ('/', '/index.html', '/login') and not self.authorized():
            return self.serve_file(self.server.web_root / 'login.html')
        if path == '/login':
            return self.redirect('/')
        if path.startswith('/p/'):
            slug = path[3:]
            if self.server.store.get_product(slug) is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            path = '/index.html'
        if path == "/":
            path = "/index.html"
        asset = (self.server.web_root / path.lstrip("/")).resolve()
        if not asset.is_relative_to(self.server.web_root) or not asset.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        return self.serve_file(asset)

    def serve_file(self, asset: Path):
        if not asset.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = asset.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", MIME.get(asset.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        mutable_web_files = {
            'index.html', 'login.html', 'flutter_service_worker.js', 'flutter_bootstrap.js',
            'main.dart.js', 'manifest.json', 'version.json',
            'AssetManifest.bin', 'AssetManifest.bin.json', 'FontManifest.json',
        }
        cache_control = 'no-store' if asset.name in mutable_web_files else 'public, max-age=3600'
        self.send_header('Cache-Control', cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        public_paths = (
            '/login', '/api/traffic/event', '/api/mercadolivre/notify',
            '/api/auth/login', '/api/auth/logout',
        )
        if path not in (*public_paths, '/api/products/import', '/api/products/publish', '/api/media/upload'):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if path not in public_paths and not self.authorized():
            return self.require_auth()
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send_error(HTTPStatus.FORBIDDEN)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if path == '/login':
            content_type = self.headers.get('Content-Type', '').split(';')[0]
            if not 0 < length <= 4096 or content_type != 'application/x-www-form-urlencoded':
                return self.send_error(HTTPStatus.BAD_REQUEST)
            try:
                form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'))
            except UnicodeDecodeError:
                return self.send_error(HTTPStatus.BAD_REQUEST)
            username = (form.get('username') or [''])[0]
            password = (form.get('password') or [''])[0]
            if not self.server.valid_admin_login(username, password):
                return self.redirect('/?login_error=1')
            token = self.server.create_admin_session()
            secure = '; Secure' if self.forwarded_proto() == 'https' else ''
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header('Location', '/')
            self.send_header('Cache-Control', 'no-store')
            self.send_header(
                'Set-Cookie',
                f'nexo_admin={token}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; HttpOnly; SameSite=Strict{secure}',
            )
            self.end_headers()
            return
        if path == '/api/media/upload':
            if not 0 < length <= 10 * 1024 * 1024:
                return self.send_json_error('A foto deve ter até 10 MB.', HTTPStatus.BAD_REQUEST)
            data = self.rfile.read(length)
            if data.startswith(b'\xff\xd8\xff'):
                extension = 'jpg'
            elif data.startswith(b'\x89PNG\r\n\x1a\n'):
                extension = 'png'
            elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
                extension = 'webp'
            else:
                return self.send_json_error('Envie uma foto JPG, PNG ou WebP.', HTTPStatus.BAD_REQUEST)
            name = f'upload-{secrets.token_hex(16)}.{extension}'
            (self.server.media_root / name).write_bytes(data)
            return self.send_json({'url': '/media/' + name})
        max_length = 9 * 1024 * 1024 if path == '/api/products/import' else 65536 if path == '/api/products/publish' else 4096
        if length < 1 or length > max_length or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_error(HTTPStatus.BAD_REQUEST)
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if path == '/api/auth/login':
            username = body.get('username')
            password = body.get('password')
            if not (
                isinstance(username, str)
                and isinstance(password, str)
                and self.server.valid_admin_login(username, password)
            ):
                return self.send_json_error('Usuário ou senha incorretos.', HTTPStatus.UNAUTHORIZED)
            token = self.server.create_admin_session()
            secure = '; Secure' if self.forwarded_proto() == 'https' else ''
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header(
                'Set-Cookie',
                f'nexo_admin={token}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; HttpOnly; SameSite=Strict{secure}',
            )
            data = json.dumps({'authenticated': True, 'user': self.server.admin_user}).encode('utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/api/auth/logout':
            token = self.admin_session_token()
            if token:
                self.server.delete_admin_session(token)
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Set-Cookie', 'nexo_admin=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict')
            data = b'{"ok": true}'
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/api/mercadolivre/notify':
            expected_app = os.environ.get('ML_CLIENT_ID', '')
            received_app = str(body.get('application_id') or '')
            if expected_app and not hmac.compare_digest(received_app, expected_app):
                return self.send_error(HTTPStatus.FORBIDDEN)
            return self.send_json({'ok': True})
        if path == '/api/products/import':
            try:
                source_url = str(body.get('url') or '')
                if len(source_url) > 2048:
                    raise ImportErrorMessage('O link do produto é longo demais.')
                market, _ = market_for_url(source_url)
                supplied_html = body.get('html')
                if supplied_html is not None and not isinstance(supplied_html, str):
                    raise ImportErrorMessage('O HTML informado é inválido.')
                if supplied_html:
                    product = import_public_product(source_url, supplied_html)
                elif market == 'mercadolivre':
                    item_id = item_id_from_url(source_url)
                    try:
                        product = fetch_product(
                            item_id,
                            self.server.ml_access_token(),
                            catalog=is_catalog_product_url(source_url),
                            selected_item=selected_item_from_url(source_url),
                        )
                        self.server.cache_images(product)
                    except MercadoLivreError as api_error:
                        try:
                            product = import_public_product(source_url)
                        except ImportErrorMessage as browser_error:
                            raise MercadoLivreError(
                                f'{api_error} A leitura pública também falhou: {browser_error}'
                            ) from browser_error
                else:
                    product = import_public_product(source_url)
                self.server.store.save_draft(product)
                return self.send_json(product)
            except (MercadoLivreError, ImportErrorMessage, ValueError) as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/products/publish':
            item_id = body.get('id')
            if not isinstance(item_id, str) or not PRODUCT_ID_RE.fullmatch(item_id):
                return self.send_json_error('Produto inválido.', HTTPStatus.BAD_REQUEST)
            if body.get('redirect') is not True:
                return self.send_json_error('Checkout próprio ainda não está disponível. Use redirecionamento.', HTTPStatus.BAD_REQUEST)
            product = self.server.store.get_draft(item_id)
            if product is None:
                return self.send_json_error('Importe o produto antes de publicar.', HTTPStatus.NOT_FOUND)
            product.setdefault('storeName', 'Minha loja')
            edits = body.get('page') or {}
            if not isinstance(edits, dict):
                return self.send_json_error('Dados da página inválidos.', HTTPStatus.BAD_REQUEST)
            for field, limit in (('storeName', 80), ('name', 200), ('price', 80), ('description', 10000), ('brand', 100), ('shippingText', 200), ('installmentText', 200)):
                if field in edits:
                    value = edits[field]
                    if not isinstance(value, str) or len(value) > limit or (field in ('storeName', 'name', 'price') and not value.strip()):
                        return self.send_json_error(f'Campo {field} inválido.', HTTPStatus.BAD_REQUEST)
                    product[field] = value.strip()
            if 'stock' in edits:
                stock = edits['stock']
                if not isinstance(stock, int) or isinstance(stock, bool) or not -1 <= stock <= 1000000000:
                    return self.send_json_error('Estoque inválido.', HTTPStatus.BAD_REQUEST)
                product['stock'] = stock
            if 'variations' in edits:
                variations = edits['variations']
                if not isinstance(variations, list) or len(variations) > 12 or any(not isinstance(value, str) or not value.strip() or len(value) > 120 for value in variations):
                    return self.send_json_error('Variações inválidas.', HTTPStatus.BAD_REQUEST)
                product['variations'] = [value.strip() for value in variations]
            if 'images' in edits:
                images = edits['images']
                if not isinstance(images, list) or not 1 <= len(images) <= 10:
                    return self.send_json_error('Informe entre 1 e 10 fotos.', HTTPStatus.BAD_REQUEST)
                for image in images:
                    if not isinstance(image, str) or len(image) > 2048:
                        return self.send_json_error('URL de foto inválida.', HTTPStatus.BAD_REQUEST)
                    try:
                        parsed = urllib.parse.urlsplit(image)
                        remote = parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and not parsed.username
                    except ValueError:
                        remote = False
                    if not (remote or re.fullmatch(r'/media/(?:mlb[0-9]{7,15}-[0-9]+|upload-[a-f0-9]{32})\.(jpg|png|webp)', image)):
                        return self.send_json_error('Use URLs HTTPS para as fotos.', HTTPStatus.BAD_REQUEST)
                product['images'] = images
            if 'reviews' in edits:
                reviews = edits['reviews']
                if not isinstance(reviews, list) or any(review not in product['reviews'] for review in reviews):
                    return self.send_json_error('Os comentários podem ser removidos, mas não alterados.', HTTPStatus.BAD_REQUEST)
                product['reviews'] = reviews
            if not product.get('storeName') or not product.get('name') or not product.get('price') or not product.get('images'):
                return self.send_json_error('Complete título, preço e fotos antes de publicar.', HTTPStatus.BAD_REQUEST)
            product['redirect'] = True
            self.server.store.save_product(product)
            return self.send_json(product)
        page = body.get('page')
        if not isinstance(page, str) or not page.startswith('/p/') or not PRODUCT_SLUG_RE.fullmatch(page[3:]) or self.server.store.get_product(page[3:]) is None or body.get('kind') not in ('view', 'heartbeat'):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        supplied = cookie.get("nexo_visitor")
        session_id = supplied.value if supplied and SESSION_RE.fullmatch(supplied.value) else secrets.token_hex(16)
        location = self.server.geo.locate(self.client_ip())
        self.server.store.record(session_id, page, body["kind"], location)
        self.send_response(HTTPStatus.NO_CONTENT)
        if supplied is None or supplied.value != session_id:
            secure = "; Secure" if self.forwarded_proto() == "https" else ""
            self.send_header("Set-Cookie", f"nexo_visitor={session_id}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def client_ip(self) -> str:
        if self.server.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",", 1)[0].strip()
        return self.client_address[0]

    def forwarded_proto(self) -> str:
        return self.headers.get("X-Forwarded-Proto", "").lower() if self.server.trust_proxy else ""

    def authorized(self) -> bool:
        token = self.admin_session_token()
        if token and self.server.has_admin_session(token):
            return True
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        username, separator, password = decoded.partition(':')
        return bool(separator) and self.server.valid_admin_login(username, password)

    def admin_session_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return ''
        supplied = cookie.get('nexo_admin')
        return supplied.value if supplied else ''

    def require_auth(self):
        self.send_json_error('Faça login para continuar.', HTTPStatus.UNAUTHORIZED)

    def redirect(self, location: str):
        self.send_response(HTTPStatus.FOUND)
        self.send_header('Location', location)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def send_oauth_result(self, success: bool, message: str):
        title = 'Conta conectada' if success else 'Não foi possível conectar'
        color = '#72e59b' if success else '#ff7885'
        refresh = '<meta http-equiv="refresh" content="2;url=/">' if success else ''
        page = f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
        <title>{title} — Nexo Commerce</title></head>
        <body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#08111f;color:white;font:16px system-ui">
        <main style="width:min(520px,calc(100% - 40px));padding:36px;border:1px solid #25334a;border-radius:22px;background:#101b2d;text-align:center">
        <div style="font-size:44px;color:{color}">{'✓' if success else '!'}</div>
        <h1 style="font-size:26px">{title}</h1><p style="color:#aab5c5">{html.escape(message)}</p>
        <a href="/" style="display:inline-block;margin-top:16px;padding:13px 20px;border-radius:12px;background:#72e59b;color:#08111f;text-decoration:none;font-weight:800">Voltar ao Nexo</a>
        </main></body></html>'''.encode('utf-8')
        self.send_response(HTTPStatus.OK if success else HTTPStatus.BAD_REQUEST)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(page)

    def send_json_error(self, message: str, status: HTTPStatus):
        data = json.dumps({'message': message}, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def admin_password(data_root: Path) -> str:
    configured = os.environ.get("NEXO_ADMIN_PASSWORD")
    if configured:
        return configured
    path = data_root / "admin_password"
    if path.exists():
        return path.read_text().strip()
    password = secrets.token_urlsafe(20)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password)
    path.chmod(0o600)
    print(f"Nexo admin user: {os.environ.get('NEXO_ADMIN_USER', 'admin')}\nNexo admin password: {password}", flush=True)
    return password


def main():
    for name, temporary_value in TEMPORARY_ML_CONFIG.items():
        if not os.environ.get(name):
            os.environ[name] = temporary_value
    data_root = Path(os.environ.get("NEXO_DATA_DIR", "/data"))
    data_root.mkdir(parents=True, exist_ok=True)
    web_root = Path(os.environ.get("NEXO_WEB_DIR", "/app/web"))
    server = NexoServer(
        (os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080"))),
        web_root,
        data_root,
        admin_password(data_root),
        user=os.environ.get("NEXO_ADMIN_USER", "admin"),
        trust_proxy=os.environ.get("TRUST_PROXY") == "1",
    )
    if os.environ.get("GEOIP_AUTO_DOWNLOAD", "1") == "1":
        threading.Thread(target=geoip_update_loop, args=(server.geo.path,), daemon=True).start()
    print(f"Nexo server listening on {server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
