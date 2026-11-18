import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit, urljoin
import httpx

DOMAINS = ('douyin.com', 'iesdouyin.com', 'douyincdn.com', 'douyinvod.com', 'douyinpic.com', 'byteimg.com',
           'douyinstatic.com', 'bytegoofy.com',
           'bytecdn.cn', 'bytecdn.com', 'zjcdn.com', 'snssdk.com', 'amemv.com', 'pstatp.com',
           'bytedance.com', 'bytednsdoc.com', 'bytegecko.com', 'bytetos.com', 'ibytedtos.com',
           'volccdn.com', 'toutiao.com', 'toutiaoimg.com', 'ixigua.com', 'bdstatic.com')
# Current Douyin page assets on additional shared CDN hosts. Keep these exact.
STATIC_HOSTS = frozenset(('lf-cdn-tos.bytescm.com', 'lf-rc1.yhgfb-cn-static.com',
                          'lf-rc2.yhgfb-cn-static.com', 'lf-ucenter-web.yhgfb-cn-static.com',
                          # Official verify-center bootstrap fetches /vc/setting here.
                          'vcs.zijieapi.com',
                          # Official identity-verification script requested after QR error 2046.
                          'auth.zijieapi.com'))
_dns = {}
_dns_locks = {}
_FAKE_IP = ipaddress.ip_network('198.18.0.0/15')


async def verify_public_dns(host):
    """Recheck a known platform hostname when a local TUN proxy supplies Fake-IP.

    Only the public hostname is sent to the resolver; never URLs, cookies or headers.
    Normal RFC1918/loopback answers never enter this path.
    """
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
            response = await client.get('https://dns.google/resolve',
                                        params={'name': host, 'type': 'A', 'edns_client_subnet': '0.0.0.0/0'})
            response.raise_for_status()
            data = response.json()
        addresses = [ipaddress.ip_address(a['data']) for a in data.get('Answer', []) if a.get('type') in (1, 28)]
        if data.get('Status') != 0 or not addresses or any(not a.is_global for a in addresses):
            raise ValueError('代理地址对应的域名未通过公网 DNS 校验')
    except (httpx.HTTPError, OSError, KeyError, TypeError) as e:
        raise ValueError('检测到代理 Fake-IP，但公网 DNS 复核失败，请检查代理网络或使用真实 DNS') from e


def allowed_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and not p.username and not p.password and
                p.port in (None, 443) and p.hostname and
                (p.hostname in STATIC_HOSTS or
                 any(p.hostname == d or p.hostname.endswith('.' + d) for d in DOMAINS)))
    except ValueError:
        return False


async def validate_url(url):
    if not allowed_url(url):
        raise ValueError('资源地址不属于支持的抖音域名')
    host = urlsplit(url).hostname
    cached = _dns.get(host)
    if cached and cached > time.time():
        return
    lock = _dns_locks.setdefault(host, asyncio.Lock())
    async with lock:
        if _dns.get(host, 0) > time.time():
            return
        try:
            records = await asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError('抖音域名解析失败，请检查 DNS 或代理网络') from e
        addresses = [ipaddress.ip_address(r[4][0]) for r in records]
        non_public = [a for a in addresses if not a.is_global]
        if not addresses or any(a.version != 4 or a not in _FAKE_IP for a in non_public):
            raise ValueError('禁止访问内网资源地址')
        if non_public:
            await verify_public_dns(host)
        if len(_dns) > 1024:
            _dns.clear()
        _dns[host] = time.time() + 60


async def download(candidates, destination, limit, cancelled, expected='media'):
    """Stream to a partial file, enforce redirects and size, then rename atomically."""
    if not candidates:
        raise ValueError('页面未返回此媒体的下载地址')
    part = destination.with_suffix(destination.suffix + '.part')
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error = '下载失败'
    async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=15), trust_env=False,
                                 headers={'Referer': 'https://www.douyin.com/',
                                          'User-Agent': 'Mozilla/5.0'}) as client:
        for candidate in candidates[:5]:
            try:
                current = candidate
                for _ in range(5):
                    await validate_url(current)
                    async with client.stream('GET', current) as response:
                        if response.is_redirect:
                            current = urljoin(current, response.headers.get('location', ''))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get('content-type', '').lower()
                        if content_type.startswith('text/') or 'application/json' in content_type:
                            raise ValueError('媒体地址返回了验证页，未保存为媒体文件')
                        if expected == 'image' and not content_type.startswith('image/'):
                            raise ValueError('图片地址未返回图片')
                        if int(response.headers.get('content-length', '0')) > limit:
                            raise ValueError('文件超过大小或剩余存储配额限制')
                        size = 0
                        with part.open('wb') as f:
                            async for chunk in response.aiter_bytes(128 * 1024):
                                if cancelled():
                                    raise asyncio.CancelledError()
                                size += len(chunk)
                                if size > limit:
                                    raise ValueError('文件超过大小或剩余存储配额限制')
                                f.write(chunk)
                        if not size:
                            raise ValueError('媒体文件为空')
                        part.replace(destination)
                        return size, content_type.split(';')[0]
                raise ValueError('媒体跳转次数过多')
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, ValueError, OSError) as e:
                # No signed URL / cookie / response body in user-visible errors.
                last_error = str(e) if isinstance(e, ValueError) else '媒体请求失败，请稍后重试或更新抖音登录'
            finally:
                part.unlink(missing_ok=True)
    raise ValueError(last_error)

