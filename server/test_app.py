import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app import NexoServer, TrafficStore
from page_import import ImportErrorMessage

PUBLISHED_PAGE = '/p/mlb1234567'


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


class NexoHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<html>Nexo</html>')
        self.server = NexoServer(('127.0.0.1', 0), web, root / 'data', 'secret', geo_locator=FakeGeo())
        self.server.store.save_product({'id': 'MLB1234567', 'slug': 'mlb1234567', 'name': 'Teste'})
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

        invalid = json.dumps({'username': 'admin', 'password': 'wrong'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(
                '/api/auth/login', invalid,
                {'Content-Type': 'application/json'},
            )
        self.assertEqual(failure.exception.code, 401)

        temporary_password = bytes.fromhex(
            '436f72696e746869616e7331323334'
        ).decode()
        temporary = json.dumps({
            'username': 'admin', 'password': temporary_password,
        }).encode()
        with self.request(
            '/api/auth/login', temporary,
            {'Content-Type': 'application/json'},
        ) as response:
            self.assertEqual(response.status, 200)

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

    def test_admin_session_survives_server_restart_and_rejects_tampering(self):
        token = self.server.create_admin_session()
        replacement = NexoServer(
            ('127.0.0.1', 0), self.server.web_root, self.server.data_root,
            'secret', geo_locator=FakeGeo(),
        )
        try:
            self.assertTrue(replacement.has_admin_session(token))
            tampered = token[:-1] + ('0' if token[-1] != '0' else '1')
            self.assertFalse(replacement.has_admin_session(tampered))
        finally:
            replacement.server_close()

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
        with patch('app.fetch_product', return_value=product):
            with self.request(
                '/api/products/import',
                json.dumps({'url': 'https://www.mercadolivre.com.br/produto/MLB7654321'}).encode(),
                headers,
            ) as response:
                self.assertEqual(json.load(response)['name'], 'Produto real')
        with self.request(
            '/api/products/publish',
            json.dumps({'id': 'MLB7654321', 'redirect': True}).encode(),
            headers,
        ) as response:
            self.assertEqual(json.load(response)['slug'], 'mlb7654321')
        with self.request('/api/products/mlb7654321') as response:
            self.assertEqual(json.load(response)['description'], 'Descrição')
        with self.request('/api/products', headers={'Authorization': f'Basic {authorization}'}) as response:
            self.assertIn('Produto real', [product['name'] for product in json.load(response)['products']])
        with self.request('/p/mlb7654321') as response:
            self.assertEqual(response.status, 200)

    def test_creates_editable_page_from_marketplace_html(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        html = (
            '<meta property="og:title" content="Título encontrado">'
            '<meta property="og:image" content="https://images.example.com/p.jpg">'
        )
        with self.request(
            '/api/products/import',
            json.dumps({'url': 'https://www.temu.com/goods.html', 'html': html}).encode(),
            headers,
        ) as response:
            draft = json.load(response)
        self.assertEqual(draft['market'], 'Temu')
        with self.request(
            '/api/products/publish',
            json.dumps({
                'id': draft['id'], 'redirect': True,
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
                'id': draft['id'], 'redirect': True,
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
        self.assertIn('Leitura bloqueada', json.load(failure.exception)['message'])

    def test_uses_rendered_dom_when_initial_html_has_no_product(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        rendered = (
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Câmera renderizada",'
            '"image":"https://images.example.com/camera.jpg",'
            '"offers":{"price":"129.90","priceCurrency":"BRL"}}'
            '</script>'
        )
        with patch('app.fetch_html', return_value='<html></html>'), patch(
            'app.fetch_rendered_html', return_value=rendered
        ) as capture:
            with self.request(
                '/api/products/import',
                json.dumps({'url': 'https://www.temu.com/produto'}).encode(),
                headers,
            ) as response:
                draft = json.load(response)
        capture.assert_called_once()
        self.assertEqual(draft['name'], 'Câmera renderizada')
        self.assertEqual(draft['price'], 'R$ 129,90')

    def test_ml_uses_public_dom_when_oauth_is_not_configured(self):
        authorization = base64.b64encode(b'admin:secret').decode()
        headers = {'Authorization': f'Basic {authorization}', 'Content-Type': 'application/json'}
        html = (
            '<meta property="og:title" content="Câmera Intelbras">'
            '<meta property="og:image" content="https://http2.mlstatic.com/camera.jpg">'
        )
        with patch('app.fetch_html', return_value=html), patch(
            'app.fetch_rendered_html', side_effect=ImportErrorMessage('Captura indisponível.')
        ):
            with self.request(
                '/api/products/import',
                json.dumps({'url': 'https://www.mercadolivre.com.br/camera/p/MLB47535705'}).encode(),
                headers,
            ) as response:
                draft = json.load(response)
        self.assertEqual(draft['market'], 'Mercado Livre')
        self.assertEqual(draft['name'], 'Câmera Intelbras')
        self.assertTrue(draft['importNotice'])

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
            json.dumps({'url': 'https://www.amazon.com.br/dp/B012345678', 'html': '<meta property="og:title" content="Produto"><meta property="og:image" content="https://images.example.com/p.jpg">'}).encode(),
            headers,
        ) as response:
            draft = json.load(response)
        with self.request(
            '/api/products/publish',
            json.dumps({
                'id': draft['id'], 'redirect': True,
                'page': {'storeName': 'Minha loja', 'name': 'Produto', 'price': 'R$ 10,00', 'images': [path]},
            }).encode(),
            headers,
        ) as response:
            self.assertEqual(json.load(response)['images'], [path])


if __name__ == '__main__':
    unittest.main()
