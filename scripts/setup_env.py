#!/usr/bin/env python3
"""Create deployment settings without overwriting existing secrets."""
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
destination = root / '.env'
if destination.exists():
    raise SystemExit('.env 已存在，未修改。请按需要手动更新 PUBLIC_URL。')
url = input('站点访问地址（含协议，默认 http://localhost:8765）：').strip() or 'http://localhost:8765'
p = urlsplit(url)
if (p.scheme not in ('http', 'https') or not p.hostname or p.path not in ('', '/')
        or p.username or p.password or p.query or p.fragment
        or not re.fullmatch(r'[A-Za-z0-9.-]+', p.hostname)
        or any(c in url for c in '\r\n \t$`')):
    raise SystemExit('请输入完整站点地址，不含路径。')
password = secrets.token_urlsafe(24)
content = (root / '.env.example').read_text().replace('http://localhost:8765', url.rstrip('/'))
content = content.replace('replace-with-a-random-password-at-least-10-characters', password)
fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(content)
print('.env 已生成；管理员用户名：admin。')
print('管理员密码只在本机 .env 的 ADMIN_PASSWORD 中保存，请通过安全渠道交给使用者。')
print('启动：docker compose up -d --build')
