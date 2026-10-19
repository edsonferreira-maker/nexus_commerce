"""Self-hosted Nexo web server and anonymous, first-party traffic collector."""

from __future__ import annotations

import base64
import gzip
import fcntl
import tempfile
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
    approved_media,
    exchange_code,
    fetch_product,
    item_id_from_url,
    is_catalog_product_url,
    selected_item_from_url,
    refresh_token,
)
from product_pages import render_product, render_admin, local_media, render_integrations, allowed_product_media
from integrations import IntegrationStore, IntegrationError, TenantRepository, public_product
from universal_import import SourceError, GenericAdapter, MercadoLivreAdapter, validate_url, decorate
from cryptography.fernet import Fernet

from page_import import (
    ImportErrorMessage,
    extract_product,
    fetch_html,
    fetch_rendered_html,
    mercado_livre_link_fallback,
    market_for_url,
)
from payments import (
    PAYMENT_STATUSES,
    PIXZY_PROVIDER,
    PaymentGatewayError,
    PixzyClient,
    PixzyConfig,
    parse_brl_cents,
)

try:
    import maxminddb
except ImportError:  # Allows local tests without installing the GeoIP reader.
    maxminddb = None


ACTIVE_SECONDS = 5 * 60


def payment_log(log_event: str, **fields):
    """Emit compact structured logs without credentials or payer PII."""
    print(json.dumps({'event': log_event, **fields}, ensure_ascii=False, separators=(',', ':')), flush=True)


def import_public_product(source_url: str, supplied_html: str | None = None, config=None) -> dict:
    return GenericAdapter().extract(source_url, config=config, html=supplied_html)


SESSION_RE = re.compile(r"^[a-f0-9]{32}$")
ADMIN_SESSION_RE = re.compile(r"^[0-9]{10}\.[a-f0-9]{32}\.[a-f0-9]{64}$")
ADMIN_SESSION_SECONDS = 30 * 24 * 60 * 60
ML_OAUTH_STATE_RE = re.compile(r"^[0-9]{10}\.[a-f0-9]{32}\.[a-f0-9]{64}$")
ML_OAUTH_STATE_SECONDS = 30 * 60
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
                CREATE TABLE IF NOT EXISTS product_import_history (
                    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, source_url TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL, status_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payment_settings (
                    provider TEXT PRIMARY KEY,
                    api_token TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    public_id TEXT NOT NULL UNIQUE,
                    client_key TEXT NOT NULL UNIQUE,
                    product_slug TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    status TEXT NOT NULL,
                    customer_json TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    public_id TEXT NOT NULL UNIQUE,
                    order_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_transaction_id TEXT NOT NULL UNIQUE,
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    status TEXT NOT NULL,
                    pix_code TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    paid_at INTEGER,
                    expired_at INTEGER,
                    failed_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(id)
                );
                CREATE INDEX IF NOT EXISTS payments_order ON payments(order_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS payment_status_history (
                    id INTEGER PRIMARY KEY,
                    payment_id TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(payment_id) REFERENCES payments(id)
                );
                CREATE TABLE IF NOT EXISTS payment_webhook_events (
                    event_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    processed_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
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
            db.execute('INSERT INTO product_import_history (item_id, source_url, fetched_at, status_json) VALUES (?, ?, ?, ?)', (product['id'], product.get('inputUrl') or product.get('sourceUrl', ''), int(time.time()), json.dumps(product.get('sourceStatus', {}))))
            db.execute('DELETE FROM product_import_history WHERE fetched_at < ?', (int(time.time()) - 30 * 86400,))
            db.execute(
                """INSERT INTO product_drafts (item_id, data_json, imported_at) VALUES (?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET data_json=excluded.data_json, imported_at=excluded.imported_at""",
                (product['id'], json.dumps(product, ensure_ascii=False), int(time.time())),
            )

    def cached_import(self, source_url: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT data_json FROM product_drafts WHERE json_extract(data_json, '$.inputUrl') = ? LIMIT 1", (source_url,)).fetchone()
        return json.loads(row[0]) if row else None

    def own_recommendations(self, slugs: list[str]) -> list[dict]:
        products = [self.get_product(slug) for slug in slugs]
        return [p for p in products if p and p.get('catalogOwner') == 'own_catalog']

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

    def token_cipher(self):
        external_key = os.environ.get('NEXO_CREDENTIAL_KEY')
        if external_key:
            return Fernet(external_key.encode())
        key_path = self.path.parent / 'oauth.key'
        if not key_path.exists():
            fd, temporary = tempfile.mkstemp(prefix='.oauth-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'wb') as key_file:
                    key_file.write(Fernet.generate_key())
                try:
                    os.link(temporary, key_path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        return Fernet(key_path.read_bytes())

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
                ('enc:' + self.token_cipher().encrypt(access_token.encode()).decode(), 'enc:' + self.token_cipher().encrypt(refresh_value.encode()).decode(), int(time.time()) + expires_in),
            )

    def get_token(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT access_token, refresh_token, expires_at FROM integrations WHERE name = 'mercadolivre'"
            ).fetchone()
        if not row:
            return None
        result = dict(zip(('access_token', 'refresh_token', 'expires_at'), row))
        if not result['access_token'].startswith('enc:'):
            with self.connect() as db:
                db.execute('UPDATE integrations SET access_token = ?, refresh_token = ? WHERE name = ? AND access_token = ?', ('enc:' + self.token_cipher().encrypt(result['access_token'].encode()).decode(), 'enc:' + self.token_cipher().encrypt(result['refresh_token'].encode()).decode(), 'mercadolivre', result['access_token']))
            return self.get_token()
        for key in ('access_token', 'refresh_token'):
            result[key] = self.token_cipher().decrypt(result[key][4:].encode()).decode()
        return result

    def create_oauth_state(self) -> str:
        state = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM oauth_states WHERE expires_at < ?', (int(time.time()),))
            db.execute('INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)', (state, int(time.time()) + 600))
        return state

    def consume_oauth_state(self, state: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute('SELECT expires_at FROM oauth_states WHERE state = ?', (state,)).fetchone()
            db.execute('DELETE FROM oauth_states WHERE state = ?', (state,))
        return bool(row and row[0] >= time.time())

    def get_payment_config(self) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                'SELECT api_token, base_url, webhook_url, updated_at FROM payment_settings WHERE provider = ?',
                (PIXZY_PROVIDER,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        if not result['api_token'].startswith('enc:'):
            self.save_payment_config(result['api_token'], result['base_url'], result['webhook_url'])
        else:
            result['api_token'] = self.token_cipher().decrypt(result['api_token'][4:].encode()).decode()
        return result

    def save_payment_config(self, api_token: str, base_url: str, webhook_url: str):
        with self.connect() as db:
            db.execute(
                """INSERT INTO payment_settings (provider, api_token, base_url, webhook_url, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET api_token=excluded.api_token,
                    base_url=excluded.base_url, webhook_url=excluded.webhook_url,
                    updated_at=excluded.updated_at""",
                (PIXZY_PROVIDER, 'enc:' + self.token_cipher().encrypt(api_token.encode()).decode(), base_url, webhook_url, int(time.time())),
            )

    def create_or_get_order(self, product: dict, customer: dict, client_key: str, quantity: int) -> dict:
        unit_amount = parse_brl_cents(product.get('price', ''))
        amount = unit_amount * quantity
        if amount < 500:
            raise ValueError('O valor mínimo para pagamento via Pix é R$ 5,00.')
        now = int(time.time())
        order_id = secrets.token_hex(16)
        public_id = secrets.token_hex(16)
        items = [{'name': product['name'][:255], 'price': unit_amount, 'quantity': quantity}]
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO orders
                (id, public_id, client_key, product_slug, amount, status, customer_json, items_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    order_id, public_id, client_key, product['slug'], amount,
                    json.dumps(customer, ensure_ascii=False), json.dumps(items, ensure_ascii=False), now, now,
                ),
            )
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM orders WHERE client_key = ?', (client_key,)).fetchone()
        return dict(row)

    def payment_for_order(self, order_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM payments WHERE order_id = ? AND status IN ('pending', 'paid') ORDER BY created_at DESC LIMIT 1",
                (order_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_payment(self, order: dict, transaction: dict) -> dict:
        transaction_id = str(transaction.get('transaction_id') or transaction.get('id') or '')
        status = str(transaction.get('status') or '')
        pix_code = transaction.get('br_code')
        amount = transaction.get('amount')
        if not transaction_id or status not in PAYMENT_STATUSES or not isinstance(pix_code, str) or not pix_code:
            raise PaymentGatewayError('Resposta inválida do gateway.', details='missing transaction fields')
        if not isinstance(amount, int) or isinstance(amount, bool) or amount != order['amount']:
            raise PaymentGatewayError('Valor divergente retornado pelo gateway.', details='amount mismatch')
        now = int(time.time())
        payment_id = secrets.token_hex(16)
        public_id = secrets.token_hex(16)
        metadata = {'order_id': order['public_id']}
        with self.connect() as db:
            db.execute(
                """INSERT INTO payments
                (id, public_id, order_id, provider, provider_transaction_id, amount, status, pix_code,
                 metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payment_id, public_id, order['id'], PIXZY_PROVIDER, transaction_id, amount, status,
                    pix_code, json.dumps(metadata), now, now,
                ),
            )
            db.execute(
                """INSERT INTO payment_status_history
                (payment_id, old_status, new_status, source, payload_json, created_at)
                VALUES (?, NULL, ?, 'create', '{}', ?)""",
                (payment_id, status, now),
            )
        return self.get_payment(public_id)

    def get_payment(self, public_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT payments.*, orders.public_id AS order_public_id
                FROM payments JOIN orders ON orders.id = payments.order_id
                WHERE payments.public_id = ?""",
                (public_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_payment_by_transaction(self, transaction_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT payments.*, orders.public_id AS order_public_id
                FROM payments JOIN orders ON orders.id = payments.order_id
                WHERE payments.provider_transaction_id = ?""",
                (transaction_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_orders(self) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT orders.public_id, orders.product_slug, orders.amount, orders.status,
                orders.created_at, payments.public_id AS payment_id
                FROM orders LEFT JOIN payments ON payments.id = (
                    SELECT latest.id FROM payments AS latest
                    WHERE latest.order_id = orders.id ORDER BY latest.created_at DESC LIMIT 1
                )
                ORDER BY orders.created_at DESC LIMIT 100"""
            ).fetchall()
        result = []
        for row in rows:
            order = dict(row)
            product = self.get_product(order['product_slug']) or {}
            result.append({
                'id': order['public_id'],
                'product': product.get('name', order['product_slug']),
                'amount': order['amount'],
                'status': order['status'],
                'paymentId': order['payment_id'] or '',
                'createdAt': order['created_at'],
            })
        return result

    def webhook_processed(self, event_key: str) -> bool:
        with self.connect() as db:
            return db.execute(
                'SELECT 1 FROM payment_webhook_events WHERE event_key = ?', (event_key,)
            ).fetchone() is not None

    def apply_payment_status(
        self,
        payment: dict,
        status: str,
        source: str,
        payload: dict,
        event_key: str | None = None,
        event: str = '',
    ) -> bool:
        if status not in PAYMENT_STATUSES:
            raise ValueError('Status de pagamento inválido.')
        now = int(time.time())
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        with self.connect() as db:
            if event_key:
                inserted = db.execute(
                    """INSERT OR IGNORE INTO payment_webhook_events
                    (event_key, provider, transaction_id, event, status, payload_json, processed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_key, PIXZY_PROVIDER, payment['provider_transaction_id'], event, status,
                        payload_json, now, now,
                    ),
                ).rowcount
                if not inserted:
                    return False
            current = db.execute('SELECT status FROM payments WHERE id = ?', (payment['id'],)).fetchone()
            if current is None:
                raise ValueError('Pagamento não encontrado.')
            old_status = current[0]
            if old_status == 'paid' and status != 'paid':
                return True
            if old_status in ('expired', 'failed', 'cancelled') and status == 'pending':
                return True
            if old_status != status:
                timestamp_column = {'paid': 'paid_at', 'expired': 'expired_at', 'failed': 'failed_at'}.get(status)
                if timestamp_column:
                    db.execute(
                        f'UPDATE payments SET status = ?, updated_at = ?, {timestamp_column} = ? WHERE id = ?',
                        (status, now, now, payment['id']),
                    )
                else:
                    db.execute('UPDATE payments SET status = ?, updated_at = ? WHERE id = ?', (status, now, payment['id']))
                db.execute(
                    """INSERT INTO payment_status_history
                    (payment_id, old_status, new_status, source, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (payment['id'], old_status, status, source, payload_json, now),
                )
                order_status = 'paid' if status == 'paid' else status if status in ('expired', 'failed') else 'pending'
                db.execute('UPDATE orders SET status = ?, updated_at = ? WHERE id = ?', (order_status, now, payment['order_id']))
        return True


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

    def __init__(self, address, web_root: Path, data_root: Path, password: str, user: str = "admin", trust_proxy: bool = False, geo_locator=None, payment_gateway=None):
        self.web_root = web_root.resolve()
        self.data_root = data_root.resolve()
        self.media_root = self.data_root / 'media'
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.store = TrafficStore(data_root / "traffic.sqlite3")
        self.connections = IntegrationStore(self.store)
        self.rate_lock = threading.Lock()
        self.import_rates = {}
        self.login_rates = {}
        self.geo = geo_locator or GeoLocator(data_root / "dbip-city-lite.mmdb")
        self.admin_user = user
        self.admin_password = password
        session_secret = os.environ.get('NEXO_SESSION_SECRET') or (
            f'nexo-session:{self.admin_user}:{self.admin_password}'
        )
        self.admin_session_secret = hashlib.sha256(session_secret.encode('utf-8')).digest()
        self.revoked_admin_sessions: dict[str, int] = {}
        self.admin_sessions_lock = threading.Lock()
        self.ml_last_error = ''
        self.ml_lock = threading.RLock()
        self.import_times = []
        self.import_ttl = int(os.environ.get('ML_CACHE_TTL', '900'))
        stored_payment_config = self.store.get_payment_config() or {}
        self.pixzy_managed_by_environment = bool(os.environ.get('PIXZY_API_TOKEN'))
        self.pixzy_config = PixzyConfig(
            api_token=os.environ.get('PIXZY_API_TOKEN') or stored_payment_config.get('api_token', ''),
            base_url=(
                os.environ.get('PIXZY_BASE_URL')
                or stored_payment_config.get('base_url')
                or 'https://app.pixzypay.com/api'
            ),
            webhook_url=os.environ.get('PIXZY_WEBHOOK_URL') or stored_payment_config.get('webhook_url', ''),
        )
        self.payment_gateway = payment_gateway or PixzyClient(self.pixzy_config)
        self.payment_lock = threading.Lock()
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
        if not hmac.compare_digest(username.encode('utf-8'), self.admin_user.encode('utf-8')):
            return False
        configured = hmac.compare_digest(password.encode('utf-8'), self.admin_password.encode('utf-8'))
        return configured

    def create_ml_oauth_state(self) -> str:
        expiry = str(int(time.time()) + ML_OAUTH_STATE_SECONDS)
        nonce = secrets.token_hex(16)
        payload = f'{expiry}.{nonce}'
        signature = hmac.new(
            self.admin_session_secret,
            f'mercadolivre:{payload}'.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        state = f'{payload}.{signature}'
        with self.store.connect() as db:
            db.execute('INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)', (state, int(expiry)))
        return state

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
        return hmac.compare_digest(signature, expected) and self.store.consume_oauth_state(state)

    @property
    def ml_configured(self) -> bool:
        return all(os.environ.get(name) for name in ('ML_CLIENT_ID', 'ML_CLIENT_SECRET', 'ML_REDIRECT_URI'))

    @contextmanager
    def ml_oauth_lock(self):
        with self.ml_lock:
            lock_path = self.data_root / 'oauth.lock'
            with lock_path.open('a') as lock_file:
                lock_path.chmod(0o600)
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    def ml_access_token(self) -> str:
        with self.ml_oauth_lock():
            return self._ml_access_token()

    def ml_retry_token(self, failed_token):
        with self.ml_oauth_lock():
            stored = self.store.get_token()
            if not stored or not stored.get('refresh_token'):
                raise MercadoLivreError('Reconecte a conta do Mercado Livre para renovar o acesso.')
            if stored['access_token'] == failed_token:
                self.store.save_token(refresh_token(os.environ['ML_CLIENT_ID'], os.environ['ML_CLIENT_SECRET'], stored['refresh_token']))
            return self._ml_access_token()

    def _ml_access_token(self) -> str:
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

    @property
    def payments_configured(self) -> bool:
        return self.pixzy_config.configured

    @staticmethod
    def validate_payment_url(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2048:
            raise ValueError(f'{field} inválida.')
        value = value.strip().rstrip('/')
        try:
            parsed = urllib.parse.urlsplit(value)
            loopback = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
            valid_scheme = parsed.scheme == 'https' or (parsed.scheme == 'http' and loopback)
            if not valid_scheme or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError
        except ValueError:
            raise ValueError(f'{field} inválida. Use HTTPS.') from None
        return value

    def save_pixzy_config(self, api_token: str, base_url: str, webhook_url: str):
        if self.pixzy_managed_by_environment:
            raise PermissionError('A PixzyPay é gerenciada pelas variáveis de ambiente do servidor.')
        current_token = self.pixzy_config.api_token
        if api_token:
            if not isinstance(api_token, str) or not 10 <= len(api_token.strip()) <= 4096:
                raise ValueError('Token da PixzyPay inválido.')
            current_token = api_token.strip()
        if not current_token:
            raise ValueError('Informe o token da PixzyPay.')
        base_url = self.validate_payment_url(base_url, 'URL da API')
        webhook_url = self.validate_payment_url(webhook_url, 'URL do webhook')
        config = PixzyConfig(api_token=current_token, base_url=base_url, webhook_url=webhook_url)
        self.store.save_payment_config(current_token, base_url, webhook_url)
        self.pixzy_config = config
        self.payment_gateway = PixzyClient(config)

    @staticmethod
    def public_payment(payment: dict) -> dict:
        return {
            'id': payment['public_id'],
            'orderId': payment['order_public_id'],
            'provider': payment['provider'],
            'amount': payment['amount'],
            'status': payment['status'],
            'brCode': payment['pix_code'],
            'createdAt': payment['created_at'],
            'updatedAt': payment['updated_at'],
        }

    def create_checkout(self, slug: str, body: dict) -> dict:
        if not self.payments_configured:
            raise PaymentGatewayError('Pagamentos via Pix ainda não estão configurados.', status=503)
        product = self.store.get_product(slug)
        if product is None or product.get('redirect') is not False:
            raise ValueError('Este produto não está disponível para checkout próprio.')
        quantity = body.get('quantity', 1)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 100:
            raise ValueError('Quantidade inválida.')
        stock = product.get('stock', -1)
        if isinstance(stock, int) and stock >= 0 and quantity > stock:
            raise ValueError('Quantidade indisponível em estoque.')
        client_key = body.get('idempotencyKey')
        if not isinstance(client_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', client_key):
            raise ValueError('Identificador da tentativa inválido.')
        customer = body.get('customer')
        if not isinstance(customer, dict):
            raise ValueError('Informe os dados do pagador.')
        limits = {'name': 120, 'email': 254, 'document': 20, 'phone': 30}
        cleaned = {}
        for field, limit in limits.items():
            value = customer.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                raise ValueError('Preencha corretamente os dados do pagador.')
            cleaned[field] = value.strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', cleaned['email']):
            raise ValueError('Informe um e-mail válido.')
        if len(re.sub(r'\D', '', cleaned['document'])) not in (11, 14):
            raise ValueError('Informe um CPF ou CNPJ válido.')

        with self.payment_lock:
            order = self.store.create_or_get_order(product, cleaned, client_key, quantity)
            existing = self.store.payment_for_order(order['id'])
            if existing:
                existing['order_public_id'] = order['public_id']
                return self.public_payment(existing)
            items = json.loads(order['items_json'])
            payload = {
                'amount': order['amount'],
                'client_name': cleaned['name'],
                'client_email': cleaned['email'],
                'client_doc': cleaned['document'],
                'client_phone': cleaned['phone'],
                'webhook_url': self.pixzy_config.webhook_url,
                'metadata': {'order_id': order['public_id']},
                'items': items,
            }
            try:
                transaction = self.payment_gateway.create_payment(payload)
                payment = self.store.save_payment(order, transaction)
            except PaymentGatewayError as error:
                payment_log('payment_creation_failed', orderId=order['public_id'], provider=PIXZY_PROVIDER, status=error.status)
                raise
            payment_log(
                'payment_created', paymentId=payment['public_id'], orderId=order['public_id'],
                providerTransactionId=payment['provider_transaction_id'], status=payment['status'],
            )
            return self.public_payment(payment)

    def reconcile_payment(self, payment: dict, source: str = 'reconciliation') -> dict:
        remote = self.payment_gateway.get_payment(payment['provider_transaction_id'])
        remote_id = str(remote.get('id') or remote.get('transaction_id') or '')
        remote_amount = remote.get('amount')
        remote_status = str(remote.get('status') or '')
        if remote_id != payment['provider_transaction_id'] or remote_amount != payment['amount']:
            raise PaymentGatewayError('Dados divergentes ao confirmar o pagamento.', details='transaction identity mismatch')
        if remote_status not in PAYMENT_STATUSES:
            raise PaymentGatewayError('Status inválido retornado pelo gateway.', details=remote_status)
        self.store.apply_payment_status(payment, remote_status, source, remote)
        updated = self.store.get_payment(payment['public_id'])
        payment_log(
            'payment_reconciled', paymentId=payment['public_id'], orderId=payment['order_public_id'],
            providerTransactionId=payment['provider_transaction_id'], status=remote_status,
        )
        return self.public_payment(updated)

    def process_pixzy_webhook(self, body: dict) -> bool:
        event = body.get('event')
        transaction = body.get('transaction')
        if event not in ('paid', 'expired', 'failed') or not isinstance(transaction, dict):
            raise ValueError('Evento de pagamento inválido.')
        transaction_id = str(transaction.get('id') or transaction.get('transaction_id') or '')
        status = str(transaction.get('status') or '')
        if not transaction_id or status != event:
            raise ValueError('Status da transação inconsistente.')
        payment = self.store.get_payment_by_transaction(transaction_id)
        if payment is None:
            raise LookupError('Transação não encontrada.')
        if transaction.get('amount') != payment['amount']:
            raise ValueError('Valor da transação divergente.')
        metadata = transaction.get('metadata') or {}
        if not isinstance(metadata, dict) or metadata.get('order_id') != payment['order_public_id']:
            raise ValueError('Pedido da transação divergente.')
        event_key = hashlib.sha256(
            f'{PIXZY_PROVIDER}:{transaction_id}:{event}:{status}'.encode('utf-8')
        ).hexdigest()
        if self.store.webhook_processed(event_key):
            return False

        # PixzyPay currently documents no signature. Confirm provider state
        # directly before changing an order's financial status.
        remote = self.payment_gateway.get_payment(transaction_id)
        remote_id = str(remote.get('id') or remote.get('transaction_id') or '')
        if remote_id != transaction_id or remote.get('amount') != payment['amount'] or remote.get('status') != status:
            raise ValueError('A confirmação direta da transação divergiu do webhook.')
        applied = self.store.apply_payment_status(
            payment, status, 'webhook', body, event_key=event_key, event=event,
        )
        payment_log(
            'payment_webhook_received', paymentId=payment['public_id'], orderId=payment['order_public_id'],
            providerTransactionId=transaction_id, webhookEvent=event, status=status,
        )
        if applied:
            payment_log(
                f'payment_{status}', paymentId=payment['public_id'], orderId=payment['order_public_id'],
                providerTransactionId=transaction_id, status=status,
            )
        return applied



class NexoHandler(BaseHTTPRequestHandler):
    server: NexoServer

    def log_message(self, format, *args):
        # Request query strings can carry OAuth codes; only log method/path/status.
        print(json.dumps({'method': self.command, 'path': urllib.parse.urlsplit(self.path).path,
                          'status': str(args[1]) if len(args) > 1 else ''}), flush=True)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith(('/api/mercadolivre/', '/api/payments/config', '/api/payments/account', '/api/traffic/live', '/api/orders')) and self.authorized() and self.identity['tenant'] != 'admin':
            return self.send_json_error('Use as conexões próprias na Central de Integrações.', HTTPStatus.FORBIDDEN)
        if path == "/health":
            return self.send_json({"ok": True, "geoipReady": self.server.geo.ready})
        if path == '/api/auth/session':
            if not self.authorized():
                return self.send_json_error('Sessão não autenticada.', HTTPStatus.UNAUTHORIZED)
            return self.send_json({'authenticated': True, **self.identity})
        if path in ('/api/integrations', '/admin/integrations'):
            if not self.authorized():
                return self.require_auth()
            if path == '/admin/integrations':
                return self.send_html(render_integrations(self.identity))
            return self.send_json({'providers': self.server.connections.list(self.identity['tenant']), 'identity': self.identity})
        if path == '/api/integrations/audit':
            if not self.authorized():
                return self.require_auth()
            if self.identity['role'] != 'owner':
                return self.send_json_error('Auditoria restrita ao proprietário.', HTTPStatus.FORBIDDEN)
            with self.server.store.connect() as db:
                db.row_factory = sqlite3.Row
                rows = db.execute('SELECT actor,provider,action,created_at FROM integration_audit WHERE tenant=? ORDER BY id DESC LIMIT 100', (self.identity['tenant'],)).fetchall()
            return self.send_json({'entries': [dict(row) for row in rows]})
        if path == '/api/integrations/mercadolivre/connect':
            if not self.authorized():
                return self.require_auth()
            if self.identity['role'] != 'owner':
                return self.send_json_error('Somente o proprietário pode gerenciar conexões.', HTTPStatus.FORBIDDEN)
            try:
                return self.redirect(self.server.connections.oauth_start(self.identity))
            except (IntegrationError, ValueError) as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/integrations/mercadolivre/callback':
            if not self.authorized():
                return self.require_auth()
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                if self.identity['role'] != 'owner':
                    raise IntegrationError('Somente o proprietário pode autorizar a conta.')
                self.server.connections.oauth_finish((query.get('state') or [''])[0], (query.get('code') or [''])[0], self.identity)
                return self.send_oauth_result(True, 'Conexão autorizada. Volte à Central de Integrações.')
            except (ValueError, MercadoLivreError) as error:
                return self.send_oauth_result(False, str(error))
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
                'error': '' if token else self.server.ml_last_error,
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
                with self.server.ml_oauth_lock():
                    self.server.store.save_token(token)
            except (MercadoLivreError, KeyError) as error:
                self.server.ml_last_error = str(error)
                return self.send_oauth_result(False, self.server.ml_last_error)
            self.server.ml_last_error = ''
            return self.send_oauth_result(True, 'Conta do Mercado Livre conectada com sucesso.')
        if path == '/api/payments/config':
            if not self.authorized():
                return self.require_auth()
            return self.send_json({
                'provider': PIXZY_PROVIDER,
                'label': 'PixzyPay',
                'configured': self.server.payments_configured,
                'credential': 'Configurada no servidor' if self.server.pixzy_config.api_token else 'Não configurada',
                'baseUrl': self.server.pixzy_config.base_url,
                'webhookUrl': self.server.pixzy_config.webhook_url,
                'webhookConfigured': bool(self.server.pixzy_config.webhook_url),
                'managedByEnvironment': self.server.pixzy_managed_by_environment,
            })
        if path == '/api/payments/account':
            if not self.authorized():
                return self.require_auth()
            if not self.server.payments_configured:
                return self.send_json_error('Configure a PixzyPay no servidor.', HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                account = self.server.payment_gateway.get_account()
                return self.send_json({'ok': True, 'accountId': str(account.get('id') or '')})
            except PaymentGatewayError as error:
                payment_log('pixzy_unavailable', status=error.status)
                return self.send_json_error(str(error), self.gateway_status(error))
        if path == "/api/traffic/live":
            if not self.authorized():
                return self.require_auth()
            result = self.server.store.live()
            result["geoipReady"] = self.server.geo.ready
            return self.send_json(result)
        if path == '/api/orders':
            if not self.authorized():
                return self.require_auth()
            return self.send_json({'orders': self.server.store.list_orders()})
        if path.startswith('/api/payments/'):
            payment_id = path[len('/api/payments/'):]
            if not re.fullmatch(r'[a-f0-9]{32}', payment_id):
                return self.send_error(HTTPStatus.NOT_FOUND)
            payment = self.server.store.get_payment(payment_id)
            if payment is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_json(self.server.public_payment(payment))
        if path == '/api/products':
            if not self.authorized():
                return self.require_auth()
            return self.send_json({'products': self.repository.list_products()})
        if path == '/api/products/draft':
            if not self.authorized():
                return self.require_auth()
            key = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('id') or [''])[0]
            product = next((p for p in self.repository.list_products() if p.get('id') == key), None) or self.repository.get_draft(key)
            if not product:
                return self.send_json_error('Rascunho não encontrado.', HTTPStatus.NOT_FOUND)
            return self.send_json(product)
        if path.startswith('/api/products/'):
            slug = path[len('/api/products/'):]
            if not PRODUCT_SLUG_RE.fullmatch(slug):
                return self.send_error(HTTPStatus.NOT_FOUND)
            product, public_repository = public_product(self.server.store, slug)
            if product is None or not product.get('rightsConfirmed'):
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_json({k: v for k, v in product.items() if not k.startswith('_') and k != 'authorization'})
        if path.startswith("/api/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if path.startswith('/media/'):
            name = path[len('/media/'):]
            if not re.fullmatch(r'(?:mlb[0-9]{7,15}-[0-9]+|upload-[a-f0-9]{32})\.(jpg|png|webp)', name):
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.serve_file(self.server.media_root / name)
        if path in ('/', '/index.html', '/login') and not self.authorized():
            error = 'invalid' if 'login_error' in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query) else ''
            return self.serve_login(error=error)
        if path == '/login':
            return self.redirect('/')
        if path == '/admin/import':
            if not self.authorized():
                return self.require_auth()
            return self.send_html(render_admin(self.repository.list_products()), (self.server.connections.get(self.identity['tenant'], 'generic') or {}).get('mediaHosts', []))
        if path.startswith(('/p/', '/produto/')):
            slug = path.split('/')[-1]
            product, public_repository = public_product(self.server.store, slug) if PRODUCT_SLUG_RE.fullmatch(slug) else (None, None)
            if product is None or not product.get('rightsConfirmed'):
                return self.send_error(HTTPStatus.NOT_FOUND)
            recommendations = public_repository.own_recommendations(product.get('recommendations', []))
            return self.send_html(render_product(product, recommendations, self.server.import_ttl), product.get('allowedMediaHosts', []))
        if path == "/":
            path = "/index.html"
        asset = (self.server.web_root / path.lstrip("/")).resolve()
        if not asset.is_relative_to(self.server.web_root) or not asset.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        return self.serve_file(asset)

    def serve_login(self, error='', username='', status=HTTPStatus.OK):
        messages = {
            'invalid': 'Usuário ou senha incorretos. Confira os dados salvos e tente novamente.',
            'rate_limit': 'Muitas tentativas de acesso. Aguarde um minuto antes de tentar novamente.',
        }
        message = messages.get(error)
        feedback = (
            '<div id="login-error" class="error" role="alert" tabindex="-1">'
            + html.escape(message) + '</div>'
        ) if message else ''
        template = (self.server.web_root / 'login.html').read_text(encoding='utf-8')
        content = template.replace('<!--LOGIN_FEEDBACK-->', feedback).replace(
            '__NEXO_LOGIN_USERNAME__', html.escape(username[:80], quote=True),
        )
        return self.send_html(content, status=status)

    def send_html(self, content, media_hosts=None, status=HTTPStatus.OK):
        from universal_import import validate_host
        origins = ' '.join('https://' + validate_host(host) for host in (media_hosts or []))
        nonce = secrets.token_urlsafe(24)
        data = content.replace('<script>', f'<script nonce="{nonce}">').encode('utf-8')
        self.send_response(status)
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            self.send_header('Retry-After', '60')
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', f"default-src 'self'; img-src 'self' https://*.mlstatic.com {origins}; frame-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

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
        checkout_match = re.fullmatch(r'/api/products/(' + PRODUCT_SLUG_RE.pattern + r')/checkout', path)
        reconcile_match = re.fullmatch(r'/api/payments/([a-f0-9]{32})/reconcile', path)
        public_paths = (
            '/login', '/api/traffic/event', '/api/mercadolivre/notify',
            '/api/auth/login', '/api/auth/logout', '/api/webhooks/pixzypay/transactions',
        )
        connection_action = re.fullmatch(r'/api/integrations/(generic|mercadolivre)/(save|test|revoke)', path)
        known_path = connection_action is not None or path in (
            *public_paths, '/api/products/import', '/api/products/publish',
            '/api/media/upload', '/api/payments/config', '/api/catalog/products', '/api/accounts', '/api/products/preview',
        )
        if not known_path and checkout_match is None and reconcile_match is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        is_public = path in public_paths or checkout_match is not None or reconcile_match is not None
        if not is_public and not self.authorized():
            return self.require_auth()
        if not is_public and self.identity['role'] == 'viewer':
            return self.send_json_error('Perfil somente leitura.', HTTPStatus.FORBIDDEN)
        if not is_public and self.identity['tenant'] != 'admin' and path == '/api/payments/config':
            return self.send_json_error('Configuração global restrita.', HTTPStatus.FORBIDDEN)
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
            if not self.allow_rate('login:' + self.client_ip(), 20):
                return self.serve_login('rate_limit', username, HTTPStatus.TOO_MANY_REQUESTS)
            if len(username) > 80 or len(password) > 256:
                return self.serve_login('invalid', username, HTTPStatus.UNAUTHORIZED)
            token = self.server.create_admin_session() if self.server.valid_admin_login(username, password) else self.server.connections.login(username, password)
            if not token:
                return self.serve_login('invalid', username, HTTPStatus.UNAUTHORIZED)
            secure = '; Secure' if self.forwarded_proto() == 'https' else ''
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header('Location', '/')
            self.send_header('Cache-Control', 'no-store')
            self.send_header(
                'Set-Cookie',
                f'nexo_admin={token}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; HttpOnly; SameSite=Lax{secure}',
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
        max_length = 9 * 1024 * 1024 if path == '/api/products/import' else 65536 if path in ('/api/products/publish', '/api/products/preview') else 16384
        if length < 1 or length > max_length or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_error(HTTPStatus.BAD_REQUEST)
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if path == '/api/auth/login':
            if not self.allow_rate('login:' + self.client_ip(), 20):
                return self.send_json_error('Muitas tentativas. Aguarde um minuto.', HTTPStatus.TOO_MANY_REQUESTS)
            username = body.get('username')
            password = body.get('password')
            if not (
                isinstance(username, str)
                and isinstance(password, str)
                and len(username) <= 80 and len(password) <= 256
            ):
                return self.send_json_error('Usuário ou senha incorretos.', HTTPStatus.UNAUTHORIZED)
            token = self.server.create_admin_session() if self.server.valid_admin_login(username, password) else self.server.connections.login(username, password)
            if not token:
                return self.send_json_error('Usuário ou senha incorretos.', HTTPStatus.UNAUTHORIZED)
            secure = '; Secure' if self.forwarded_proto() == 'https' else ''
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header(
                'Set-Cookie',
                f'nexo_admin={token}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; HttpOnly; SameSite=Lax{secure}',
            )
            data = json.dumps({'authenticated': True, 'user': username}).encode('utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/api/auth/logout':
            token = self.admin_session_token()
            if token:
                self.server.delete_admin_session(token)
            self.server.connections.logout(token)
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
        if path == '/api/payments/config':
            try:
                api_token = body.get('apiToken', '')
                base_url = body.get('baseUrl', '')
                webhook_url = body.get('webhookUrl', '')
                if not all(isinstance(value, str) for value in (api_token, base_url, webhook_url)):
                    raise ValueError('Configuração da PixzyPay inválida.')
                self.server.save_pixzy_config(api_token, base_url, webhook_url)
                return self.send_json({
                    'ok': True,
                    'configured': self.server.payments_configured,
                    'credential': 'Configurada no servidor',
                    'baseUrl': self.server.pixzy_config.base_url,
                    'webhookUrl': self.server.pixzy_config.webhook_url,
                    'webhookConfigured': True,
                    'managedByEnvironment': False,
                })
            except PermissionError as error:
                return self.send_json_error(str(error), HTTPStatus.CONFLICT)
            except ValueError as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if checkout_match is not None:
            try:
                payment = self.server.create_checkout(checkout_match.group(1), body)
                return self.send_json(payment, HTTPStatus.CREATED)
            except ValueError as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
            except PaymentGatewayError as error:
                return self.send_json_error(
                    'Não foi possível gerar o pagamento. Tente novamente.', self.gateway_status(error),
                )
        if reconcile_match is not None:
            payment = self.server.store.get_payment(reconcile_match.group(1))
            if payment is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            try:
                return self.send_json(self.server.reconcile_payment(payment))
            except PaymentGatewayError as error:
                return self.send_json_error('Não foi possível consultar o pagamento.', self.gateway_status(error))
        if path == '/api/webhooks/pixzypay/transactions':
            try:
                processed = self.server.process_pixzy_webhook(body)
                return self.send_json({'ok': True, 'processed': processed})
            except LookupError:
                return self.send_json_error('Transação não encontrada.', HTTPStatus.NOT_FOUND)
            except ValueError as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
            except PaymentGatewayError as error:
                payment_log('pixzy_unavailable', status=error.status)
                return self.send_json_error('Não foi possível validar a transação.', self.gateway_status(error))
        if connection_action:
            if self.identity['role'] != 'owner':
                return self.send_json_error('Somente o proprietário pode gerenciar conexões.', HTTPStatus.FORBIDDEN)
            provider, action = connection_action.groups()
            try:
                if action == 'save':
                    response = self.server.connections.save(self.identity, provider, body)
                elif action == 'revoke':
                    response = self.server.connections.revoke(self.identity, provider)
                else:
                    if not self.allow_rate('test:' + self.identity['tenant'], 10):
                        return self.send_json_error('Aguarde antes de testar novamente.', HTTPStatus.TOO_MANY_REQUESTS)
                    response = self.server.connections.test(self.identity, provider, body.get('url', ''))
                return self.send_json(response)
            except (ValueError, MercadoLivreError) as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/accounts':
            if self.identity['tenant'] != 'admin' or self.identity['role'] != 'owner':
                return self.send_json_error('Cadastro restrito ao administrador.', HTTPStatus.FORBIDDEN)
            try:
                username = body.get('username')
                if username == self.server.admin_user:
                    raise IntegrationError('Nome reservado ao administrador.')
                tenant = body.get('tenant') or secrets.token_hex(16)
                if not isinstance(tenant, str) or not re.fullmatch(r'[a-f0-9]{32}', tenant):
                    raise IntegrationError('Organização inválida.')
                self.server.connections.create_user(username, body.get('password'), tenant, body.get('role', 'owner'))
                self.server.connections.audit(self.identity, 'accounts', 'create_user')
                return self.send_json({'ok': True, 'tenant': tenant})
            except ValueError as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/products/preview':
            try:
                product = self.repository.get_draft(body.get('id', ''))
                if not product:
                    return self.send_json_error('Rascunho não encontrado.', HTTPStatus.NOT_FOUND)
                self.apply_preview_edits(product, body.get('page') or {})
                recommendations = body.get('recommendations', [])
                if not isinstance(recommendations, list) or len(recommendations) > 20 or any(not isinstance(v, str) for v in recommendations):
                    raise ValueError('Recomendações inválidas.')
                return self.send_html(render_product(product, self.repository.own_recommendations(recommendations), self.server.import_ttl), product.get('allowedMediaHosts', []))
            except ValueError as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/catalog/products':
            # Explicit operator-owned records in the existing product repository.
            name, seller, destination = (body.get(k) for k in ('name', 'storeName', 'sourceUrl'))
            if any(not isinstance(v, str) or not v.strip() or len(v) > 2048 for v in (name, seller, destination)) or body.get('rightsConfirmed') is not True:
                return self.send_json_error('Informe produto, vendedor, destino e autorização de uso.', HTTPStatus.BAD_REQUEST)
            try:
                validate_url(destination)
            except ValueError:
                return self.send_json_error('Destino HTTPS público inválido.', HTTPStatus.BAD_REQUEST)
            identity = hashlib.sha256((seller + destination).encode()).hexdigest()[:16]
            own = {'id': 'SRC' + identity.upper(), 'slug': 'p-' + identity,
                   'name': name.strip(), 'storeName': seller.strip(), 'sourceUrl': destination,
                   'catalogOwner': 'own_catalog', 'source': 'own_catalog', 'market': 'Catálogo próprio',
                   'provenance': {'name': 'own_catalog', 'sourceUrl': 'own_catalog'},
                   'price': '', 'images': [], 'reviews': [], 'redirect': True,
                   'rightsConfirmed': True, 'syncedAt': time.time()}
            self.repository.save_product(own)
            return self.send_json(own)
        if path == '/api/products/import':
            try:
                if not self.allow_rate('import:' + self.identity['tenant'], 10):
                    return self.send_json_error('Limite de importações. Aguarde um minuto.', HTTPStatus.TOO_MANY_REQUESTS)
                source_url = body.get('url') or ''
                if not isinstance(source_url, str):
                    raise SourceError('URL inválida.')
                source_url = source_url.strip()
                if re.fullmatch(r'MLB[-_]?\d{7,15}', source_url, re.I):
                    source_url = 'https://produto.mercadolivre.com.br/' + source_url
                validate_url(source_url)
                market, _ = market_for_url(source_url)
                supplied_html = body.get('html')
                config = self.server.connections.get(self.identity['tenant'], 'generic') or {}
                if market == 'mercadolivre' and supplied_html is not None:
                    raise MercadoLivreError('Use a API autorizada; HTML do Mercado Livre não é aceito.')
                if market != 'mercadolivre' and supplied_html is not None and body.get('authorizationConfirmed') is not True:
                    raise SourceError('Confirme autorização para usar o HTML fornecido.')
                if market != 'mercadolivre' and supplied_html is None and body.get('manual') is not True:
                    validate_url(source_url, config.get('allowedHosts', []))
                    if not config.get('authorizationConfirmed'):
                        raise SourceError('Autorize a fonte na Central de Integrações.')
                scoped = self.server.connections.get(self.identity['tenant'], 'mercadolivre') if market == 'mercadolivre' else None
                cached = self.repository.cached_import(source_url)
                if cached and cached.get('_connectionRevision') != (scoped or {}).get('revision'):
                    cached = None
                if market == 'mercadolivre' and self.identity['tenant'] != 'admin' and not scoped and body.get('manual') is not True:
                    raise MercadoLivreError('Conecte sua conta na Central de Integrações.')
                # Reuse API cache only. HTML/config changes must always be reprocessed.
                if market == 'mercadolivre' and body.get('manual') is not True and body.get('revalidate') is not True and cached and time.time() - cached.get('syncedAt', 0) < self.server.import_ttl:
                    return self.send_json(cached)
                if body.get('manual') is True:
                    if body.get('authorizationConfirmed') is not True:
                        raise SourceError('Confirme a autorização para os dados próprios.')
                    from page_import import blank_product
                    product = decorate(blank_product(source_url), 'user_supplied')
                    product.update(inputUrl=source_url, allowedMediaHosts=config.get('mediaHosts') or [validate_url(source_url)[1]])
                elif market == 'mercadolivre':
                    if scoped:
                        token = self.server.connections.access_token(self.identity)
                    elif self.identity['tenant'] == 'admin':
                        token = self.server.ml_access_token()
                    else:
                        raise MercadoLivreError('Conecte sua conta na Central de Integrações.')
                    adapter = MercadoLivreAdapter()
                    try:
                        product = adapter.extract(source_url, token, int(os.environ.get('ML_REVIEW_MAX_PAGES', '1')))
                    except MercadoLivreError as error:
                        if error.status != 401:
                            raise
                        token = self.server.connections.access_token(self.identity, token) if scoped else self.server.ml_retry_token(token)
                        product = adapter.extract(source_url, token, int(os.environ.get('ML_REVIEW_MAX_PAGES', '1')))
                else:
                    product = GenericAdapter().extract(source_url, config=config, html=supplied_html)
                if scoped:
                    product['_connectionRevision'] = scoped.get('revision')
                product['authorization'] = {**product.get('authorization', {}), 'confirmedBy': self.identity['user'], 'manualConfirmed': body.get('authorizationConfirmed') is True}
                self.repository.save_draft(product)
                return self.send_json(product)
            except (MercadoLivreError, ImportErrorMessage, ValueError, RecursionError) as error:
                return self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        if path == '/api/products/publish':
            item_id = body.get('id')
            if not isinstance(item_id, str) or not PRODUCT_ID_RE.fullmatch(item_id):
                return self.send_json_error('Produto inválido.', HTTPStatus.BAD_REQUEST)
            redirect = body.get('redirect')
            if not isinstance(redirect, bool):
                return self.send_json_error('Modo de venda inválido.', HTTPStatus.BAD_REQUEST)
            if not redirect and self.identity['tenant'] != 'admin':
                return self.send_json_error('Esta conta publica links para a fonte original.', HTTPStatus.BAD_REQUEST)
            if not redirect and not self.server.payments_configured:
                return self.send_json_error(
                    'Configure a PixzyPay antes de ativar o checkout próprio.', HTTPStatus.BAD_REQUEST,
                )
            product = self.repository.get_draft(item_id)
            if product is None:
                return self.send_json_error('Importe o produto antes de publicar.', HTTPStatus.NOT_FOUND)
            if product.get('market') == 'Mercado Livre' and redirect is not True:
                return self.send_json_error('Este anúncio deve vincular ao Mercado Livre.', HTTPStatus.BAD_REQUEST)
            if body.get('rightsConfirmed') is not True:
                return self.send_json_error('Confirme os direitos de publicação dos dados.', HTTPStatus.BAD_REQUEST)
            published = self.repository.get_product(product['slug']) or {}
            recommendations = body.get('recommendations', published.get('recommendations', []))
            if not isinstance(recommendations, list) or len(recommendations) > 20 or any(not isinstance(v, str) or not PRODUCT_SLUG_RE.fullmatch(v) for v in recommendations):
                return self.send_json_error('Recomendações inválidas.', HTTPStatus.BAD_REQUEST)
            for value in recommendations:
                own = self.repository.get_product(value)
                if not own or own.get('catalogOwner') != 'own_catalog':
                    return self.send_json_error('Selecione somente produtos do catálogo próprio.', HTTPStatus.BAD_REQUEST)
            product['recommendations'] = list(dict.fromkeys(recommendations))
            product['rightsConfirmed'] = body.get('rightsConfirmed') is True
            product.setdefault('storeName', 'Minha loja')
            edits = body.get('page') or {}
            if not isinstance(edits, dict):
                return self.send_json_error('Dados da página inválidos.', HTTPStatus.BAD_REQUEST)
            for field, limit in (('storeName', 80), ('name', 200), ('price', 80), ('description', 10000), ('brand', 100), ('shippingText', 200), ('installmentText', 200)):
                if field in edits:
                    value = edits[field]
                    if not isinstance(value, str) or len(value) > limit or (field in ('storeName', 'name') and not value.strip()):
                        return self.send_json_error(f'Campo {field} inválido.', HTTPStatus.BAD_REQUEST)
                    if product.get(field) != value.strip():
                        product.setdefault('provenance', {})[field] = 'user_supplied'
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
                if not isinstance(images, list) or not 0 <= len(images) <= 10:
                    return self.send_json_error('Informe até 10 fotos.', HTTPStatus.BAD_REQUEST)
                for image in images:
                    if not isinstance(image, str) or len(image) > 2048:
                        return self.send_json_error('URL de foto inválida.', HTTPStatus.BAD_REQUEST)
                    try:
                        parsed = urllib.parse.urlsplit(image)
                        remote = parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and not parsed.username
                    except ValueError:
                        remote = False
                    if product.get('market') == 'Mercado Livre' and not (approved_media(image) or local_media(image)):
                        return self.send_json_error('Origem de mídia não autorizada.', HTTPStatus.BAD_REQUEST)
                    if not (remote or re.fullmatch(r'/media/(?:mlb[0-9]{7,15}-[0-9]+|upload-[a-f0-9]{32})\.(jpg|png|webp)', image)):
                        return self.send_json_error('Use URLs HTTPS para as fotos.', HTTPStatus.BAD_REQUEST)
                if any(not allowed_product_media(image, product) for image in images):
                    return self.send_json_error('Origem de mídia não autorizada.', HTTPStatus.BAD_REQUEST)
                if product.get('images') != images:
                    product.setdefault('provenance', {})['images'] = 'user_supplied'
                product['images'] = images
            if 'reviews' in edits:
                reviews = edits['reviews']
                if not isinstance(reviews, list) or any(review not in product['reviews'] for review in reviews):
                    return self.send_json_error('Os comentários podem ser removidos, mas não alterados.', HTTPStatus.BAD_REQUEST)
                if product.get('reviews') != reviews:
                    product['reviewsFilteredByUser'] = True
                product['reviews'] = reviews
            if not product.get('storeName') or not product.get('name'):
                return self.send_json_error('Complete loja e título antes de publicar.', HTTPStatus.BAD_REQUEST)
            product['redirect'] = redirect
            self.update_edit_diagnostics(product, edits)
            self.repository.save_product(product)
            return self.send_json(product)
        page = body.get('page')
        if not isinstance(page, str) or not page.startswith('/p/') or not PRODUCT_SLUG_RE.fullmatch(page[3:]) or public_product(self.server.store, page[3:])[0] is None or body.get('kind') not in ('view', 'heartbeat'):
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

    @property
    def identity(self):
        return getattr(self, '_identity', None) or {'user': self.server.admin_user, 'tenant': 'admin', 'role': 'owner'}

    @property
    def repository(self):
        return TenantRepository(self.server.store, self.identity['tenant'])

    def allow_rate(self, key, maximum):
        with self.server.rate_lock:
            now = time.time()
            rates = self.server.import_rates
            # Bound memory and remove expired tenant/IP buckets.
            for old in list(rates):
                rates[old] = [t for t in rates[old] if now - t < 60]
                if not rates[old]:
                    del rates[old]
            entries = rates.setdefault(key, [])
            if len(entries) >= maximum:
                return False
            entries.append(now)
            return True

    def authorized(self) -> bool:
        token = self.admin_session_token()
        if token.startswith('u.'):
            identity = self.server.connections.identity(token)
            if identity:
                self._identity = identity
                return True
        if token and self.server.has_admin_session(token):
            self._identity = {'user': self.server.admin_user, 'tenant': 'admin', 'role': 'owner'}
            return True
        value = self.headers.get('Authorization', '')
        if not value.startswith('Basic '):
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return False
        username, separator, password = decoded.partition(':')
        if separator and self.server.valid_admin_login(username, password):
            self._identity = {'user': self.server.admin_user, 'tenant': 'admin', 'role': 'owner'}
            return True
        return False

    def apply_preview_edits(self, product, edits):
        if not isinstance(edits, dict):
            raise ValueError('Dados da página inválidos.')
        for field, limit in (('storeName', 80), ('name', 200), ('price', 80), ('description', 10000)):
            if field in edits:
                if not isinstance(edits[field], str) or len(edits[field]) > limit:
                    raise ValueError('Campo inválido: ' + field)
                value = edits[field].strip()
                if product.get(field) != value:
                    product.setdefault('provenance', {})[field] = 'user_supplied'
                product[field] = value
        if 'images' in edits:
            if not isinstance(edits['images'], list) or len(edits['images']) > 10 or any(not allowed_product_media(u, product) for u in edits['images']):
                raise ValueError('Origem de mídia não autorizada.')
            product['images'] = edits['images']
        if 'reviews' in edits:
            if not isinstance(edits['reviews'], list) or any(review not in product.get('reviews', []) for review in edits['reviews']):
                raise ValueError('Os comentários só podem ser removidos.')
            product['reviews'] = edits['reviews']
        self.update_edit_diagnostics(product, edits)

    def update_edit_diagnostics(self, product, edits):
        for field in edits:
            if product.get('provenance', {}).get(field) == 'user_supplied':
                product.setdefault('sourceStatus', {})[field] = {'state': 'available' if product.get(field) not in ('', [], None) else 'absent'}
                product.setdefault('fieldDiagnostics', {})[field] = {'value': product.get(field), 'source': 'user_supplied', 'selector_or_path': None, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'confidence': 1.0, 'status': 'available' if product.get(field) not in ('', [], None) else 'absent', 'error': None}

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

    @staticmethod
    def gateway_status(error: PaymentGatewayError) -> HTTPStatus:
        if error.status in (401, 403, 404, 422, 429, 500, 503):
            return HTTPStatus(error.status)
        return HTTPStatus.BAD_GATEWAY

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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
    print('Nexo admin password generated and saved in the private data volume. Configure NEXO_ADMIN_PASSWORD to replace it.', flush=True)
    return password


def main():
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
