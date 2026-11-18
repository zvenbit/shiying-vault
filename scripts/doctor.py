#!/usr/bin/env python3
"""Non-secret operational diagnostics. Run inside the deployment container."""
import importlib.metadata
import asyncio
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.network import validate_url

print('拾映部署检查')
print('FFmpeg:', shutil.which('ffmpeg') or '未找到')
for package in ('fastapi','uvicorn','playwright','cryptography'):
    try: print(package + ':', importlib.metadata.version(package))
    except importlib.metadata.PackageNotFoundError: print(package + ': 未安装')
data = Path(os.environ.get('VAULT_DATA','/data'))
print('数据目录可写:', data.is_dir() and os.access(data,os.W_OK))
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        print('Chromium 已安装:', Path(p.chromium.executable_path).is_file())
except Exception:
    print('Chromium 检查失败，请确认 Playwright 安装')
async def check_network():
    for host in ('www.douyin.com','v.douyin.com','lf-douyin-pc-web.douyinstatic.com',
                 'vcs.zijieapi.com','auth.zijieapi.com',
                 'lf-rc1.yhgfb-cn-static.com','lf-ucenter-web.yhgfb-cn-static.com'):
        try:
            ips=sorted({r[4][0] for r in socket.getaddrinfo(host,443)})
            print(host, ', '.join(ips))
            fake=any(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/15') for ip in ips)
            await validate_url('https://' + host + '/')
            print('  地址校验通过' + ('（代理 Fake-IP 已通过独立公网 DNS 复核）' if fake else ''))
        except (OSError, ValueError) as e:
            print('  地址校验失败:', str(e))
asyncio.run(check_network())
print('地址校验通过不代表作品接口可用；仍需在页面提交真实任务验收。')
print('浏览器沙箱:', os.environ.get('BROWSER_SANDBOX','true'))
print('本检查不会读取或输出登录 Cookie、密码或加密密钥。')
