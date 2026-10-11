"""Build and run the self-hosted app and API together for local development."""

from __future__ import annotations

import os
import hashlib
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path):
    """Load simple KEY=VALUE entries without replacing shell variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key or not key.replace('_', '').isalnum() or key[0].isdigit():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('\"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def available_port(preferred: int) -> int:
    for port in range(preferred, preferred + 11):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise SystemExit('As portas locais de 8080 a 8090 estão ocupadas. Pare uma execução anterior.')


def prepare_browser():
    """Prepare the backend environment. Browsers are installed separately for E2E."""
    local = ROOT / '.local'
    local.mkdir(exist_ok=True)
    venv = local / 'venv'
    python = venv / 'bin' / 'python'
    requirements = ROOT / 'server' / 'requirements.txt'
    marker = local / 'requirements.sha256'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    try:
        if not python.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        if not marker.exists() or marker.read_text() != digest:
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
            marker.write_text(digest)
        browsers = local / 'browsers'
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(browsers)
        return str(python)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'O ambiente Python não ficou disponível: {error}', flush=True)
        return sys.executable


def open_browser_when_ready():
    url = f"http://127.0.0.1:{os.environ.get('PORT', '8080')}/"
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + 'health', timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        return
    user = os.environ.get('NEXO_ADMIN_USER', 'admin')
    password = os.environ.get('NEXO_ADMIN_PASSWORD', 'local-dev')
    print(f'Abra {url} no navegador (usuário: {user}; senha local: {password}).', flush=True)
    if os.environ.get('NEXO_OPEN_BROWSER', '1') == '0':
        return
    if sys.platform == 'darwin':
        subprocess.run(['open', url], check=False)
    else:
        import webbrowser

        webbrowser.open(url)


def main():
    load_env_file(ROOT / '.env')
    preferred = int(os.environ.get('PORT', '8080'))
    port = available_port(preferred)
    if port != preferred:
        print(f'A porta {preferred} está ocupada; usando {port}.', flush=True)

    python = prepare_browser()
    subprocess.run(
        ['flutter', 'build', 'web', '--release', '--dart-define=NEXO_SELF_HOSTED=true'],
        cwd=ROOT,
        check=True,
    )
    os.environ['HOST'] = '127.0.0.1'
    os.environ['PORT'] = str(port)
    os.environ['NEXO_WEB_DIR'] = str(ROOT / 'build' / 'web')
    os.environ['NEXO_DATA_DIR'] = str(ROOT / '.local' / 'data')
    os.environ.setdefault('NEXO_ADMIN_USER', 'admin')
    if not os.environ.get('NEXO_ADMIN_PASSWORD'):
        os.environ['NEXO_ADMIN_PASSWORD'] = 'local-dev'
    os.environ.setdefault('GEOIP_AUTO_DOWNLOAD', '1')
    if python != sys.executable:
        os.execv(python, [python, str(ROOT / 'scripts' / 'serve_local.py')])
    else:
        sys.path.insert(0, str(ROOT / 'server'))
        from app import main as serve

        threading.Thread(target=open_browser_when_ready, daemon=True).start()
        try:
            serve()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
