"""Own-site E2E with explicit authorized HTML/network fixtures; no external shop is scraped."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from app import NexoServer
from test_app import FakeGeo
from test_universal import FIXTURE
from e2e_marketplace import PNG

EVIDENCE = Path(__file__).resolve().parents[1] / '.local/universal-evidence'


class UniversalBrowserTest(unittest.TestCase):
    def test_integrations_live_preview_and_publication_in_three_engines(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            for engine in ('chromium', 'firefox', 'webkit'):
                with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    web = root / 'web'
                    web.mkdir()
                    server = NexoServer(('127.0.0.1', 0), web, root/'data', 'test-password', geo_locator=FakeGeo())
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    origin = f'http://127.0.0.1:{server.server_port}'
                    browser = getattr(playwright, engine).launch()
                    context = browser.new_context(viewport={'width':1440,'height':1000})
                    context.route('https://cdn.shop.example/**', lambda route: route.abort() if route.request.url.endswith('lazy.jpg') else route.fulfill(status=200,content_type='image/png',body=PNG))
                    page = context.new_page()
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    try:
                        self.assertEqual(context.request.post(origin+'/api/auth/login',data={'username':'admin','password':'test-password'}).status,200)
                        page.goto(origin+'/admin/integrations')
                        expect(page.locator('#generic-status')).to_contain_text('not_configured')
                        page.locator('#allowedHosts').fill('shop.example')
                        page.locator('#mediaHosts').fill('cdn.shop.example')
                        page.locator('#apiKey').fill('fixture-api-secret')
                        page.locator('#authorizationConfirmed').check()
                        page.locator('#generic').get_by_role('button',name='Salvar / rotacionar').click()
                        expect(page.locator('#result')).to_have_text('Configuração salva.')
                        expect(page.locator('#apiKey')).to_have_value('')
                        expect(page.locator('#generic-status')).to_contain_text('Credencial armazenada')
                        self.assertNotIn('fixture-api-secret',page.content())
                        page.locator('#test-url').fill('https://shop.example/lamp')
                        with patch('universal_import.safe_fetch',return_value=FIXTURE):
                            page.locator('#generic').get_by_role('button',name='Testar conexão').click()
                            expect(page.locator('#generic-status')).to_contain_text('verified')
                            page.get_by_role('link',name='Importar produto',exact=True).click()
                            # Preview exists and stays visible before importing.
                            expect(page.locator('#preview')).to_be_visible()
                            page.locator('#url').fill('https://shop.example/lamp')
                            page.get_by_role('button',name='Importar',exact=True).click()
                            expect(page.locator('#publish')).to_be_visible()
                            expect(page.locator('#preview-state')).to_have_text('Prévia atualizada')
                        frame=page.frame_locator('#preview-frame')
                        expect(frame.get_by_role('heading',name='Luminária autorizada',exact=True)).to_be_visible()
                        self.assertEqual(page.locator('#preview-frame').get_attribute('sandbox'),'')
                        broken=frame.locator('img').nth(1)
                        broken.scroll_into_view_if_needed()
                        expect(broken).to_have_js_property('complete',True)
                        expect(broken).to_have_js_property('naturalWidth',0)
                        expect(frame.get_by_text('Imagem do anúncio original. Se não carregar, consulte a fonte.').nth(1)).to_be_visible()
                        self.assertIsNone(page.evaluate('window.externalScriptRan'))
                        page.locator('#edit-name').fill('Título revisado <img src=x onerror=alert(1)>')
                        page.locator('#edit-price').fill('R$ 120,00')
                        page.locator('#edit-description').fill('Descrição atualizada em tempo real')
                        expect(frame.get_by_role('heading',name='Título revisado <img src=x onerror=alert(1)>',exact=True)).to_be_visible()
                        expect(frame.get_by_text('Descrição atualizada em tempo real',exact=True)).to_be_visible()
                        # An edit cannot execute markup in the preview.
                        self.assertEqual(frame.locator('img[src="x"]').count(),0)
                        box=page.locator('#preview').bounding_box()
                        form=page.locator('.data-panel').bounding_box()
                        self.assertGreater(box['x'],form['x']+form['width'])
                        page.get_by_role('button',name='Mobile',exact=True).click()
                        page.locator('#preview').screenshot(path=str(EVIDENCE/f'{engine}-preview.png'))
                        page.set_viewport_size({'width':390,'height':844})
                        page.locator('#tab-preview').click()
                        expect(page.locator('#preview')).to_be_visible()
                        expect(page.locator('.data-panel')).not_to_be_visible()
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                        page.screenshot(path=str(EVIDENCE/f'{engine}-mobile.png'),full_page=True)
                        page.locator('#tab-data').click()
                        page.locator('#rights').check()
                        page.get_by_role('button',name='Publicar',exact=True).click()
                        expect(page.locator('#public')).to_be_visible()
                        path=page.locator('#public').get_attribute('href')
                        response=context.request.get(origin+path)
                        self.assertEqual(response.status,200)
                        html=response.text()
                        (EVIDENCE/f'{engine}-ssr.html').write_text(html)
                        self.assertIn('Descrição atualizada em tempo real',html)
                        self.assertIn('Comentário fornecido pela fixture',html)
                        self.assertNotIn('fixture-api-secret',html)
                        self.assertNotIn('onerror=alert(1)>',html.replace('&gt;',''))
                        public=browser.new_context(java_script_enabled=(engine=='firefox'),viewport={'width':390,'height':844})
                        if engine == 'firefox':
                            # Firefox/Playwright can hang evaluating a context with JS disabled.
                            # CSP disables page scripts while allowing the test harness to inspect layout.
                            def disable_scripts(route):
                                response=route.fetch()
                                headers=dict(response.headers)
                                headers['content-security-policy']="default-src 'self'; img-src 'self' https://cdn.shop.example; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'"
                                route.fulfill(response=response,headers=headers)
                            public.route(origin+'/**',disable_scripts)
                        public.route('https://cdn.shop.example/**',lambda route:route.fulfill(status=200,content_type='image/png',body=PNG))
                        public_page=public.new_page()
                        public_page.goto(origin+path)
                        expect(public_page.get_by_text('Descrição atualizada em tempo real',exact=True)).to_be_visible()
                        expect(public_page.get_by_text('Comentário fornecido pela fixture',exact=True)).to_be_visible()
                        self.assertLessEqual(public_page.evaluate('document.documentElement.scrollWidth'),390)
                        image=public_page.locator('img').first
                        image.scroll_into_view_if_needed()
                        expect(image).to_have_js_property('naturalWidth',1)
                        public.close()
                        # Editing the published page restores existing values.
                        published=context.request.get(origin+'/api/products').json()['products'][0]
                        page.goto(origin+'/admin/import?id='+published['id'])
                        expect(page.locator('#edit-price')).to_have_value('R$ 120,00')
                        expect(page.locator('#edit-description')).to_have_value('Descrição atualizada em tempo real')
                        page.goto(origin+'/admin/integrations')
                        page.locator('#generic').get_by_role('button',name='Revogar no Nexo').click()
                        expect(page.locator('#generic-status')).to_contain_text('not_configured')
                        self.assertFalse(errors)
                        print(f'PASS {engine}: connection CRUD, fixture test, universal import, sandbox, live edits, desktop/mobile, publish, SSR without JS, media and revoke',flush=True)
                    finally:
                        context.close()
                        browser.close()
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
