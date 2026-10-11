import concurrent.futures
import http.client
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations import IntegrationError, TenantRepository
from universal_import import (GenericAdapter, SourceError, adapter_for, public_addresses,
                              safe_fetch, validate_url, PinnedHTTPS)
from mercadolivre import MercadoLivreError
import test_app

FIXTURE = (Path(__file__).parent / 'fixtures/authorized_product.html').read_text()
CONFIG = {'allowedHosts': ['shop.example'], 'mediaHosts': ['cdn.shop.example'],
          'authorizationConfirmed': True, 'apiKey': 'super-secret-api-key',
          'headers': {}, 'selectors': {}}


class UniversalParserTest(unittest.TestCase):
    def test_jsonld_images_variants_reviews_and_provenance(self):
        product = GenericAdapter().extract('https://shop.example/lamp', CONFIG, FIXTURE)
        self.assertEqual(product['name'], 'Luminária autorizada')
        self.assertEqual(product['price'], 'R$ 89,90')
        self.assertEqual(product['rating'], '4,5')
        self.assertEqual(product['reviewCount'], 2)
        self.assertEqual(product['reviews'][0]['comment'], 'Comentário fornecido pela fixture')
        self.assertEqual(product['variations'], ['Branco', 'Preto'])
        self.assertIn('https://cdn.shop.example/lamp.jpg', product['images'])
        self.assertIn('https://cdn.shop.example/large.jpg', product['images'])
        self.assertEqual(product['fieldDiagnostics']['name']['selector_or_path'], 'jsonld.Product.name')
        self.assertNotIn(CONFIG['apiKey'], json.dumps(product))

    def test_microdata_selectors_malformed_html_and_missing_reviews(self):
        html = '''<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Nativo</h1>
        <p itemprop="description">Descrição microdata</p><meta itemprop="price" content="10.50">
        <meta itemprop="priceCurrency" content="USD"><span id="price">12.50</span>
        <img itemprop="image" data-src="https://cdn.shop.example/a.jpg"><div>HTML sem fechar'''
        product = GenericAdapter().extract('https://shop.example/p', {**CONFIG, 'selectors': {'price': '#price'}}, html)
        self.assertEqual(product['name'], 'Nativo')
        self.assertEqual(product['price'], 'US$ 12,50')
        self.assertEqual(product['description'], 'Descrição microdata')
        self.assertEqual(product['reviews'], [])
        self.assertEqual(product['rating'], '')
        self.assertEqual(product['fieldDiagnostics']['reviews']['status'], 'absent')
        self.assertEqual(product['images'], ['https://cdn.shop.example/a.jpg'])

    def test_query_product_ids_do_not_overwrite_each_other(self):
        first = GenericAdapter().extract('https://shop.example/product?id=1', CONFIG, FIXTURE)
        second = GenericAdapter().extract('https://shop.example/product?id=2', CONFIG, FIXTURE)
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['slug'], second['slug'])

    def test_zero_price_is_preserved_without_inventing_ratings(self):
        html = '<script type="application/ld+json">{"@type":"Product","name":"Gratuito","offers":{"price":0,"priceCurrency":"BRL"}}</script>'
        product = GenericAdapter().extract('https://shop.example/p', CONFIG, html)
        self.assertEqual(product['price'], 'R$ 0,00')
        self.assertEqual(product['currency'], 'BRL')
        self.assertEqual(product['rating'], '')

    def test_media_policy_and_nonfinite_price_or_rating(self):
        html = '<script type="application/ld+json">{"@type":"Product","name":"Produto","image":"https://private.example/x","offers":{"price":"NaN","priceCurrency":"BRL"},"aggregateRating":{"ratingValue":"Infinity"}}</script>'
        product = GenericAdapter().extract('https://shop.example/p', CONFIG, html)
        self.assertEqual(product['price'], '')
        self.assertEqual(product['rating'], '')
        self.assertEqual(product['images'], [])
        self.assertIn('allowlist', product['fieldDiagnostics']['images']['error'])

    def test_microdata_aggregate_and_reviews_have_explicit_paths(self):
        html = '''<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Microdata</h1>
        <div itemprop="aggregateRating"><meta itemprop="ratingValue" content="4.2"><meta itemprop="reviewCount" content="1"></div>
        <article itemprop="review"><p itemprop="reviewBody">Comentário microdata</p><span itemprop="author">Autora</span><meta itemprop="ratingValue" content="4"></article></div>'''
        p = GenericAdapter().extract('https://shop.example/p', CONFIG, html)
        self.assertEqual(p['rating'], '4,2')
        self.assertEqual(p['reviews'][0]['comment'], 'Comentário microdata')
        self.assertEqual(p['reviews'][0]['stars'], 4)
        self.assertEqual(p['fieldDiagnostics']['reviews']['source'], 'authorized_html')
        self.assertIn('itemprop', p['fieldDiagnostics']['rating']['selector_or_path'])

    def test_ssrf_urls_and_market_detection(self):
        for url in ('http://shop.example/a', 'https://127.0.0.1/x', 'https://[::1]/', 'https://2130706433/', 'https://shop.example:444/', 'https://a:b@shop.example/', 'https://localhost/x', 'https://a.local/', 'https://shop.example\\@evil.example', 'https://shop.example/\n'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        with self.assertRaisesRegex(SourceError, 'não autorizado'):
            validate_url('https://shop.example.evil.example/p', ['shop.example'])
        self.assertFalse(adapter_for('https://www.mercadolivre.com.br/MLB1234567').capabilities.get('html'))
        self.assertTrue(adapter_for('https://shop.example/p').capabilities['html'])
        with self.assertRaises(SourceError):
            GenericAdapter().extract('https://www.mercadolivre.com.br/MLB1234567', CONFIG, FIXTURE)

    def test_dns_all_addresses_checked_and_connection_pins_public_address(self):
        for address in ('127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', 'fd00::1', '::ffff:127.0.0.1'):
            with self.subTest(address=address), patch('socket.getaddrinfo', return_value=[(0,0,0,'',('93.184.216.34',443)), (0,0,0,'',(address,443))]), self.assertRaises(SourceError):
                public_addresses('shop.example')
        connection = PinnedHTTPS('shop.example', '93.184.216.34')
        connection._context = MagicMock()
        with patch('socket.create_connection') as create:
            connection.connect()
        create.assert_called_once_with(('93.184.216.34', 443), timeout=12)
        self.assertEqual(connection._context.wrap_socket.call_args.kwargs['server_hostname'], 'shop.example')

    def response(self, status=200, headers=None, body=b'<h1>Produto</h1>'):
        response = MagicMock()
        response.status = status
        response.getheader.side_effect = lambda key, default=None: (headers or {'Content-Type': 'text/html'}).get(key, default)
        response.read.return_value = body
        return response

    def test_redirects_never_leak_credentials_and_private_redirects_rejected(self):
        config = {**CONFIG, 'allowedHosts': ['shop.example', 'second.example']}
        first, second = MagicMock(), MagicMock()
        first.getresponse.return_value = self.response(302, {'Location': 'https://second.example/p'})
        second.getresponse.return_value = self.response()
        with patch('universal_import.public_addresses', return_value=['93.184.216.34']), patch('universal_import.PinnedHTTPS', side_effect=[first, second]):
            self.assertIn('Produto', safe_fetch('https://shop.example/p', config))
        self.assertIn('Authorization', first.request.call_args.kwargs['headers'])
        self.assertNotIn('Authorization', second.request.call_args.kwargs['headers'])
        first.getresponse.return_value = self.response(302, {'Location': 'https://127.0.0.1/secret'})
        with patch('universal_import.public_addresses', return_value=['93.184.216.34']), patch('universal_import.PinnedHTTPS', return_value=first), self.assertRaises(SourceError):
            safe_fetch('https://shop.example/p', CONFIG)

    def test_connection_errors_are_actionable_and_never_echo_secrets(self):
        for code in (401,403,404,429,500):
            connection = MagicMock()
            connection.getresponse.return_value = self.response(code)
            with self.subTest(code=code), patch('universal_import.public_addresses', return_value=['93.184.216.34']), patch('universal_import.PinnedHTTPS', return_value=connection), self.assertRaisesRegex(SourceError, str(code)) as failure:
                safe_fetch('https://shop.example/p', CONFIG)
            self.assertNotIn(CONFIG['apiKey'], str(failure.exception))


class UniversalHttpTest(unittest.TestCase):
    setUp = test_app.NexoHttpTest.setUp
    tearDown = test_app.NexoHttpTest.tearDown
    request = test_app.NexoHttpTest.request

    def post(self, path, body, cookie=None):
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        else:
            import base64
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:secret').decode()
        with self.request(path, json.dumps(body).encode(), headers) as response:
            return json.load(response)

    def login(self, username, password='long-password-123'):
        with self.request('/api/auth/login', json.dumps({'username':username,'password':password}).encode(), {'Content-Type':'application/json'}) as response:
            return response.headers['Set-Cookie'].split(';')[0]

    def user(self, name, tenant=None, role='owner'):
        data = self.post('/api/accounts', {'username':name,'password':'long-password-123','tenant':tenant,'role':role})
        return self.login(name), data['tenant']

    def identity(self, cookie):
        with self.request('/api/auth/session', headers={'Cookie':cookie}) as response:
            return json.load(response)

    def test_accounts_tenant_isolation_encrypted_connections_rbac_and_logout(self):
        alice, a_tenant = self.user('alice')
        bob, _ = self.user('bob')
        editor, _ = self.user('editor', a_tenant, 'editor')
        viewer, _ = self.user('viewer', a_tenant, 'viewer')
        self.post('/api/integrations/generic/save', CONFIG, alice)
        with self.request('/api/integrations', headers={'Cookie':alice}) as response:
            payload = response.read().decode()
            self.assertNotIn(CONFIG['apiKey'], payload)
            self.assertIn('shop.example', payload)
        with self.request('/api/integrations', headers={'Cookie':bob}) as response:
            self.assertNotIn('shop.example', response.read().decode())
        with self.server.store.connect() as db:
            encrypted = db.execute('SELECT encrypted FROM tenant_connections WHERE tenant=?', (a_tenant,)).fetchone()[0]
            self.assertNotIn(CONFIG['apiKey'], encrypted)
            self.assertNotIn(CONFIG['apiKey'], str(db.execute('SELECT * FROM integration_audit').fetchall()))
        for cookie in (editor, viewer):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.post('/api/integrations/generic/revoke', {}, cookie)
            self.assertEqual(failure.exception.code, 403)
        self.post('/api/auth/logout', {}, alice)
        with self.assertRaises(urllib.error.HTTPError):
            self.identity(alice)

    def test_end_to_end_authorized_import_preview_publish_and_cross_account_drafts(self):
        alice, _ = self.user('alice')
        bob, _ = self.user('bob')
        self.post('/api/integrations/generic/save', CONFIG, alice)
        self.post('/api/integrations/generic/save', CONFIG, bob)
        with patch('universal_import.safe_fetch', return_value=FIXTURE) as fetch:
            a = self.post('/api/products/import', {'url':'https://shop.example/lamp'}, alice)
            b = self.post('/api/products/import', {'url':'https://shop.example/lamp'}, bob)
        self.assertEqual(fetch.call_count, 2)
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(a['slug'], b['slug'])
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post('/api/products/publish', {'id':a['id'],'redirect':True,'rightsConfirmed':True}, bob)
        self.assertEqual(failure.exception.code, 404)
        edits = {'name':'Meu título <script>bad()</script>', 'price':'R$ 100,00','description':'Descrição editada'}
        with self.request('/api/products/preview', json.dumps({'id':a['id'],'page':edits}).encode(), {'Cookie':alice,'Content-Type':'application/json'}) as response:
            preview = response.read().decode()
            self.assertIn('Meu título &lt;script&gt;', preview)
            self.assertNotIn('<script>bad()', preview)
            self.assertIn('https://cdn.shop.example', response.headers['Content-Security-Policy'])
        published = self.post('/api/products/publish', {'id':a['id'],'redirect':True,'rightsConfirmed':True,'page':edits}, alice)
        self.assertEqual(published['fieldDiagnostics']['name']['source'], 'user_supplied')
        with self.request('/produto/'+a['slug']) as response:
            public = response.read().decode()
        self.assertIn('Descrição editada', public)
        self.assertIn('Comentário fornecido pela fixture', public)
        self.assertNotIn(CONFIG['apiKey'], public)
        with self.request('/api/products', headers={'Cookie':bob}) as response:
            self.assertEqual(json.load(response)['products'], [])
        own_a = self.post('/api/catalog/products', {'name':'Próprio','storeName':'Loja','sourceUrl':'https://shop.example/own','rightsConfirmed':True}, alice)
        own_b = self.post('/api/catalog/products', {'name':'Próprio','storeName':'Loja','sourceUrl':'https://shop.example/own','rightsConfirmed':True}, bob)
        self.assertNotEqual(own_a['slug'], own_b['slug'])
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/products/publish', {'id':a['id'],'redirect':True,'rightsConfirmed':True,'recommendations':[own_b['slug']]}, alice)

    def test_missing_authorization_no_network_manual_fallback_and_consent(self):
        with patch('universal_import.safe_fetch') as fetch:
            with self.assertRaises(urllib.error.HTTPError):
                self.post('/api/products/import', {'url':'https://shop.example/p'})
            fetch.assert_not_called()
        product = self.post('/api/products/import', {'url':'https://shop.example/p','manual':True,'authorizationConfirmed':True})
        self.assertEqual(product['reviews'], [])
        self.assertEqual(product['rating'], '')
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/products/publish', {'id':product['id'],'redirect':True,'page':{'name':'Meu produto'}})
        self.post('/api/products/publish', {'id':product['id'],'redirect':True,'rightsConfirmed':True,'page':{'name':'Meu produto'}})

    def test_connection_test_status_revocation_and_ssrf_headers(self):
        self.post('/api/integrations/generic/save', CONFIG)
        with patch('universal_import.safe_fetch', return_value=FIXTURE):
            self.assertTrue(self.post('/api/integrations/generic/test', {'url':'https://shop.example/p'})['ok'])
        with patch('universal_import.safe_fetch', side_effect=SourceError('Autorização expirada. HTTP 401.')):
            result = self.post('/api/integrations/generic/test', {'url':'https://shop.example/p'})
            self.assertFalse(result['ok'])
            self.assertIn('401',result['message'])
        for invalid in ({**CONFIG, 'allowedHosts':['127.0.0.1']}, {**CONFIG, 'headers':{'Authorization':'other-secret'}}, {**CONFIG, 'apiKey':'secret\r\nX-Evil: value'}, {**CONFIG,'selectors':{'price':'script:has(x)'}}):
            with self.assertRaises(urllib.error.HTTPError):
                self.post('/api/integrations/generic/save', invalid)
        self.post('/api/integrations/generic/revoke', {})
        self.assertIsNone(self.server.connections.get('admin','generic'))

    def test_oauth_pkce_state_binding_replay_and_concurrent_refresh(self):
        alice, tenant = self.user('alice')
        bob, _ = self.user('bob')
        identity = self.identity(alice)
        config = {'clientId':'app-id','clientSecret':'oauth-secret','redirectUri':'https://nexo.example/api/integrations/mercadolivre/callback','pkce':True,'scopes':'offline_access'}
        self.post('/api/integrations/mercadolivre/save', config, alice)
        url = self.server.connections.oauth_start(identity)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        state = query['state'][0]
        with self.assertRaises(IntegrationError):
            self.server.connections.oauth_finish(state,'code',self.identity(bob))
        token = {'access_token':'access-private','refresh_token':'refresh-private','expires_in':3600}
        with patch('integrations.request_json',return_value=token) as exchange:
            self.server.connections.oauth_finish(state,'code',identity)
            self.assertIn('code_verifier', exchange.call_args.kwargs['form'])
        with self.assertRaises(IntegrationError):
            self.server.connections.oauth_finish(state,'code',identity)
        stored = self.server.connections.get(tenant,'mercadolivre')
        stored['expires_at'] = 0
        self.server.connections.write(tenant,'mercadolivre',stored)
        def rotating(*args):
            time.sleep(.05)
            return {'access_token':'new-access','refresh_token':'rotated-refresh','expires_in':3600}
        with patch('integrations.refresh_token',side_effect=rotating) as refresh:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                results=list(executor.map(lambda _:self.server.connections.access_token(identity), range(6)))
            self.assertEqual(results,['new-access']*6)
            self.assertEqual(refresh.call_count,1)
        self.assertEqual(self.server.connections.get(tenant,'mercadolivre')['refresh_token'],'rotated-refresh')
        with patch('integrations.request_json', return_value={'id': 1}) as me:
            self.assertTrue(self.post('/api/integrations/mercadolivre/test', {}, alice)['ok'])
            me.assert_called_once_with('https://api.mercadolibre.com/users/me', 'new-access')
        with patch('integrations.request_json', side_effect=MercadoLivreError('Sem permissão. HTTP 403.', 403)):
            self.assertFalse(self.post('/api/integrations/mercadolivre/test', {}, alice)['ok'])
        with self.request('/api/integrations',headers={'Cookie':alice}) as response:
            payload=response.read().decode()
            for secret in ('oauth-secret','access-private','new-access','rotated-refresh'):
                self.assertNotIn(secret,payload)
        self.post('/api/integrations/mercadolivre/revoke',{},alice)
        with self.assertRaises(MercadoLivreError):
            self.server.connections.access_token(identity)

    def test_credential_rotation_invalidates_api_cache_and_revoke_blocks_import(self):
        from test_marketplace_contract import ITEM, fixture_api
        alice, tenant = self.user('alice')
        identity = self.identity(alice)
        config = {'clientId':'app-id','clientSecret':'secret-one','redirectUri':'https://nexo.example/api/integrations/mercadolivre/callback'}
        self.post('/api/integrations/mercadolivre/save',config,alice)
        stored = self.server.connections.get(tenant,'mercadolivre')
        self.server.connections.save_grant(identity,stored,{'access_token':'fixture-token','refresh_token':'refresh','expires_in':3600})
        with patch('mercadolivre.request_json',side_effect=fixture_api) as fetch:
            a = self.post('/api/products/import',{'url':ITEM['permalink']},alice)
            count=fetch.call_count
            self.post('/api/products/import',{'url':ITEM['permalink']},alice)
            self.assertEqual(fetch.call_count,count)
            self.post('/api/integrations/mercadolivre/save',{**config,'clientSecret':'secret-two'},alice)
            with self.assertRaises(urllib.error.HTTPError):
                self.post('/api/products/import',{'url':ITEM['permalink']},alice)
        self.post('/api/integrations/mercadolivre/revoke',{},alice)
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/products/import',{'url':ITEM['permalink']},alice)
        with self.request('/api/integrations/audit',headers={'Cookie':alice}) as response:
            audit = response.read().decode()
            self.assertIn('save',audit)
            self.assertNotIn('secret-one',audit)
            self.assertNotIn('secret-two',audit)

    def test_manual_mercado_livre_data_does_not_consult_api(self):
        with patch('universal_import.fetch_product') as api:
            p = self.post('/api/products/import',{'url':'https://produto.mercadolivre.com.br/MLB-7654321','manual':True,'authorizationConfirmed':True})
            api.assert_not_called()
        self.assertEqual(p['source'],'user_supplied')
        self.assertEqual(p['reviews'],[])
        self.post('/api/products/publish',{'id':p['id'],'redirect':True,'rightsConfirmed':True,'page':{'name':'Dados próprios autorizados'}})

    def test_csrf_and_viewer_cannot_publish(self):
        viewer,_=self.user('viewer',role='viewer')
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post('/api/products/import', {'url':'https://shop.example/p','manual':True,'authorizationConfirmed':True},viewer)
        self.assertEqual(failure.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/api/integrations/generic/save',json.dumps(CONFIG).encode(),{'Cookie':viewer,'Content-Type':'application/json','Origin':'https://evil.example'})
        self.assertEqual(failure.exception.code,403)


if __name__ == '__main__':
    unittest.main()
