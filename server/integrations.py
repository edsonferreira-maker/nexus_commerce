"""Account-scoped credentials, identities and OAuth. All secrets stay server-side."""
import base64
import fcntl
import hashlib
import hmac
import json
import math
import re
import secrets
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

from mercadolivre import MercadoLivreError, request_json, refresh_token

PROVIDERS = [
    {'id': 'mercadolivre', 'label': 'Mercado Livre', 'auth': 'oauth2',
     'permissions': 'Autorize sua conta e habilite offline_access para renovação.',
     'fields': ['clientId', 'clientSecret', 'redirectUri', 'scopes', 'pkce']},
    {'id': 'generic', 'label': 'Loja autorizada (HTML / JSON-LD)', 'auth': 'api_key_optional',
     'permissions': 'Somente domínios com autorização de leitura automatizada e uso de conteúdo.',
     'fields': ['allowedHosts', 'mediaHosts', 'apiKey', 'headers', 'selectors', 'region', 'environment', 'authorizationConfirmed']},
]


class IntegrationError(ValueError):
    pass


def host_list(value):
    from universal_import import validate_host
    if not isinstance(value, list) or not value or len(value) > 20:
        raise IntegrationError('Informe de 1 a 20 domínios exatos autorizados.')
    return list(dict.fromkeys(validate_host(v) for v in value))


def public_config(provider, data):
    fields = ('clientId', 'redirectUri', 'scopes', 'pkce') if provider == 'mercadolivre' else (
        'allowedHosts', 'mediaHosts', 'headers', 'selectors', 'region', 'environment', 'authorizationConfirmed')
    result = {k: data[k] for k in fields if k in data}
    result['credential'] = '••••••••' if any(data.get(k) for k in ('clientSecret', 'apiKey', 'access_token')) else ''
    result['connected'] = bool(data.get('access_token')) if provider == 'mercadolivre' else bool(data.get('authorizationConfirmed'))
    return result


class IntegrationStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS account_users (
                username TEXT PRIMARY KEY, tenant TEXT NOT NULL, role TEXT NOT NULL,
                password_hash TEXT NOT NULL, salt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tenant_connections (
                tenant TEXT NOT NULL, provider TEXT NOT NULL, encrypted TEXT NOT NULL,
                status TEXT NOT NULL, checked_at INTEGER, message TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(tenant, provider));
            CREATE TABLE IF NOT EXISTS integration_audit (
                id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                provider TEXT NOT NULL, action TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tenant_oauth (
                state_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                encrypted TEXT NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tenant_records (
                tenant TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
                data_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(tenant, kind, key));
            CREATE UNIQUE INDEX IF NOT EXISTS public_tenant_slugs ON tenant_records(key) WHERE kind='product';
            ''')

    def encrypt(self, data):
        return self.store.token_cipher().encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.store.token_cipher().decrypt(value.encode()))

    def audit(self, identity, provider, action, db=None):
        if db is None:
            with self.store.connect() as connection:
                self.audit(identity, provider, action, connection)
        else:
            db.execute('INSERT INTO integration_audit (tenant,actor,provider,action,created_at) VALUES (?,?,?,?,?)',
                       (identity['tenant'], identity['user'], provider, action, int(time.time())))

    def create_user(self, username, password, tenant, role='owner'):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}', username):
            raise IntegrationError('Usuário inválido: use 3 a 80 letras, números ou . _ @ -')
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise IntegrationError('Use uma senha de 12 a 256 caracteres.')
        if role not in ('owner', 'editor', 'viewer'):
            raise IntegrationError('Perfil inválido.')
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM account_users WHERE username=?', (username,)).fetchone():
                raise IntegrationError('Usuário já cadastrado.')
            db.execute('INSERT INTO account_users VALUES (?,?,?,?,?)', (username, tenant, role, digest, salt))

    def login(self, username, password):
        with self.store.connect() as db:
            row = db.execute('SELECT tenant,role,password_hash,salt FROM account_users WHERE username=?', (username,)).fetchone()
        # Same expensive hash for unknown users.
        salt = row[3] if row else '0' * 32
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        if not row or not hmac.compare_digest(digest, row[2]):
            return None
        token = 'u.' + secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute('DELETE FROM account_sessions WHERE expires_at < ?', (int(time.time()),))
            db.execute('INSERT INTO account_sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), username, int(time.time()) + 86400))
        return token

    def identity(self, token):
        with self.store.connect() as db:
            row = db.execute('''SELECT u.username,u.tenant,u.role FROM account_sessions s
                JOIN account_users u ON u.username=s.username WHERE s.token_hash=? AND s.expires_at>?''',
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        return dict(zip(('user', 'tenant', 'role'), row)) if row else None

    def logout(self, token):
        with self.store.connect() as db:
            db.execute('DELETE FROM account_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def get(self, tenant, provider):
        with self.store.connect() as db:
            row = db.execute('SELECT encrypted FROM tenant_connections WHERE tenant=? AND provider=?', (tenant, provider)).fetchone()
        return self.decrypt(row[0]) if row else None

    def list(self, tenant):
        result = []
        with self.store.connect() as db:
            for provider in PROVIDERS:
                row = db.execute('SELECT encrypted,status,checked_at,message FROM tenant_connections WHERE tenant=? AND provider=?', (tenant, provider['id'])).fetchone()
                result.append({**provider, 'config': public_config(provider['id'], self.decrypt(row[0])) if row else {},
                               'status': row[1] if row else 'not_configured', 'checkedAt': row[2] if row else None,
                               'message': row[3] if row else ''})
        return result

    @contextmanager
    def lock(self, tenant, provider):
        name = hashlib.sha256((tenant + ':' + provider).encode()).hexdigest()
        with (self.store.path.parent / ('connection-' + name + '.lock')).open('a') as file:
            Path(file.name).chmod(0o600)
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def write(self, tenant, provider, data, status='configured'):
        with self.store.connect() as db:
            db.execute('''INSERT INTO tenant_connections (tenant,provider,encrypted,status) VALUES (?,?,?,?)
                ON CONFLICT(tenant,provider) DO UPDATE SET encrypted=excluded.encrypted,status=excluded.status,checked_at=NULL,message='' ''',
                (tenant, provider, self.encrypt(data), status))

    def save(self, identity, provider, body):
        from universal_import import validate_url
        if provider not in ('generic', 'mercadolivre'):
            raise IntegrationError('Provedor não suportado.')
        with self.lock(identity['tenant'], provider):
            previous = self.get(identity['tenant'], provider) or {}
            if provider == 'mercadolivre':
                data = {k: body.get(k, previous.get(k, '')) for k in ('clientId', 'redirectUri', 'scopes')}
                if any(not isinstance(v, str) or len(v) > 2048 for v in data.values()):
                    raise IntegrationError('Configuração OAuth inválida.')
                data['clientSecret'] = body.get('clientSecret') or previous.get('clientSecret', '')
                data['pkce'] = body.get('pkce', previous.get('pkce', False)) is True
                if not data['clientId'] or not isinstance(data['clientSecret'], str) or not data['clientSecret'] or len(data['clientSecret']) > 2048:
                    raise IntegrationError('Informe Client ID e Client Secret.')
                validate_url(data['redirectUri'])  # Exact URI is also validated by OAuth provider.
                # Editing app credentials invalidates existing grants.
                if all(data.get(k) == previous.get(k) for k in ('clientId', 'clientSecret', 'redirectUri', 'pkce', 'scopes')):
                    data.update({k: previous[k] for k in ('access_token', 'refresh_token', 'expires_at') if k in previous})
            else:
                data = {k: body.get(k, previous.get(k, '')) for k in ('region', 'environment')}
                if any(not isinstance(v, str) or len(v) > 80 for v in data.values()):
                    raise IntegrationError('Região ou ambiente inválido.')
                data['allowedHosts'] = host_list(body.get('allowedHosts'))
                data['mediaHosts'] = host_list(body.get('mediaHosts') or data['allowedHosts'])
                data['apiKey'] = body.get('apiKey') or previous.get('apiKey', '')
                if not isinstance(data['apiKey'], str) or len(data['apiKey']) > 2048 or any(c in data['apiKey'] for c in '\r\n'):
                    raise IntegrationError('API key inválida.')
                headers = body.get('headers') or {}
                if not isinstance(headers, dict) or set(headers) - {'Accept-Language', 'X-Region'} or any(not isinstance(v, str) or len(v) > 120 or '\r' in v or '\n' in v for v in headers.values()):
                    raise IntegrationError('Cabeçalhos permitidos: Accept-Language e X-Region.')
                data['headers'] = headers
                selectors = body.get('selectors') or {}
                from universal_import import valid_selector
                if not isinstance(selectors, dict) or set(selectors) - {'name', 'price', 'currency', 'description', 'images'} or any(not valid_selector(v) for v in selectors.values()):
                    raise IntegrationError('Use seletores simples: tag, #id, .classe ou [itemprop="campo"].')
                data['selectors'] = selectors
                if body.get('authorizationConfirmed') is not True:
                    raise IntegrationError('Confirme autorização para leitura automatizada.')
                data['authorizationConfirmed'] = True
            data['revision'] = secrets.token_hex(16)
            self.write(identity['tenant'], provider, data)
            self.audit(identity, provider, 'save')
        return {'ok': True}

    def revoke(self, identity, provider):
        with self.lock(identity['tenant'], provider), self.store.connect() as db:
            db.execute('DELETE FROM tenant_connections WHERE tenant=? AND provider=?', (identity['tenant'], provider))
            db.execute('DELETE FROM tenant_oauth WHERE tenant=?', (identity['tenant'],))
            self.audit(identity, provider, 'revoke_local', db)
        return {'ok': True, 'message': 'Credenciais removidas do Nexo. Revogue também a autorização no provedor.'}

    def access_token(self, identity, failed_token=None):
        with self.lock(identity['tenant'], 'mercadolivre'):
            data = self.get(identity['tenant'], 'mercadolivre') or {}
            if not data.get('access_token'):
                raise MercadoLivreError('Conecte sua conta na Central de Integrações.')
            if data.get('expires_at', 0) <= time.time() + 60 or (failed_token and data['access_token'] == failed_token):
                if not data.get('refresh_token'):
                    raise MercadoLivreError('Reconecte a conta para renovar a autorização.')
                token = refresh_token(data['clientId'], data['clientSecret'], data['refresh_token'])
                self.save_grant(identity, data, token)
                self.audit(identity, 'mercadolivre', 'refresh')
                return token['access_token']
            return data['access_token']

    def save_grant(self, identity, config, token):
        if not isinstance(token.get('access_token'), str) or not token['access_token'] or not isinstance(token.get('expires_in'), (int, float)) or isinstance(token['expires_in'], bool) or not math.isfinite(token['expires_in']) or token['expires_in'] <= 0:
            raise MercadoLivreError('Resposta OAuth incompleta. Reconecte a conta.')
        config.update(access_token=token['access_token'], refresh_token=token.get('refresh_token', ''), expires_at=int(time.time() + token['expires_in']))
        self.write(identity['tenant'], 'mercadolivre', config, 'connected')

    def oauth_start(self, identity):
        config = self.get(identity['tenant'], 'mercadolivre')
        if not config:
            raise IntegrationError('Salve as credenciais antes de autorizar.')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with self.store.connect() as db:
            db.execute('DELETE FROM tenant_oauth WHERE expires_at < ?', (int(time.time()),))
            db.execute('INSERT INTO tenant_oauth VALUES (?,?,?,?,?)', (hashlib.sha256(state.encode()).hexdigest(), identity['tenant'], identity['user'], self.encrypt({'verifier': verifier, 'config': config}), int(time.time()) + 600))
        query = dict(response_type='code', client_id=config['clientId'], redirect_uri=config['redirectUri'], state=state)
        if config.get('scopes'):
            query['scope'] = config['scopes']
        if config.get('pkce'):
            query.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(), code_challenge_method='S256')
        return 'https://auth.mercadolivre.com.br/authorization?' + urllib.parse.urlencode(query)

    def oauth_finish(self, state, code, identity):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT tenant,actor,encrypted,expires_at FROM tenant_oauth WHERE state_hash=?', (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
            if not row:
                raise IntegrationError('Autorização expirada ou já utilizada.')
            if row[0] != identity['tenant'] or row[1] != identity['user']:
                raise IntegrationError('Retorne usando a mesma conta que iniciou a autorização.')
            db.execute('DELETE FROM tenant_oauth WHERE state_hash=?', (hashlib.sha256(state.encode()).hexdigest(),))
        if row[3] < time.time() or not code or len(code) > 4096:
            raise IntegrationError('Autorização expirada ou recusada.')
        saved = self.decrypt(row[2])
        config = saved['config']
        with self.lock(identity['tenant'], 'mercadolivre'):
            current = self.get(identity['tenant'], 'mercadolivre')
            if not current or any(current.get(k) != config.get(k) for k in ('clientId', 'clientSecret', 'redirectUri', 'pkce', 'scopes')):
                raise IntegrationError('Conexão alterada ou revogada. Autorize novamente.')
            form = dict(grant_type='authorization_code', client_id=config['clientId'], client_secret=config['clientSecret'], redirect_uri=config['redirectUri'], code=code)
            if config.get('pkce'):
                form['code_verifier'] = saved['verifier']
            token = request_json('https://api.mercadolibre.com/oauth/token', form=form)
            self.save_grant(identity, config, token)
            self.audit(identity, 'mercadolivre', 'authorize')

    def test(self, identity, provider, url=''):
        from universal_import import safe_fetch, validate_url, GenericAdapter
        config = self.get(identity['tenant'], provider)
        if not config:
            raise IntegrationError('Conexão não configurada.')
        try:
            if provider == 'mercadolivre':
                request_json('https://api.mercadolibre.com/users/me', self.access_token(identity))
            elif provider == 'generic':
                validate_url(url, config['allowedHosts'])
                page = safe_fetch(url, config)
                GenericAdapter().extract(url, config=config, html=page)
            else:
                raise IntegrationError('Provedor não suportado.')
            status, message = 'verified', 'Conexão verificada com resposta real HTTP 200.'
        except (ValueError, MercadoLivreError) as error:
            status, message = 'error', str(error)
        with self.store.connect() as db:
            db.execute('UPDATE tenant_connections SET status=?,checked_at=?,message=? WHERE tenant=? AND provider=?', (status, int(time.time()), message, identity['tenant'], provider))
        self.audit(identity, provider, 'test_' + status)
        return {'ok': status == 'verified', 'message': message, 'status': status}


class TenantRepository:
    """Legacy admin data remains accessible; new accounts use a separate composite key."""
    def __init__(self, store, tenant):
        self.store, self.tenant = store, tenant

    def save(self, kind, key, value):
        if self.tenant == 'admin':
            return getattr(self.store, 'save_' + ('draft' if kind == 'draft' else 'product'))(value)
        with self.store.connect() as db:
            db.execute('''INSERT INTO tenant_records VALUES (?,?,?,?,?) ON CONFLICT(tenant,kind,key)
                DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at''', (self.tenant, kind, key, json.dumps(value), int(time.time())))
            if kind == 'draft':
                db.execute('INSERT INTO tenant_records VALUES (?,?,?,?,?)', (self.tenant, 'history', secrets.token_hex(16), json.dumps({'id': key, 'inputUrl': value.get('inputUrl'), 'fields': value.get('fieldDiagnostics')}), int(time.time())))
                db.execute("DELETE FROM tenant_records WHERE kind='history' AND updated_at < ?", (int(time.time()) - 30 * 86400,))

    def save_product(self, product):
        if self.tenant != 'admin' and not product.get('sourceItemId'):
            source_id = product['id']
            digest = hashlib.sha256((self.tenant + ':' + source_id).encode()).hexdigest()[:16]
            product.update(sourceItemId=source_id, id='SRC' + digest.upper(), slug='p-' + digest)
        self.save('product', product['slug'], product)

    def save_draft(self, product):
        if self.tenant != 'admin':
            source_id = product.get('sourceItemId') or product['id']
            digest = hashlib.sha256((self.tenant + ':' + source_id).encode()).hexdigest()[:16]
            product.update(sourceItemId=source_id, id='SRC' + digest.upper(), slug='p-' + digest)
        self.save('draft', product['id'], product)

    def get(self, kind, key):
        if self.tenant == 'admin':
            return getattr(self.store, 'get_' + kind)(key)
        with self.store.connect() as db:
            row = db.execute('SELECT data_json FROM tenant_records WHERE tenant=? AND kind=? AND key=?', (self.tenant, kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def get_product(self, slug):
        return self.get('product', slug)

    def get_draft(self, item_id):
        return self.get('draft', item_id)

    def list_products(self):
        if self.tenant == 'admin':
            return self.store.list_products()
        with self.store.connect() as db:
            rows = db.execute("SELECT data_json FROM tenant_records WHERE tenant=? AND kind='product' ORDER BY updated_at DESC LIMIT 100", (self.tenant,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def cached_import(self, url):
        if self.tenant == 'admin':
            return self.store.cached_import(url)
        with self.store.connect() as db:
            row = db.execute("SELECT data_json FROM tenant_records WHERE tenant=? AND kind='draft' AND json_extract(data_json,'$.inputUrl')=? ORDER BY updated_at DESC LIMIT 1", (self.tenant, url)).fetchone()
        return json.loads(row[0]) if row else None

    def own_recommendations(self, slugs):
        return [p for slug in slugs if (p := self.get_product(slug)) and p.get('catalogOwner') == 'own_catalog']


def public_product(store, slug):
    product = store.get_product(slug)
    if product:
        return product, TenantRepository(store, 'admin')
    with store.connect() as db:
        row = db.execute("SELECT tenant,data_json FROM tenant_records WHERE kind='product' AND key=?", (slug,)).fetchone()
    return (json.loads(row[1]), TenantRepository(store, row[0])) if row else (None, None)
