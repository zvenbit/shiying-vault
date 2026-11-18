import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('VAULT_DATA', ROOT.parent / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'vault.sqlite3'
PUBLIC_URL = os.environ.get('PUBLIC_URL', 'http://localhost:8765').rstrip('/')
SECURE_COOKIE = PUBLIC_URL.startswith('https://')
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
MAX_ITEMS = int(os.environ.get('MAX_ITEMS', '200'))
MAX_FILE_MB = int(os.environ.get('MAX_FILE_MB', '300'))
USER_QUOTA_MB = int(os.environ.get('USER_QUOTA_MB', '5120'))
JOB_TIMEOUT = int(os.environ.get('JOB_TIMEOUT_SECONDS', '3600'))
WORKERS = max(1, min(3, int(os.environ.get('WORKER_CONCURRENCY', '1'))))
BROWSER_CHANNEL = os.environ.get('BROWSER_CHANNEL') or None
BROWSER_HEADLESS = os.environ.get('BROWSER_HEADLESS', 'true').lower() != 'false'
BROWSER_SANDBOX = os.environ.get('BROWSER_SANDBOX', 'true').lower() == 'true'
DISABLE_WORKER = os.environ.get('DISABLE_WORKER') == '1'
SESSION_SECONDS = 7 * 24 * 3600

