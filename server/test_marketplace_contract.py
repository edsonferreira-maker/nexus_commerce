"""Regression fixtures are synthetic; these tests never read Mercado Livre pages."""
import base64
import io
import json
import os
import sqlite3
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from app import TrafficStore
from mercadolivre import (API_ROOT, MercadoLivreError, approved_media, fetch_product,
                          item_id_from_url, product_from_api, request_json)
from product_pages import render_product
import test_app

ITEM = {
    'id': 'MLB7654321', 'title': 'Produto de teste', 'price': 25.0,
    'currency_id': 'BRL', 'catalog_product_id': 'MLB1234567',
    'permalink': 'https://produto.mercadolivre.com.br/MLB-7654321',
    'pictures': [{'id': 'photo-a', 'secure_url': 'https://http2.mlstatic.com/a.jpg'}],
    'variations': [{'id': 1, 'picture_ids': ['photo-a'], 'attribute_combinations': []}],
}


def fixture_api(url, token):
    assert token == 'fixture-token'
    if '/description' in url:
        return {'plain_text': 'Descrição de teste <script>alert(1)</script>'}
    if '/reviews/' in url:
        return {'rating_average': 4.5, 'paging': {'total': 1},
                'rating_levels': {'five_star': 1},
                'reviews': [{'content': 'Comentário de teste', 'rate': 5}]}
    return dict(ITEM)


class NormalizationTest(unittest.TestCase):
    def test_url_and_media_security(self):
        self.assertEqual(item_id_from_url('MLB-7654321'), 'MLB7654321')
        for url in ['http://produto.mercadolivre.com.br/MLB7654321',
                    'https://mercadolivre.com.br.evil.test/MLB7654321',
                    'https://user@mercadolivre.com.br/MLB7654321',
                    'https://mercadolivre.com.br:8443/MLB7654321',
                    'https://127.0.0.1/MLB7654321',
                    'https://mercadolivre.com.br/MLB7654321000000000']:
            with self.subTest(url=url), self.assertRaises(MercadoLivreError):
                item_id_from_url(url)
        for url in ['https://mlstatic.com.evil.test/a', 'http://http2.mlstatic.com/a',
                    'https://user@http2.mlstatic.com/a', 'https://http2.mlstatic.com:8080/a']:
            self.assertFalse(approved_media(url))

    def test_missing_optional_data_does_not_invent_values(self):
        product = product_from_api({**ITEM, 'price': None, 'pictures': []}, {}, {})
        self.assertEqual(product['price'], '')
        self.assertEqual(product['rating'], '')
        self.assertEqual(product['images'], [])
        self.assertEqual(product['description'], '')
        self.assertEqual(product['stock'], -1)
        self.assertIn('images', product['missingFields'])

    def test_thumbnail_and_variation_mapping(self):
        product = product_from_api(ITEM, {}, {})
        self.assertEqual(product['variationDetails'][0]['images'], ['https://http2.mlstatic.com/a.jpg'])
        product = product_from_api({**ITEM, 'pictures': [], 'secure_thumbnail': 'https://http2.mlstatic.com/thumb.jpg'}, {}, {})
        self.assertEqual(product['images'], ['https://http2.mlstatic.com/thumb.jpg'])

    def test_malformed_optional_fields_are_safe(self):
        product = product_from_api({**ITEM, 'price': 'bad', 'pictures': [None, 4],
                                    'attributes': 'bad', 'variations': [None], 'shipping': 'bad',
                                    'available_quantity': 'bad'},
                                   {'plain_text': []}, {'reviews': [None, {'content': 'x', 'rate': True}],
                                                        'paging': 'bad', 'rating_average': float('nan')})
        self.assertEqual(product['images'], [])
        self.assertEqual(product['price'], '')
        self.assertEqual(product['rating'], '')
        self.assertIsNone(product['reviews'][0]['stars'])

    def test_optional_endpoint_failures_preserve_main_item(self):
        for status, state in [(400, 'error'), (403, 'forbidden'), (404, 'absent'), (429, 'transient_error'), (503, 'transient_error')]:
            def api(url, token):
                if '/description' in url or '/reviews/' in url:
                    raise MercadoLivreError('fixture', status)
                return ITEM
            with self.subTest(status=status), patch('mercadolivre.request_json', side_effect=api):
                product = fetch_product(ITEM['id'], 'fixture-token')
                self.assertEqual(product['sourceStatus']['description']['state'], state)
                self.assertEqual(product['sourceStatus']['reviews']['httpStatus'], status)
                self.assertEqual(product['images'], ['https://http2.mlstatic.com/a.jpg'])

    def test_catalog_mismatch_and_user_product_ambiguity(self):
        with patch('mercadolivre.request_json', return_value=ITEM):
            with self.assertRaisesRegex(MercadoLivreError, 'não pertence'):
                fetch_product('MLB9999999', 'fixture-token', True, ITEM['id'])
        with patch('mercadolivre.request_json', side_effect=[{'user_id': 1}, {'results': ['MLB7654321', 'MLB1234567']}]):
            with self.assertRaisesRegex(MercadoLivreError, 'múltiplas'):
                fetch_product('MLBU4195394312', 'fixture-token')

    def test_paginated_reviews_and_partial_failure(self):
        offsets = []
        def api(url, token):
            if '/reviews/' in url:
                offsets.append(url)
                if 'offset=10' in url:
                    return {'reviews': [{'content': 'Página dois', 'rate': 4}]}
                return {'reviews': [{'content': 'Página um', 'rate': 5}] * 10, 'paging': {'total': 11}, 'rating_average': 4.8}
            return {} if '/description' in url else ITEM
        with patch('mercadolivre.request_json', side_effect=api):
            product = fetch_product(ITEM['id'], 'fixture-token', review_pages=3)
        self.assertEqual(len(product['reviews']), 11)
        self.assertEqual(product['reviews'][-1]['comment'], 'Página dois')
        self.assertIn('offset=10', offsets[-1])
        self.assertEqual(len(offsets), 2)

    def test_api_redirects_and_unexpected_json(self):
        for status in (301, 302, 307):
            opener = MagicMock()
            opener.open.side_effect = urllib.error.HTTPError(API_ROOT, status, '', {'Location': 'http://127.0.0.1/'}, io.BytesIO(b'{}'))
            with patch('mercadolivre.urllib.request.build_opener', return_value=opener):
                with self.assertRaises(MercadoLivreError) as error:
                    request_json(API_ROOT + '/items/MLB7654321', 'fixture-token')
                self.assertEqual(error.exception.status, status)
                self.assertEqual(opener.open.call_count, 1)
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'[]'
        with patch('mercadolivre.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(MercadoLivreError, 'Formato inesperado'):
                request_json(API_ROOT + '/items/MLB7654321', 'fixture-token')

    def test_bearer_statuses_retry_after_and_no_oauth_replay(self):
        for status in (400, 401, 403, 404, 429):
            opener = MagicMock()
            opener.open.side_effect = urllib.error.HTTPError(API_ROOT, status, '', {'Retry-After': '2'}, io.BytesIO(b'{"access_token":"must-not-leak"}'))
            with patch('mercadolivre.urllib.request.build_opener', return_value=opener), patch('mercadolivre.time.sleep') as sleep:
                with self.assertRaises(MercadoLivreError) as caught:
                    request_json(API_ROOT + '/items/MLB7654321', 'fixture-token')
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn('must-not-leak', str(caught.exception))
                self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer fixture-token')
                self.assertEqual(opener.open.call_count, 3 if status == 429 else 1)
                if status == 429:
                    sleep.assert_called_with(2.0)
        with patch('mercadolivre._request_json', side_effect=MercadoLivreError('rate', 429)) as api:
            with self.assertRaises(MercadoLivreError):
                request_json(API_ROOT + '/oauth/token', form={'grant_type': 'refresh_token'})
            self.assertEqual(api.call_count, 1)

    def test_public_html_escapes_xss_and_signals_stale_price(self):
        product = product_from_api({**ITEM, 'title': '<script>bad()</script>'}, {'plain_text': '<img src=x onerror=bad()>'}, {})
        html = render_product(product, [])
        self.assertIn('&lt;script&gt;bad()', html)
        self.assertNotIn('<script>bad()', html)
        self.assertIn('&lt;img src=x', html)
        self.assertIn('Preço desatualizado', html)
        self.assertIn('Nenhuma recomendação', html)


class MarketplaceHttpTest(unittest.TestCase):
    setUp = test_app.NexoHttpTest.setUp
    tearDown = test_app.NexoHttpTest.tearDown
    request = test_app.NexoHttpTest.request

    def post(self, path, body):
        return self.request(path, json.dumps(body).encode(), {
            'Authorization': 'Basic ' + base64.b64encode(b'admin:secret').decode(),
            'Content-Type': 'application/json',
        })

    def test_import_publish_ssr_recommendations_cache_and_revalidation(self):
        self.server.ml_access_token = lambda: 'fixture-token'
        with patch('mercadolivre.request_json', side_effect=fixture_api) as api:
            with self.post('/api/products/import', {'url': ITEM['id']}) as r:
                draft = json.load(r)
            self.assertEqual(api.call_count, 3)
            with self.post('/api/products/import', {'url': ITEM['id']}) as r:
                self.assertEqual(json.load(r)['syncedAt'], draft['syncedAt'])
            self.assertEqual(api.call_count, 3)
            with self.post('/api/products/import', {'url': ITEM['id'], 'revalidate': True}):
                pass
            self.assertEqual(api.call_count, 6)
        with self.post('/api/catalog/products', {'name': 'Produto próprio', 'storeName': 'Loja própria',
                      'sourceUrl': 'https://loja.example/produto', 'rightsConfirmed': True}) as r:
            own = json.load(r)
        with self.post('/api/products/publish', {'id': ITEM['id'], 'redirect': True,
                      'rightsConfirmed': True, 'recommendations': [own['slug']]}) as r:
            self.assertEqual(json.load(r)['recommendations'], [own['slug']])
        with self.request('/produto/' + draft['slug']) as r:
            content = r.read().decode()
            self.assertIn('Descrição de teste', content)
            self.assertIn('Comentário de teste', content)
            self.assertIn('Produto próprio', content)
            self.assertIn('img-src', r.headers['Content-Security-Policy'])
            self.assertNotIn('fixture-token', content)
            self.assertNotIn('flutter_bootstrap', content)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post('/api/products/publish', {'id': ITEM['id'], 'redirect': True,
                      'rightsConfirmed': True, 'recommendations': ['mlb1234567']})
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()

    def test_rotating_refresh_is_serialized_and_encrypted(self):
        self.server.store.save_token({'access_token': 'old-access', 'refresh_token': 'old-refresh', 'expires_in': 1})
        with patch.dict(os.environ, {'ML_CLIENT_ID': 'client', 'ML_CLIENT_SECRET': 'secret', 'ML_REDIRECT_URI': 'https://example.test/callback'}), patch('app.refresh_token', return_value={'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600}) as refresh:
            with ThreadPoolExecutor(max_workers=8) as executor:
                result = list(executor.map(lambda _: self.server.ml_access_token(), range(8)))
            self.assertEqual(result, ['new-access'] * 8)
            refresh.assert_called_once_with('client', 'secret', 'old-refresh')
        self.assertEqual(self.server.store.get_token()['refresh_token'], 'new-refresh')
        with self.server.store.connect() as db:
            stored = db.execute('SELECT access_token, refresh_token FROM integrations').fetchone()
        self.assertNotIn('new-access', stored)
        self.assertTrue(all(v.startswith('enc:') for v in stored))
        self.assertEqual((self.server.data_root / 'oauth.key').stat().st_mode & 0o777, 0o600)

    def test_401_rotates_token_and_retries_only_once(self):
        self.server.store.save_token({'access_token': 'rejected-access', 'refresh_token': 'old-refresh', 'expires_in': 3600})
        draft = product_from_api(ITEM, {}, {})
        with patch.dict(os.environ, {'ML_CLIENT_ID': 'client', 'ML_CLIENT_SECRET': 'secret', 'ML_REDIRECT_URI': 'https://example.test/callback'}), patch('universal_import.fetch_product', side_effect=[MercadoLivreError('expired', 401), draft]) as fetch, patch('app.refresh_token', return_value={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600}) as refresh:
            with self.post('/api/products/import', {'url': ITEM['id']}) as response:
                self.assertEqual(json.load(response)['id'], ITEM['id'])
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[1], 'new-access')
            refresh.assert_called_once()
        self.assertEqual(self.server.store.get_token()['refresh_token'], 'rotated-refresh')

    def test_absent_media_can_be_published_and_legacy_consent_is_required(self):
        draft = product_from_api({**ITEM, 'pictures': [], 'price': None}, {}, {})
        self.server.store.save_draft(draft)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post('/api/products/publish', {'id': ITEM['id'], 'redirect': True})
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()
        with self.post('/api/products/publish', {'id': ITEM['id'], 'redirect': True, 'rightsConfirmed': True}):
            pass
        with self.request('/produto/' + draft['slug']) as response:
            self.assertIn('Fotos indisponíveis', response.read().decode())
        draft.pop('rightsConfirmed', None)
        self.server.store.save_product(draft)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/produto/' + draft['slug'])
        self.assertEqual(failure.exception.code, 404)
        failure.exception.close()

    def test_oauth_state_cannot_be_replayed(self):
        state = self.server.create_ml_oauth_state()
        self.assertTrue(self.server.valid_ml_oauth_state(state))
        self.assertFalse(self.server.valid_ml_oauth_state(state))

    def test_import_rate_limit_and_admin_protection(self):
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/admin/import')
        self.assertEqual(failure.exception.code, 401)
        failure.exception.close()
        self.server.import_rates['import:admin'] = [time.time()] * 10
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post('/api/products/import', {'url': ITEM['id']})
        self.assertEqual(failure.exception.code, 429)
        failure.exception.close()
