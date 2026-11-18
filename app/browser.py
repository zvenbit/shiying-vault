import asyncio
import time
import weakref
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from . import config
from .network import validate_url
from .security import load_connection, save_connection


class BrowserPool:
    def __init__(self):
        self.runtime = None
        self.browser = None
        self.lock = asyncio.Lock()
        self.login_guard = asyncio.Lock()
        self.logins = {}
        self.diagnostics = weakref.WeakKeyDictionary()

    def record_request(self, context, kind, request):
        """Bounded in-memory metadata only; paths, queries and credentials are excluded."""
        diagnostic = self.diagnostics.get(context)
        if diagnostic is None:
            return
        record = {'host': (urlsplit(request.url).hostname or '')[:253],
                  'type': request.resource_type}
        records = diagnostic.setdefault(kind, [])
        if record not in records and len(records) < 32:
            records.append(record)

    async def ensure(self):
        async with self.lock:
            if self.browser and self.browser.is_connected():
                return
            if not self.runtime:
                self.runtime = await async_playwright().start()
            self.browser = await self.runtime.chromium.launch(
                headless=config.BROWSER_HEADLESS, channel=config.BROWSER_CHANNEL,
                chromium_sandbox=config.BROWSER_SANDBOX,
                args=['--disable-dev-shm-usage'])

    async def context(self, user_id):
        await self.ensure()
        state = load_connection(user_id)
        context = await self.browser.new_context(
            storage_state=state, viewport={'width': 1200, 'height': 800},
            locale='zh-CN', timezone_id='Asia/Shanghai', service_workers='block',
            accept_downloads=False)
        self.diagnostics[context] = {}
        context.on('requestfailed', lambda request: self.record_request(context, 'failed', request))

        async def route_request(route):
            try:
                await validate_url(route.request.url)
                await route.continue_()
            except Exception as e:
                self.record_request(context, 'blocked', route.request)
                if route.request.is_navigation_request():
                    self.diagnostics[context]['navigation'] = (str(e) if isinstance(e, ValueError)
                                                               else '网络请求未通过校验，请检查 DNS 或代理')
                    self.diagnostics[context]['navigation_host'] = urlsplit(route.request.url).hostname or ''
                await route.abort()
        await context.route('**/*', route_request)
        context.set_default_timeout(12000)
        return context

    async def reap(self):
        for uid, session in list(self.logins.items()):
            if session['expires'] < time.time():
                await self.end_login(uid)

    async def start_login(self, user_id):
        async with self.login_guard:
            await self._start_login(user_id)

    async def _start_login(self, user_id):
        await self.reap()
        if user_id in self.logins:
            return
        if len(self.logins) >= 2:
            raise ValueError('当前有其他成员正在连接抖音，请稍后再试')
        context = await self.context(user_id)
        page = await context.new_page()
        self.logins[user_id] = {'context': context, 'page': page,
                                'expires': time.time() + 600, 'lock': asyncio.Lock(),
                                'phase': 'opening', 'message': ''}
        session = self.logins[user_id]
        context.on('response', lambda response: self.capture_login_response(session, response))
        try:
            await page.goto('https://www.douyin.com/user/self', wait_until='domcontentloaded', timeout=45000)
            try:
                await page.get_by_text('扫码登录', exact=True).first.wait_for(state='visible', timeout=6000)
            except BrowserTimeout:
                login = page.get_by_role('button', name='登录', exact=True)
                if await login.count() and await login.first.is_visible():
                    await login.first.click()
        except Exception:
            # Keep the page available for the user to inspect / retry; never bypass a challenge.
            pass

    async def capture_login_response(self, session, response):
        """Keep only QR state/error codes; never retain tokens, redirect URLs or response bodies."""
        p = urlsplit(response.url)
        if p.hostname != 'www.douyin.com' or p.path != '/passport/web/check_qrconnect/':
            return
        try:
            body = await response.json()
            data = body.get('data', {})
            raw_code = data.get('error_code', 0)
            code = int(raw_code) if str(raw_code).isdecimal() and len(str(raw_code)) <= 6 else None
            status = data.get('status', '')
            known_statuses = ('new', 'scanned', 'confirmed', 'expired', 'refused')
            session['qr'] = {'status': status if status in known_statuses else 'unknown',
                             'code': code, 'http': response.status if isinstance(response.status, int) else None,
                             'updated': int(time.time())}
            # Keep observing subsequent replies without replacing or extending verification.
            if session.get('phase') == 'verification':
                return
            if code == 2046:
                session['phase'] = 'verification'
                session['message'] = '抖音返回登录校验错误（2046），尚未登录成功。如果下方出现身份验证，请在页面完成；若仍停在手机确认，请勿重复确认，可使用右侧的短信验证码登录。'
                session['expires'] = max(session.get('expires', 0), time.time() + 300)
                return
            messages = {
                'new': '请用抖音扫描二维码，并在手机上确认登录。',
                'scanned': '抖音已收到扫码，正在等待手机端确认；若你已确认，请稍候。',
                'confirmed': '抖音已收到手机确认，正在完成网页登录…',
                'expired': '抖音二维码已过期，请点击画面中的刷新或重新打开连接窗口。',
                'refused': '手机端已取消本次登录，请重新扫码。',
            }
            session['phase'] = status if status in messages else 'waiting'
            session['message'] = messages.get(status, '正在等待抖音返回登录结果…')
            if isinstance(code, int) and code:
                session['message'] = f'抖音登录接口返回错误（{code}），请重新扫码或稍后重试。'
        except Exception:
            if session.get('phase') != 'verification':
                session['message'] = '暂未读取到抖音登录状态，正在等待下一次轮询…'

    async def login_view(self, user_id):
        await self.reap()
        session = self.logins.get(user_id)
        if not session:
            raise ValueError('连接窗口已过期，请重新打开')
        async with session['lock']:
            pages = [p for p in session['context'].pages if not p.is_closed()]
            if not pages:
                raise ValueError('抖音登录窗口已关闭，请重新打开')
            # Identity verification may open another tab. Screenshot and input use that tab.
            session['page'] = pages[-1]
            cookies = await session['context'].cookies('https://www.douyin.com')
            ready = any(c['name'] in ('sessionid', 'sessionid_ss') and c['value'] for c in cookies)
            if ready:
                save_connection(user_id, await session['context'].storage_state())
            diagnostic = self.diagnostics.get(session['context'], {})
            if not ready and diagnostic.get('navigation'):
                session['message'] = '登录跳转未完成：' + diagnostic.get('navigation_host', '') + '，' + diagnostic['navigation']
            return session, ready

    async def end_login(self, user_id):
        session = self.logins.pop(user_id, None)
        if session:
            try:
                await session['context'].close()
            except Exception:
                pass

    async def close(self):
        for uid in list(self.logins):
            await self.end_login(uid)
        if self.browser:
            await self.browser.close()
        if self.runtime:
            await self.runtime.stop()


pool = BrowserPool()

