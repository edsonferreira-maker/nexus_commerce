import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from page_import import (
    ImportErrorMessage,
    augment_with_dom_snapshot,
    extract_product,
    fetch_html,
    mercado_livre_link_fallback,
    market_for_url,
)


class PageImportTest(unittest.TestCase):
    def test_mercado_livre_html_fetch_is_forbidden(self):
        with patch('page_import.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ImportErrorMessage, 'API autorizada'):
                fetch_html('https://produto.mercadolivre.com.br/MLB-1234567')
            opener.assert_not_called()

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
        self.assertEqual(market_for_url('https://amazon.com.br.evil.example/dp/B012345678')[0], 'generic')
        with self.assertRaises(ImportErrorMessage):
            market_for_url('https://127.0.0.1/dp/B012345678')
        with self.assertRaisesRegex(ImportErrorMessage, 'não trouxe dados do produto'):
            extract_product('https://www.amazon.com.br/dp/B012345678', '<html></html>')

    def test_builds_last_resort_draft_from_mlb_permalink(self):
        product = mercado_livre_link_fallback(
            'https://produto.mercadolivre.com.br/MLB-6068020650-capa-iphone-16e-'
            'e-17e-magsafe-anti-impacto-suporte-retratil-_JM?variation=188173224482'
        )
        self.assertEqual(
            product['name'],
            'Capa iPhone 16e e 17e MagSafe anti impacto suporte retratil',
        )
        self.assertEqual(product['market'], 'Mercado Livre')
        self.assertEqual(product['price'], '')
        self.assertEqual(product['images'], [])
        self.assertIn('bloqueou a leitura automática', product['importNotice'])

    def test_builds_last_resort_draft_from_mlbu_permalink(self):
        product = mercado_livre_link_fallback(
            'https://www.mercadolivre.com.br/capa-capinha-anti-shock-para-samsung--'
            'pelicula-ceramica-3d/up/MLBU4195394312'
        )
        self.assertEqual(
            product['name'],
            'Capa capinha anti shock para samsung pelicula ceramica 3d',
        )


if __name__ == '__main__':
    unittest.main()
