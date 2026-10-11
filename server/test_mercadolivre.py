import unittest
from unittest.mock import patch

from mercadolivre import MercadoLivreError, fetch_product, is_catalog_product_url, item_id_from_url, product_from_api, selected_item_from_url


class MercadoLivreTest(unittest.TestCase):
    def test_only_accepts_mercado_livre_item_urls(self):
        self.assertEqual(
            item_id_from_url('https://www.mercadolivre.com.br/um-produto/p/MLB1234567890'),
            'MLB1234567890',
        )
        self.assertEqual(
            item_id_from_url('https://www.mercadolivre.com.br/capa/up/MLBU4195394312'),
            'MLBU4195394312',
        )
        with self.assertRaises(MercadoLivreError):
            item_id_from_url('https://evil.example/MLB1234567890')
        with self.assertRaises(MercadoLivreError):
            item_id_from_url('http://localhost/MLB1234567890')
        self.assertTrue(is_catalog_product_url('https://www.mercadolivre.com.br/fone/p/MLB1234567890'))
        self.assertFalse(is_catalog_product_url('https://produto.mercadolivre.com.br/MLB-1234567890'))
        self.assertEqual(
            selected_item_from_url('https://www.mercadolivre.com.br/camera-inteligente-im7-3mp-de-resolucao-branca-intelbras/p/MLB47535705?pdp_filters=deal%3AMLB1365080-1#polycard_client=search-desktop&be_origin=backend&wid=MLB6167651244&sid=search'),
            'MLB6167651244',
        )
        self.assertEqual(
            selected_item_from_url(
                'https://www.mercadolivre.com.br/capa/up/MLBU4195394312?'
                'pdp_filters=item_id:MLB4831773371#is_advertising=true'
            ),
            'MLB4831773371',
        )

    def test_catalog_link_requires_explicit_listing(self):
        with patch('mercadolivre.request_json') as api:
            with self.assertRaisesRegex(MercadoLivreError, 'Selecione'):
                fetch_product('MLB1234567', 'token', catalog=True)
            api.assert_not_called()

    def test_catalog_link_prefers_selected_listing_when_it_matches(self):
        urls = []
        def api(url, _token):
            urls.append(url)
            if '/items/MLB6167651244/description' in url:
                return {}
            if '/items/MLB6167651244' in url:
                return {
                    'id': 'MLB6167651244', 'catalog_product_id': 'MLB47535705',
                    'title': 'Câmera Intelbras', 'price': 100,
                    'permalink': 'https://produto.mercadolivre.com.br/MLB-6167651244',
                    'pictures': [{'secure_url': 'https://http2.mlstatic.com/a.jpg'}],
                }
            return {'reviews': []}
        with patch('mercadolivre.request_json', side_effect=api):
            product = fetch_product('MLB47535705', 'token', catalog=True, selected_item='MLB6167651244')
        self.assertEqual(product['id'], 'MLB6167651244')
        self.assertFalse(any('/products/' in url for url in urls))

    def test_user_product_link_resolves_available_item(self):
        urls = []
        def api(url, _token):
            urls.append(url)
            if '/user-products/MLBU4195394312' in url:
                return {'id': 'MLBU4195394312', 'user_id': 1234}
            if '/users/1234/items/search' in url:
                return {'results': ['MLB6068020650']}
            if '/items/MLB6068020650/description' in url:
                return {'plain_text': 'Descrição'}
            if '/items/MLB6068020650' in url:
                return {
                    'id': 'MLB6068020650', 'status': 'active',
                    'available_quantity': 5, 'title': 'Capa Samsung', 'price': 39.9,
                    'permalink': 'https://produto.mercadolivre.com.br/MLB-6068020650',
                    'pictures': [{'secure_url': 'https://http2.mlstatic.com/capa.jpg'}],
                }
            return {'reviews': []}
        with patch('mercadolivre.request_json', side_effect=api):
            product = fetch_product('MLBU4195394312', 'token')
        self.assertEqual(product['id'], 'MLB6068020650')
        self.assertEqual(product['name'], 'Capa Samsung')
        self.assertTrue(any('user_product_id=MLBU4195394312' in url for url in urls))

    def test_preserves_real_photos_description_and_comments(self):
        product = product_from_api(
            {
                'id': 'MLB1234567890',
                'title': 'Fone real',
                'price': 1234.5, 'currency_id': 'BRL',
                'permalink': 'https://produto.mercadolivre.com.br/MLB-1234567890',
                'pictures': [
                    {'secure_url': 'https://http2.mlstatic.com/a.jpg'},
                    {'secure_url': 'https://http2.mlstatic.com/b.jpg'},
                ],
                'available_quantity': 7,
                'attributes': [{'id': 'BRAND', 'value_name': 'Marca X'}],
                'variations': [{'attribute_combinations': [{'name': 'Cor', 'value_name': 'Preto'}]}],
                'shipping': {'free_shipping': True},
            },
            {'plain_text': 'Descrição real'},
            {
                'rating_average': 4.5,
                'paging': {'total': 2},
                'reviews': [{'content': 'Gostei', 'rate': 5, 'date_created': '2026-08-01T00:00:00Z'}],
            },
        )
        self.assertEqual(product['price'], 'R$ 1.234,50')
        self.assertEqual(len(product['images']), 2)
        self.assertEqual(product['description'], 'Descrição real')
        self.assertEqual(product['stock'], 7)
        self.assertEqual(product['reviews'][0]['comment'], 'Gostei')
        self.assertEqual(product['variations'], ['Cor: Preto'])

    def test_never_invents_reviews(self):
        product = product_from_api(
            {
                'id': 'MLB1234567890', 'title': 'Fone', 'price': 10,
                'permalink': 'https://produto.mercadolivre.com.br/MLB-1234567890',
                'pictures': [{'secure_url': 'https://http2.mlstatic.com/a.jpg'}],
            },
            {},
            {},
        )
        self.assertEqual(product['reviews'], [])
        self.assertEqual(product['reviewCount'], 0)
        self.assertEqual(product['stock'], -1)


if __name__ == '__main__':
    unittest.main()
