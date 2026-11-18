"""Opt-in real-browser regression using only synthetic pages, no Douyin network or cookies."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

temporary = tempfile.TemporaryDirectory(prefix='vault-browser-login-test-')
os.environ['VAULT_DATA'] = temporary.name
os.environ['BROWSER_HEADLESS'] = 'true'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
from app.browser import pool
from app.main import LoginInput, login_input, login_view


async def check():
    db.init()
    context = await pool.context('synthetic-user')

    async def fixture(route):
        if route.request.url == 'https://www.douyin.com/verification-fixture':
            html = '<label>Test code<input id="code"></label><button>Continue</button>'
        elif route.request.url == 'https://www.douyin.com/login-fixture':
            html = '<button onclick="window.open(\'/verification-fixture\')">Open verification</button>'
        else:
            await route.abort()
            return
        await route.fulfill(status=200, content_type='text/html', body=html)

    await context.route('**/*', fixture)
    parent = await context.new_page()
    pool.logins['synthetic-user'] = {'context': context, 'page': parent,
                                    'expires': time.time() + 60, 'lock': asyncio.Lock(),
                                    'phase': 'verification', 'message': 'Synthetic verification'}
    try:
        await parent.goto('https://www.douyin.com/login-fixture')
        async with parent.expect_popup() as popup_info:
            await parent.get_by_role('button', name='Open verification').click()
        popup = await popup_info.value
        await popup.wait_for_load_state()
        snapshot = await login_view(user={'id': 'synthetic-user'})
        assert snapshot['phase'] == 'verification' and not snapshot['ready']
        assert pool.logins['synthetic-user']['page'] is popup
        assert len(snapshot['image']) > 100
        await popup.locator('#code').click()
        await login_input(LoginInput(text='fixture-123'), user={'id': 'synthetic-user'})
        assert await popup.locator('#code').input_value() == 'fixture-123'
        await login_input(LoginInput(key='Backspace'), user={'id': 'synthetic-user'})
        assert await popup.locator('#code').input_value() == 'fixture-12'
        await popup.close()
        await login_view(user={'id': 'synthetic-user'})
        assert pool.logins['synthetic-user']['page'] is parent
        print('PASS: actual popup, screenshot, manual text/key input, parent recovery')
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(check())
    temporary.cleanup()
