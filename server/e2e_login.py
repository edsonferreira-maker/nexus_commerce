"""Login UI regression checks against our local server, with fixture credentials."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from app import NexoServer
from test_app import FakeGeo


class LoginBrowserTest(unittest.TestCase):
    def test_login_errors_and_retry_in_three_engines(self):
        with sync_playwright() as playwright:
            for engine in ('chromium', 'firefox', 'webkit'):
                with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    web = root / 'web'
                    web.mkdir()
                    (web / 'login.html').write_text(
                        (Path(__file__).resolve().parents[1] / 'web/login.html').read_text()
                    )
                    (web / 'index.html').write_text('<html><body>Painel autenticado</body></html>')
                    server = NexoServer(('127.0.0.1', 0), web, root / 'data', 'fixture-password', geo_locator=FakeGeo())
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    browser = None
                    try:
                        browser = getattr(playwright, engine).launch()
                        for javascript in (True, False):
                            with browser.new_context(java_script_enabled=javascript, viewport={'width': 390, 'height': 844}) as context:
                                page = context.new_page()
                                errors = []
                                page.on('pageerror', lambda error: errors.append(str(error)))
                                page.goto(f'http://127.0.0.1:{server.server_port}/login')
                                page.get_by_label('Usuário', exact=True).fill('admin')
                                page.get_by_label('Senha', exact=True).fill('wrong-fixture-password')
                                page.get_by_role('button', name='Entrar no painel').click()
                                expect(page.get_by_role('alert')).to_contain_text('Usuário ou senha incorretos')
                                expect(page.get_by_label('Usuário', exact=True)).to_have_value('admin')
                                expect(page.get_by_label('Senha', exact=True)).to_have_value('')
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                                if javascript:
                                    page.get_by_role('button', name='Mostrar senha').click()
                                    expect(page.get_by_label('Senha', exact=True)).to_have_attribute('type', 'text')
                                    page.get_by_role('button', name='Ocultar senha').click()
                                with patch('app.NexoHandler.allow_rate', return_value=False):
                                    page.get_by_label('Senha', exact=True).fill('wrong-fixture-password')
                                    page.get_by_role('button', name='Entrar no painel').click()
                                    expect(page.get_by_role('alert')).to_contain_text('Aguarde um minuto')
                                page.get_by_label('Senha', exact=True).fill('fixture-password')
                                page.get_by_role('button', name='Entrar no painel').click()
                                expect(page.get_by_text('Painel autenticado')).to_be_visible()
                                self.assertEqual(errors, [])
                    finally:
                        if browser:
                            browser.close()
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
