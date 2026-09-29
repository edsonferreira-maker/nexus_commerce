import unittest

from page_import import ImportErrorMessage, extract_product, market_for_url


class PageImportTest(unittest.TestCase):
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

    def test_rejects_arbitrary_urls_and_missing_product_data(self):
        with self.assertRaises(ImportErrorMessage):
            market_for_url('https://amazon.com.br.evil.example/dp/B012345678')
        with self.assertRaisesRegex(ImportErrorMessage, 'não trouxe dados do produto'):
            extract_product('https://www.amazon.com.br/dp/B012345678', '<html></html>')


if __name__ == '__main__':
    unittest.main()
