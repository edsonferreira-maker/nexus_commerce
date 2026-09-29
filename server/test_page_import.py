import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from page_import import (
    ImportErrorMessage,
    augment_with_dom_snapshot,
    extract_product,
    fetch_html,
    market_for_url,
)


class PageImportTest(unittest.TestCase):
    def test_mercado_livre_retries_as_link_preview_after_cloud_block(self):
        class Response:
            def __init__(self):
                self.headers = Message()
                self.headers['Content-Type'] = 'text/html; charset=utf-8'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return 'https://produto.mercadolivre.com.br/MLB-1234567-produto-_JM'

            def read(self, _limit):
                return b'<html><h1>Produto</h1></html>'

        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                if len(requests) == 1:
                    raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)
                return Response()

        with patch('page_import.urllib.request.build_opener', return_value=Opener()):
            html = fetch_html(
                'https://produto.mercadolivre.com.br/MLB-1234567-produto-_JM'
            )
        self.assertIn('<h1>Produto</h1>', html)
        self.assertIn('facebookexternalhit', requests[1][0].get_header('User-agent'))

    def test_extracts_product_metadata_for_supported_markets(self):
        cases = [
            ('https://www.mercadolivre.com.br/camera/p/MLB47535705', 'Mercado Livre'),
            ('https://shopee.com.br/item/123', 'Shopee'),
            ('https://www.temu.com/goods.html', 'Temu'),
            ('https://pt.aliexpress.com/item/123.html', 'AliExpress'),
            ('https://www.amazon.com.br/dp/B012345678', 'Amazon'),
        ]
        for url, name in cases:
            with self.subTest(name=name):
                html = '''<html><head><script type="application/ld+json">
                {"@context":"https://schema.org","@type":"Product","name":"Câmera",
                 "description":"Descrição da câmera","image":["https://images.example.com/1.jpg"],
                 "offers":{"price":"129.90","priceCurrency":"BRL"},
                 "aggregateRating":{"ratingValue":"4.8","reviewCount":"2"},
                 "review":[{"author":{"name":"Ana"},"reviewBody":"Funcionou bem",
                            "reviewRating":{"ratingValue":"5"}}]}
                </script></head></html>'''
                product = extract_product(url, html)
                self.assertEqual(product['market'], name)
                self.assertEqual(product['name'], 'Câmera')
                self.assertEqual(product['price'], 'R$ 129,90')
                self.assertEqual(product['images'], ['https://images.example.com/1.jpg'])
                self.assertEqual(product['reviews'][0]['comment'], 'Funcionou bem')
                self.assertEqual(product['stock'], -1)

    def test_uses_open_graph_when_json_ld_is_missing(self):
        product = extract_product(
            'https://www.temu.com/goods.html',
            '<meta property="og:title" content="Luminária">'
            '<meta property="og:image" content="https://img.example.com/a.jpg">',
        )
        self.assertEqual(product['name'], 'Luminária')
        self.assertEqual(product['reviews'], [])
        self.assertEqual(product['price'], '')

    def test_extracts_visible_dom_heading_gallery_and_microdata_price(self):
        product = extract_product(
            'https://shopee.com.br/produto',
            '<h1>Câmera Inteligente IM7</h1>'
            '<img alt="Câmera Inteligente IM7" src="https://images.example.com/1.jpg">'
            '<img alt="Câmera Inteligente IM7" src="https://images.example.com/2.jpg">'
            '<meta itemprop="priceCurrency" content="BRL">'
            '<span itemprop="price" content="249.90">R$ 249,90</span>',
        )
        self.assertEqual(product['name'], 'Câmera Inteligente IM7')
        self.assertEqual(product['images'], [
            'https://images.example.com/1.jpg', 'https://images.example.com/2.jpg',
        ])
        self.assertEqual(product['price'], 'R$ 249,90')

    def test_builds_product_from_automatically_inspected_dom(self):
        url = 'https://www.mercadolivre.com.br/produto/MLB1234567'
        html = augment_with_dom_snapshot('<html><body></body></html>', {
            'title': 'Produto carregado por JavaScript',
            'description': 'Detalhes encontrados na página visível.',
            'priceText': 'R$ 1.299,90',
            'images': ['https://http2.mlstatic.com/produto.jpg'],
        }, url)
        product = extract_product(url, html)
        self.assertEqual(product['name'], 'Produto carregado por JavaScript')
        self.assertEqual(product['price'], 'R$ 1.299,90')
        self.assertEqual(product['images'], ['https://http2.mlstatic.com/produto.jpg'])
        self.assertEqual(product['description'], 'Detalhes encontrados na página visível.')

    def test_rejects_arbitrary_urls_and_missing_product_data(self):
        with self.assertRaises(ImportErrorMessage):
            market_for_url('https://amazon.com.br.evil.example/dp/B012345678')
        with self.assertRaisesRegex(ImportErrorMessage, 'não trouxe dados do produto'):
            extract_product('https://www.amazon.com.br/dp/B012345678', '<html></html>')


if __name__ == '__main__':
    unittest.main()
