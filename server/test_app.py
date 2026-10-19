import base64
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app import NexoServer, TrafficStore, admin_password
from page_import import ImportErrorMessage
from payments import PixzyConfig

PUBLISHED_PAGE = '/p/mlb1234567'


class AdminPasswordTest(unittest.TestCase):
    def test_generated_password_is_persistent_private_and_not_logged(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch('builtins.print') as output:
            root = Path(directory)
            generated = admin_password(root)
            self.assertEqual(admin_password(root), generated)
            self.assertEqual((root / 'admin_password').stat().st_mode & 0o777, 0o600)
            self.assertNotIn(generated, str(output.call_args_list))
            with patch.dict(os.environ, {'NEXO_ADMIN_PASSWORD': 'replacement-fixture'}):
                self.assertEqual(admin_password(root), 'replacement-fixture')


class FakeGeo:
    ready = True
    path = Path('/unused')

    def locate(self, _address):
        return {
            'country': 'BR',
            'state': 'SP',
            'city': 'São Paulo',
            'lat': -23.55,
            'lon': -46.63,
        }


class FakePaymentGateway:
    def __init__(self):
        self.created = []
        self.transactions = {}

    def create_payment(self, payload):
        self.created.append(payload)
        transaction_id = f'tx-{len(self.created)}'
        transaction = {
            'transaction_id': transaction_id,
            'id': transaction_id,
            'amount': payload['amount'],
            'status': 'pending',
            'br_code': '000201-pix-code',
            'metadata': payload['metadata'],
        }
        self.transactions[transaction_id] = transaction
        return transaction

    def get_payment(self, transaction_id):
        return self.transactions[transaction_id]

    def get_account(self):
        return {'id': 'account-1', 'balance': 10000}


class TrafficStoreTest(unittest.TestCase):
    def test_counts_unique_active_sessions_and_expires_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrafficStore(Path(directory) / 'traffic.sqlite3')
            location = FakeGeo().locate('127.0.0.1')
            store.record('a', PUBLISHED_PAGE, 'view', location, now=1000)
            store.record('a', PUBLISHED_PAGE, 'heartbeat', location, now=1010)
            store.record('b', PUBLISHED_PAGE, 'view', location, now=1010)
            snapshot = store.live(now=1010)
            self.assertEqual(snapshot['activeVisitors'], 2)
            self.assertEqual(snapshot['cities'][0]['visitors'], 2)
            self.assertEqual(snapshot['views24h'], 2)
            self.assertEqual(len(snapshot['hourlyViews']), 24)
            self.assertEqual(store.live(now=1400)['activeVisitors'], 0)

    def test_saves_temporary_mercado_livre_token_without_refresh_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrafficStore(Path(directory) / 'traffic.sqlite3')
            store.save_token({'access_token': 'temporary', 'expires_in': 21600})
            stored = store.get_token()
            self.assertEqual(stored['access_token'], 'temporary')
            self.assertEqual(stored['refresh_token'], '')


class NexoHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<html>Nexo</html>')
        (web / 'login.html').write_text(
            (Path(__file__).resolve().parents[1] / 'web' / 'login.html').read_text()
        )
        self.server = NexoServer(('127.0.0.1', 0), web, root / 'data', 'secret', geo_locator=FakeGeo())
        self.server.store.save_product({'id': 'MLB1234567', 'slug': 'mlb1234567', 'name': 'Teste', 'rightsConfirmed': True})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        return urllib.request.urlopen(request)

    def configure_payments(self):
        gateway = FakePaymentGateway()
        self.server.pixzy_config = PixzyConfig(
            'server-only-token', webhook_url=self.url + '/api/webhooks/pixzypay/transactions',
        )
        self.server.payment_gateway = gateway
        self.server.store.save_product({
            'id': 'MLB1234567', 'slug': 'mlb1234567', 'name': 'Produto teste',
            'price': 'R$ 19,90', 'stock': 5, 'redirect': False, 'rightsConfirmed': True,
        })
        return gateway

    def create_payment(self, client_key='checkout_key_123456'):
        payload = json.dumps({
            'quantity': 2,
            'idempotencyKey': client_key,
            'customer': {
                'name': 'Cliente Teste', 'email': 'cliente@example.com',
                'document': '123.456.789-00', 'phone': '(11) 99999-9999',
            },
        }).encode()
        with self.request(
            '/api/products/mlb1234567/checkout', payload, {'Content-Type': 'application/json'},
        ) as response:
            return response.status, json.load(response)

    def test_pixzy_checkout_uses_server_price_and_is_idempotent(self):
        gateway = self.configure_payments()
        status, payment = self.create_payment()
        self.assertEqual(status, 201)
        self.assertEqual(payment['amount'], 3980)
        self.assertEqual(payment['status'], 'pending')
        self.assertEqual(payment['brCode'], '000201-pix-code')
        _, duplicate = self.create_payment()
        self.assertEqual(duplicate['id'], payment['id'])
        self.assertEqual(len(gateway.created), 1)
        self.assertEqual(gateway.created[0]['amount'], 3980)
        self.assertEqual(gateway.created[0]['items'][0]['price'], 1990)
        self.assertNotIn('server-only-token', json.dumps(payment))

    def test_reconciliation_and_idempotent_paid_webhook(self):
        gateway = self.configure_payments()
        _, created = self.create_payment()
        stored = self.server.store.get_payment(created['id'])
        transaction_id = stored['provider_transaction_id']
        gateway.transactions[transaction_id]['status'] = 'paid'

        with self.request(
            f"/api/payments/{created['id']}/reconcile", b'{}', {'Content-Type': 'application/json'},
        ) as response:
            self.assertEqual(json.load(response)['status'], 'paid')

        webhook = json.dumps({
            'event': 'paid',
            'transaction': {
                'id': transaction_id, 'amount': 3980, 'status': 'paid',
                'metadata': {'order_id': created['orderId']}, 'items': [],
            },
        }).encode()
        with self.request(
            '/api/webhooks/pixzypay/transactions', webhook, {'Content-Type': 'application/json'},
        ) as response:
            self.assertTrue(json.load(response)['processed'])
        with self.request(
            '/api/webhooks/pixzypay/transactions', webhook, {'Content-Type': 'application/json'},
        ) as response:
            self.assertFalse(json.load(response)['processed'])
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM payment_webhook_events').fetchone()[0], 1)

    def test_webhook_rejects_amount_mismatch(self):
        gateway = self.configure_payments()
        _, created = self.create_payment()
        stored = self.server.store.get_payment(created['id'])
        transaction_id = stored['provider_transaction_id']
        gateway.transactions[transaction_id]['status'] = 'paid'
        webhook = json.dumps({
            'event': 'paid',
            'transaction': {
                'id': transaction_id, 'amount': 1, 'status': 'paid',
                'metadata': {'order_id': created['orderId']},
            },
        }).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/webhooks/pixzypay/transactions', webhook, {'Content-Type': 'application/json'})
        self.assertEqual(failure.exception.code, 400)

    def test_webhook_applies_expired_and_failed_statuses(self):
        gateway = self.configure_payments()
        for index, status in enumerate(('expired', 'failed')):
            _, created = self.create_payment(f'checkout_terminal_{index:02d}')
            stored = self.server.store.get_payment(created['id'])
            transaction_id = stored['provider_transaction_id']
            gateway.transactions[transaction_id]['status'] = status
            webhook = json.dumps({
                'event': status,
                'transaction': {
                    'id': transaction_id, 'amount': 3980, 'status': status,
                    'metadata': {'order_id': created['orderId']},
                },
            }).encode()
            with self.request(
                '/api/webhooks/pixzypay/transactions', webhook, {'Content-Type': 'application/json'},
            ) as response:
                self.assertTrue(json.load(response)['processed'])
            with self.request(f"/api/payments/{created['id']}") as response:
                self.assertEqual(json.load(response)['status'], status)

    def test_webhook_rejects_unknown_transaction(self):
        self.configure_payments()
        webhook = json.dumps({
            'event': 'paid',
            'transaction': {
                'id': 'unknown', 'amount': 3980, 'status': 'paid',
                'metadata': {'order_id': 'unknown'},
            },
        }).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/webhooks/pixzypay/transactions', webhook, {'Content-Type': 'application/json'})
        self.assertEqual(failure.exception.code, 404)

    def test_payment_configuration_is_admin_only(self):
        self.configure_payments()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/payments/config')
        self.assertEqual(failure.exception.code, 401)
        authorization = base64.b64encode(b'admin:secret').decode()
        with self.request(
            '/api/payments/config', headers={'Authorization': f'Basic {authorization}'},
        ) as response:
            config = json.load(response)
        self.assertTrue(config['configured'])
        self.assertNotIn('server-only-token', json.dumps(config))

    def test_admin_can_save_pixzy_token_without_exposing_it(self):
        self.server.pixzy_managed_by_environment = False
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {
            'Authorization': f'Basic {authorization}',
            'Content-Type': 'application/json',
        }
        payload = json.dumps({
            'apiToken': 'new-server-secret-token',
            'baseUrl': 'https://app.pixzypay.com/api',
            'webhookUrl': self.url + '/api/webhooks/pixzypay/transactions',
        }).encode()
        with self.request('/api/payments/config', payload, headers) as response:
            saved = json.load(response)
        self.assertTrue(saved['configured'])
        self.assertNotIn('new-server-secret-token', json.dumps(saved))
        stored = self.server.store.get_payment_config()
        self.assertEqual(stored['api_token'], 'new-server-secret-token')

        with self.request(
            '/api/payments/config', headers={'Authorization': f'Basic {authorization}'},
        ) as response:
            visible = json.load(response)
        self.assertNotIn('new-server-secret-token', json.dumps(visible))

    def test_payment_config_rejects_insecure_remote_urls(self):
        self.server.pixzy_managed_by_environment = False
        authorization = base64.b64encode(b'admin:secret').decode()
        payload = json.dumps({
            'apiToken': 'new-server-secret-token',
            'baseUrl': 'http://evil.example/api',
            'webhookUrl': 'https://shop.example/webhook',
        }).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(
                '/api/payments/config', payload,
                {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'},
            )
        self.assertEqual(failure.exception.code, 400)

    def test_public_page_tracks_visits_and_admin_api_is_private(self):
        with self.request(PUBLISHED_PAGE) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/traffic/live')
        self.assertEqual(failure.exception.code, 401)

        payload = json.dumps({'page': PUBLISHED_PAGE, 'kind': 'view'}).encode()
        with self.request('/api/traffic/event', payload, {'Content-Type': 'application/json'}) as response:
            self.assertEqual(response.status, 204)
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        with self.request('/api/traffic/event', payload, {'Content-Type': 'application/json', 'Cookie': cookie}):
            pass
        authorization = base64.b64encode(b'admin:secret').decode()
        with self.request('/api/traffic/live', headers={'Authorization': f'Basic {authorization}'}) as response:
            snapshot = json.load(response)
        self.assertEqual(snapshot['activeVisitors'], 1)
        self.assertEqual(snapshot['cities'][0]['city'], 'São Paulo')

    def test_custom_login_creates_and_removes_admin_session(self):
        with self.request('/') as response:
            self.assertEqual(response.status, 200)
            login_page = response.read().decode()
        self.assertIn('autocomplete="username"', login_page)
        self.assertIn('autocomplete="current-password"', login_page)

        invalid = json.dumps({'username': 'admin', 'password': 'wrong'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(
                '/api/auth/login', invalid,
                {'Content-Type': 'application/json'},
            )
        self.assertEqual(failure.exception.code, 401)

        credentials = json.dumps({'username': 'admin', 'password': 'secret'}).encode()
        with self.request(
            '/api/auth/login', credentials,
            {'Content-Type': 'application/json'},
        ) as response:
            self.assertEqual(response.status, 200)
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
            self.assertTrue(json.load(response)['authenticated'])

        with self.request('/api/auth/session', headers={'Cookie': cookie}) as response:
            self.assertEqual(json.load(response)['user'], 'admin')

        with self.request(
            '/api/auth/logout', b'{}',
            {'Content-Type': 'application/json', 'Cookie': cookie},
        ) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/auth/session', headers={'Cookie': cookie})
        self.assertEqual(failure.exception.code, 401)

    def test_native_login_form_sets_persistent_cookie(self):
        connection = http.client.HTTPConnection(
            '127.0.0.1', self.server.server_address[1], timeout=5,
        )
        try:
            body = urllib.parse.urlencode({
                'username': 'admin', 'password': 'secret',
            })
            connection.request(
                'POST', '/login', body,
                {'Content-Type': 'application/x-www-form-urlencoded'},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 303)
            self.assertEqual(response.getheader('Location'), '/')
            cookie = response.getheader('Set-Cookie')
            self.assertIn('Max-Age=2592000', cookie)
            self.assertIn('HttpOnly', cookie)
        finally:
            connection.close()

    def test_native_login_failure_renders_form_and_escapes_username(self):
        username = '\"><script>alert(1)</script>'
        body = urllib.parse.urlencode({'username': username, 'password': 'private-wrong-password'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/login', body, {'Content-Type': 'application/x-www-form-urlencoded'})
        response = failure.exception
        self.assertEqual(response.code, 401)
        self.assertIn('text/html', response.headers['Content-Type'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIsNone(response.headers.get('Set-Cookie'))
        page = response.read().decode()
        self.assertIn('action="/login"', page)
        self.assertIn('Usuário ou senha incorretos.', page)
        self.assertIn('role="alert"', page)
        self.assertIn('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn(username, page)
        self.assertNotIn('private-wrong-password', page)

    def test_native_login_rate_limit_renders_distinct_message(self):
        body = urllib.parse.urlencode({'username': 'admin', 'password': 'wrong'}).encode()
        with patch('app.NexoHandler.allow_rate', return_value=False):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request('/login', body, {'Content-Type': 'application/x-www-form-urlencoded'})
        response = failure.exception
        self.assertEqual(response.code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        page = response.read().decode()
        self.assertIn('Aguarde um minuto', page)
        self.assertIn('action="/login"', page)

    def test_login_error_is_rendered_without_javascript(self):
        with self.request('/?login_error=1') as response:
            page = response.read().decode()
        self.assertIn('id="login-error" class="error" role="alert"', page)
        self.assertIn('Usuário ou senha incorretos.', page)
        with self.request('/login') as response:
            initial = response.read().decode()
        self.assertNotIn('id="login-error"', initial)
        self.assertNotIn('__NEXO_LOGIN_USERNAME__', initial)

    def test_admin_login_accepts_unicode_credentials(self):
        self.server.admin_user = 'administração'
        self.server.admin_password = 'senha-com-acentuação'
        body = json.dumps({'username': self.server.admin_user, 'password': self.server.admin_password}).encode()
        with self.request('/api/auth/login', body, {'Content-Type': 'application/json'}) as response:
            self.assertTrue(json.load(response)['authenticated'])

    def test_admin_session_survives_server_restart_and_rejects_tampering(self):
        token = self.server.create_admin_session()
        oauth_state = self.server.create_ml_oauth_state()
        replacement = NexoServer(
            ('127.0.0.1', 0), self.server.web_root, self.server.data_root,
            'secret', geo_locator=FakeGeo(),
        )
        try:
            self.assertTrue(replacement.has_admin_session(token))
            tampered = token[:-1] + ('0' if token[-1] != '0' else '1')
            self.assertFalse(replacement.has_admin_session(tampered))
            self.assertTrue(replacement.valid_ml_oauth_state(oauth_state))
            oauth_tampered = oauth_state[:-1] + ('0' if oauth_state[-1] != '0' else '1')
            self.assertFalse(replacement.valid_ml_oauth_state(oauth_tampered))
        finally:
            replacement.server_close()

    def test_oauth_callback_explains_denied_authorization(self):
        state = self.server.create_ml_oauth_state()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(
                '/api/mercadolivre/callback?'
                + urllib.parse.urlencode({'state': state, 'error': 'access_denied'})
            )
        self.assertEqual(failure.exception.code, 400)
        self.assertIn('Não foi possível conectar', failure.exception.read().decode())

    def test_oauth_callback_accepts_access_token_without_refresh_token(self):
        state = self.server.create_ml_oauth_state()
        token = {'access_token': 'temporary', 'expires_in': 21600}
        with patch.dict(os.environ, {
            'ML_CLIENT_ID': 'client',
            'ML_CLIENT_SECRET': 'secret',
            'ML_REDIRECT_URI': self.url + '/api/mercadolivre/callback',
        }), patch('app.exchange_code', return_value=token):
            with self.request(
                '/api/mercadolivre/callback?'
                + urllib.parse.urlencode({'state': state, 'code': 'authorization-code'})
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('Conta conectada', response.read().decode())
        self.assertEqual(self.server.store.get_token()['refresh_token'], '')

    def test_rejects_other_origin_and_other_pages(self):
        payload = json.dumps({'page': PUBLISHED_PAGE, 'kind': 'view'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/traffic/event', payload, {'Content-Type': 'application/json', 'Origin': 'https://evil.example'})
        self.assertEqual(failure.exception.code, 403)
        wrong = json.dumps({'page': '/admin', 'kind': 'view'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/traffic/event', wrong, {'Content-Type': 'application/json'})
        self.assertEqual(failure.exception.code, 400)

    def test_accepts_mercado_livre_notifications_for_configured_app(self):
        payload = json.dumps({
            'application_id': 123456,
            'resource': '/items/MLB1234567',
            'topic': 'items',
        }).encode()
        with patch.dict(os.environ, {'ML_CLIENT_ID': '123456'}):
            with self.request(
                '/api/mercadolivre/notify', payload,
                {'Content-Type': 'application/json'},
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {'ok': True})

            invalid = json.dumps({'application_id': 999999}).encode()
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request(
                    '/api/mercadolivre/notify', invalid,
                    {'Content-Type': 'application/json'},
                )
            self.assertEqual(failure.exception.code, 403)

    def test_import_publish_and_serve_product_from_own_server(self):
        self.server.ml_access_token = lambda: 'token'
        self.server.cache_images = lambda product: None
        product = {
            'id': 'MLB7654321', 'slug': 'mlb7654321', 'name': 'Produto real',
            'price': 'R$ 10,00', 'market': 'Mercado Livre',
            'sourceUrl': 'https://produto.mercadolivre.com.br/MLB-7654321',
            'images': ['https://http2.mlstatic.com/a.jpg'], 'reviews': [],
            'description': 'Descrição', 'rating': '0', 'reviewCount': 0,
            'stock': 1, 'redirect': True,
        }
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        with patch('universal_import.fetch_product', return_value=product):
            with self.request(
                '/api/products/import',
                json.dumps({'url': 'https://www.mercadolivre.com.br/produto/MLB7654321'}).encode(),
                headers,
            ) as response:
                self.assertEqual(json.load(response)['name'], 'Produto real')
        with self.request(
            '/api/products/publish',
            json.dumps({'id': 'MLB7654321', 'redirect': True, 'rightsConfirmed': True}).encode(),
            headers,
        ) as response:
            self.assertEqual(json.load(response)['slug'], 'mlb7654321')
        with self.request('/api/products/mlb7654321') as response:
            self.assertEqual(json.load(response)['description'], 'Descrição')
        with self.request('/api/products', headers={'Authorization': f'Basic {authorization}'}) as response:
            self.assertIn('Produto real', [product['name'] for product in json.load(response)['products']])
        with self.request('/p/mlb7654321') as response:
            self.assertEqual(response.status, 200)

    def test_mlbu_ad_link_prefers_selected_mlb_listing(self):
        self.server.ml_access_token = lambda: 'token'
        self.server.cache_images = lambda product: None
        product = {
            'id': 'MLB4831773371', 'slug': 'mlb4831773371', 'name': 'Capa Samsung',
            'price': 'R$ 19,90', 'market': 'Mercado Livre',
            'sourceUrl': 'https://produto.mercadolivre.com.br/MLB-4831773371',
            'images': ['https://http2.mlstatic.com/capa.jpg'], 'reviews': [],
            'description': 'Descrição', 'rating': '0', 'reviewCount': 0,
            'stock': 1, 'redirect': True,
        }
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        url = (
            'https://www.mercadolivre.com.br/capa/up/MLBU4195394312?'
            'pdp_filters=item_id:MLB4831773371#is_advertising=true'
        )
        with patch('universal_import.fetch_product', return_value=product) as fetch:
            with self.request(
                '/api/products/import', json.dumps({'url': url}).encode(), headers,
            ) as response:
                self.assertEqual(json.load(response)['name'], 'Capa Samsung')
        self.assertEqual(fetch.call_args.args[0], 'MLB4831773371')

    def test_creates_editable_page_from_marketplace_html(self):
        self.server.connections.write('admin', 'generic', {'allowedHosts': ['www.temu.com'], 'mediaHosts': ['images.example.com'], 'authorizationConfirmed': True})
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        html = (
            '<meta property="og:title" content="Título encontrado">'
            '<meta property="og:image" content="https://images.example.com/p.jpg">'
        )
        with self.request(
            '/api/products/import',
            json.dumps({'url': 'https://www.temu.com/goods.html', 'html': html, 'authorizationConfirmed': True}).encode(),
            headers,
        ) as response:
            draft = json.load(response)
        self.assertEqual(draft['market'], 'Temu')
        with self.request(
            '/api/products/publish',
            json.dumps({
                'id': draft['id'], 'redirect': True, 'rightsConfirmed': True,
                'page': {
                    'storeName': 'Minha vitrine',
                    'name': 'Minha página da luminária', 'price': 'R$ 89,90',
                    'description': 'Texto escrito por mim',
                    'images': ['https://images.example.com/p.jpg'], 'reviews': [],
                },
            }).encode(),
            headers,
        ) as response:
            published = json.load(response)
        self.assertEqual(published['name'], 'Minha página da luminária')
        self.assertEqual(published['storeName'], 'Minha vitrine')
        self.assertEqual(published['description'], 'Texto escrito por mim')
        with self.request('/api/products/' + draft['slug']) as response:
            self.assertEqual(json.load(response)['market'], 'Temu')
        with self.request(
            '/api/products/publish',
            json.dumps({
                'id': draft['id'], 'redirect': True, 'rightsConfirmed': True,
                'page': {
                    'storeName': 'Minha vitrine', 'name': 'Título revisado',
                    'price': 'R$ 79,90', 'description': 'Texto atualizado',
                    'images': ['https://images.example.com/p.jpg'], 'reviews': [],
                },
            }).encode(),
            headers,
        ) as response:
            self.assertEqual(json.load(response)['name'], 'Título revisado')
        with self.request('/p/' + draft['slug']) as response:
            self.assertEqual(response.status, 200)
        event = json.dumps({'page': '/p/' + draft['slug'], 'kind': 'view'}).encode()
        with self.request('/api/traffic/event', event, {'Content-Type': 'application/json'}) as response:
            self.assertEqual(response.status, 204)

    def test_blocked_marketplace_reports_error_instead_of_fake_import(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        with patch('app.fetch_html', side_effect=ImportErrorMessage('Leitura bloqueada.')), patch(
            'app.fetch_rendered_html', side_effect=ImportErrorMessage('Leitura bloqueada no navegador.')
        ):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request(
                    '/api/products/import',
                    json.dumps({'url': 'https://shopee.com.br/produto'}).encode(),
                    headers,
                )
        self.assertEqual(failure.exception.code, 400)
        self.assertIn('não autorizado', json.load(failure.exception)['message'])

    def test_authorized_html_uses_native_parser_without_browser(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        rendered = (
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Câmera renderizada",'
            '"image":"https://images.example.com/camera.jpg",'
            '"offers":{"price":"129.90","priceCurrency":"BRL"}}'
            '</script>'
        )
        self.server.connections.write('admin', 'generic', {'allowedHosts': ['www.temu.com'], 'mediaHosts': ['images.example.com'], 'authorizationConfirmed': True})
        with patch('universal_import.safe_fetch', return_value=rendered), patch('app.fetch_rendered_html') as capture:
            with self.request(
                '/api/products/import',
                json.dumps({'url': 'https://www.temu.com/produto'}).encode(),
                headers,
            ) as response:
                draft = json.load(response)
        capture.assert_not_called()
        self.assertEqual(draft['name'], 'Câmera renderizada')
        self.assertEqual(draft['price'], 'R$ 129,90')

    def test_ml_requires_oauth_without_html_or_permalink_fallback(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        for url, supplied in [
            ('https://www.mercadolivre.com.br/camera/p/MLB47535705', None),
            ('https://produto.mercadolivre.com.br/MLB-6068020650-capa-_JM', None),
            ('https://produto.mercadolivre.com.br/MLB-6068020650', '<h1>Fake</h1>'),
        ]:
            with patch('app.fetch_html') as html, patch('app.fetch_rendered_html') as browser:
                body = {'url': url}
                if supplied:
                    body['html'] = supplied
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    self.request('/api/products/import', json.dumps(body).encode(), headers)
                self.assertEqual(failure.exception.code, 400)
                html.assert_not_called()
                browser.assert_not_called()
                failure.exception.close()

    def test_uploaded_photo_is_served_from_own_server(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l5sAAAAASUVORK5CYII='
        )
        with self.request(
            '/api/media/upload', image,
            {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/octet-stream'},
        ) as response:
            path = json.load(response)['url']
        with self.request(path) as response:
            self.assertEqual(response.read(), image)
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        with self.request(
            '/api/products/import',
            json.dumps({'url': 'https://www.amazon.com.br/dp/B012345678', 'authorizationConfirmed': True, 'html': '<meta property="og:title" content="Produto"><meta property="og:image" content="https://images.example.com/p.jpg">'}).encode(),
            headers,
        ) as response:
            draft = json.load(response)
        with self.request(
            '/api/products/publish',
            json.dumps({
                'id': draft['id'], 'redirect': True, 'rightsConfirmed': True,
                'page': {'storeName': 'Minha loja', 'name': 'Produto', 'price': 'R$ 10,00', 'images': [path]},
            }).encode(),
            headers,
        ) as response:
            self.assertEqual(json.load(response)['images'], [path])


if __name__ == '__main__':
    unittest.main()
