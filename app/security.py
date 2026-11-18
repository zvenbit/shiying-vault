import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import HTTPException, Request
from . import config, db


def password_hash(password):
    if not 10 <= len(password) <= 256:
        raise ValueError('密码须为 10–256 个字符')
    salt = secrets.token_bytes(16)
    digest = Scrypt(salt=salt, length=64, n=16384, r=8, p=1).derive(password.encode())
    return base64.b64encode(salt + digest).decode()


def verify_password(password, stored):
    try:
        raw = base64.b64decode(stored)
        digest = Scrypt(salt=raw[:16], length=64, n=16384, r=8, p=1).derive(password.encode())
        return hmac.compare_digest(digest, raw[16:])
    except (ValueError, TypeError):
        return False


def valid_username(value):
    if not re.fullmatch(r'[\w.@-]{2,40}', value):
        raise ValueError('用户名须为 2–40 位字母、数字、中文或 . _ @ -')
    return value


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def require_user(request: Request):
    token = request.cookies.get('vault_session', '')
    row = db.query('SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id '
                   'WHERE s.token=? AND s.expires>?', (token_hash(token), time.time()), one=True)
    if not row:
        raise HTTPException(401, '请先登录拾映')
    return row


def require_admin(request: Request):
    user = require_user(request)
    if user['role'] != 'admin':
        raise HTTPException(403, '仅管理员可管理成员')
    return user


def cipher():
    path = config.DATA / 'encryption.key'
    if not path.exists():
        key = Fernet.generate_key()
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def save_connection(user_id, state):
    value = cipher().encrypt(json.dumps(state).encode()).decode()
    db.execute('INSERT INTO connections(user_id,encrypted,updated) VALUES(?,?,?) '
               'ON CONFLICT(user_id) DO UPDATE SET encrypted=excluded.encrypted,updated=excluded.updated',
               (user_id, value, time.time()))


def load_connection(user_id):
    row = db.query('SELECT encrypted FROM connections WHERE user_id=?', (user_id,), one=True)
    return json.loads(cipher().decrypt(row['encrypted'].encode())) if row else None


def parse_cookies(text):
    """Accept a Cookie header or browser-exported cookie array; never unrelated origins."""
    text = text.strip()
    if not text or len(text) > 131072:
        raise ValueError('Cookie 内容为空或过大')
    if text.startswith(('[', '{')):
        obj = json.loads(text)
        cookies = obj if isinstance(obj, list) else obj.get('cookies', [])
    else:
        cookies = []
        for pair in text.removeprefix('Cookie:').strip().split(';'):
            if '=' in pair:
                name, value = pair.strip().split('=', 1)
                cookies.append({'name': name, 'value': value, 'domain': '.douyin.com', 'path': '/'})
    cleaned = []
    for c in cookies:
        domain = str(c.get('domain', '.douyin.com')).lstrip('.')
        if domain != 'douyin.com' and not domain.endswith('.douyin.com'):
            continue
        if not re.fullmatch(r'[\w.-]+', str(c.get('name', ''))):
            continue
        entry = {k: c[k] for k in ('name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure') if k in c}
        entry.update(domain=c.get('domain', '.douyin.com'), path=c.get('path', '/'))
        if c.get('sameSite') in ('Strict', 'Lax', 'None'):
            entry['sameSite'] = c['sameSite']
        cleaned.append(entry)
    if not cleaned:
        raise ValueError('未找到有效的抖音 Cookie')
    return {'cookies': cleaned, 'origins': []}


def same_origin(request):
    origin = request.headers.get('origin')
    expected = urlsplit(config.PUBLIC_URL)
    if origin and origin.rstrip('/') != f'{expected.scheme}://{expected.netloc}':
        return False
    return request.headers.get('sec-fetch-site') != 'cross-site'

