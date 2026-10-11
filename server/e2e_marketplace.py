"""Local-site E2E. Mercado Livre API and CDN responses are explicit fixtures.
Run: PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/venv/bin/python server/e2e_marketplace.py
"""
import base64
import struct
import zlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from app import NexoServer
from test_app import FakeGeo
from test_marketplace_contract import ITEM, fixture_api

EVIDENCE = Path(__file__).resolve().parents[1] / '.local' / 'marketplace-evidence'
def png_chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)


PNG = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\x00\x80\xff')) + png_chunk(b'IEND', b''))


class BrowserFlowTest(unittest.TestCase):
    def test_import_publish_and_public_html_in_all_engines(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            for name in ('chromium', 'firefox', 'webkit'):
                with self.subTest(engine=name), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    web = root / 'web'
                    web.mkdir()
                    server = NexoServer(('127.0.0.1', 0), web, root / 'data', 'fixture-password', geo_locator=FakeGeo())
                    server.ml_access_token = lambda: 'fixture-token'
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    origin = f'http://127.0.0.1:{server.server_address[1]}'
                    browser = getattr(playwright, name).launch()
                    try:
                        with patch('mercadolivre.request_json', side_effect=fixture_api):
                            context = browser.new_context(http_credentials={'username': 'admin', 'password': 'fixture-password'})
                            login = context.request.post(origin + '/api/auth/login', data={'username': 'admin', 'password': 'fixture-password'})
                            self.assertEqual(login.status, 200)
                            page = context.new_page()
                            page.goto(origin + '/admin/import')
                            page.get_by_text('Cadastrar produto próprio', exact=True).click()
                            page.locator('#own-name').fill('Recomendação própria de teste')
                            page.locator('#own-seller').fill('Nossa loja de teste')
                            page.locator('#own-url').fill('https://loja.example/produto')
                            page.locator('#own-rights').check()
                            with page.expect_navigation():
                                page.get_by_role('button', name='Cadastrar no catálogo').click()
                            page.locator('#url').fill(ITEM['permalink'])
                            page.get_by_role('button', name='Importar', exact=True).click()
                            expect(page.locator('#publish')).to_be_visible()
                            expect(page.locator('#result')).to_contain_text('Descrição de teste')
                            page.locator('[name=recommendation]').check()
                            page.locator('#rights').check()
                            page.get_by_role('button', name='Publicar', exact=True).click()
                            expect(page.locator('#public')).to_be_visible()
                            path = page.locator('#public').get_attribute('href')
                            self.assertEqual(path, '/produto/mlb7654321')
                            html = subprocess.check_output(['curl', '--fail', '--silent', origin + path]).decode()
                            self.assertIn('Descrição de teste', html)
                            self.assertIn('Comentário de teste', html)
                            self.assertNotIn('fixture-token', html)
                            (EVIDENCE / f'{name}-ssr.html').write_text(html)
                            context.close()
                            public = browser.new_context(java_script_enabled=(name == 'firefox'), viewport={'width': 390, 'height': 844})
                            if name == 'firefox':
                                def block_page_scripts(route):
                                    response = route.fetch()
                                    headers = dict(response.headers)
                                    headers['content-security-policy'] = "default-src 'self'; img-src 'self' https://*.mlstatic.com; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'"
                                    route.fulfill(response=response, headers=headers)
                                public.route(origin + '/**', block_page_scripts)
                            # This is fixture media, not an assertion of real CDN access.
                            public.route('https://http2.mlstatic.com/a.jpg', lambda route: route.fulfill(status=200, content_type='image/png', body=PNG))
                            page = public.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto(origin + path)
                            print(f'E2E {name}: public page loaded; checking SSR content', flush=True)
                            expect(page.get_by_role('heading', name='Produto de teste', exact=True)).to_be_visible()
                            expect(page.get_by_text('Descrição de teste <script>alert(1)</script>', exact=True)).to_be_visible()
                            expect(page.get_by_text('Comentário de teste', exact=True)).to_be_visible()
                            expect(page.get_by_role('heading', name='Recomendações da nossa loja')).to_be_visible()
                            expect(page.get_by_role('link', name='Recomendação própria de teste')).to_be_visible()
                            image = page.locator('img')
                            image.scroll_into_view_if_needed()
                            expect(image).to_have_js_property('naturalWidth', 1)
                            expect(image).to_have_js_property('complete', True)
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                            self.assertFalse(errors)
                            page.screenshot(path=str(EVIDENCE / f'{name}-mobile.png'), full_page=True)
                            page.set_viewport_size({'width': 1440, 'height': 1000})
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 1440)
                            page.screenshot(path=str(EVIDENCE / f'{name}-desktop.png'), full_page=True)
                            page.get_by_role('link', name='Recomendação própria de teste').click()
                            expect(page.get_by_role('link', name='Visitar loja do vendedor')).to_have_attribute('href', 'https://loja.example/produto')
                            public.close()
                            print(f'E2E {name}: import, own catalog, publish, curl SSR, JS disabled, fixture image, XSS, mobile/desktop and destination PASS', flush=True)
                    finally:
                        browser.close()
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
