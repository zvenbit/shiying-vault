import asyncio
import base64
import json
import logging
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from . import config, db
from .security import (require_user, require_admin, valid_username, password_hash, verify_password,
                       token_hash, same_origin, parse_cookies, save_connection)
from .browser import pool
from .parser import input_url, safe_name
from .worker import worker_loop, ACTIVE, running_jobs

log = logging.getLogger('vault')
login_attempts = {}
export_lock = asyncio.Semaphore(1)
exporting_jobs = set()


class ExportResponse(FileResponse):
    """Release the temporary ZIP even on a bad Range header or disconnected client."""
    async def __call__(self, scope, receive, send):
        cleanup, self.background = self.background, None
        try:
            await super().__call__(scope, receive, send)
        finally:
            if cleanup:
                await cleanup()


@asynccontextmanager
async def lifespan(app):
    db.init()
    if not db.query('SELECT id FROM users LIMIT 1'):
        if len(config.ADMIN_PASSWORD) < 10 or config.ADMIN_PASSWORD.startswith(('change', 'replace')):
            raise RuntimeError('首次启动请在 .env 设置 ADMIN_PASSWORD（至少 10 位），或运行 scripts/setup_env.py')
        db.execute('INSERT INTO users(id,username,password,role,created) VALUES(?,?,?,?,?)',
                   (uuid.uuid4().hex, valid_username(config.ADMIN_USER), password_hash(config.ADMIN_PASSWORD), 'admin', time.time()))
    db.execute('UPDATE jobs SET status="queued",message="服务重启，继续未完成任务" '
               'WHERE status IN ("discovering","downloading")')
    tasks = [] if config.DISABLE_WORKER else [asyncio.create_task(worker_loop()) for _ in range(config.WORKERS)]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await pool.close()


app = FastAPI(title='拾映 · 抖音内容库', version='1.0.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protect(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if not same_origin(request):
            return JSONResponse({'detail': '请求来源不匹配，请使用配置的站点地址'}, status_code=403)
        try:
            if int(request.headers.get('content-length', 0)) > 262144:
                return JSONResponse({'detail': '请求内容过大'}, status_code=413)
        except ValueError:
            return JSONResponse({'detail': '无效请求'}, status_code=400)
        # Bound chunked bodies too, before Pydantic reads the request.
        if request.method != 'DELETE':
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    return JSONResponse({'detail': '请求内容过大'}, status_code=413)
            request._body = bytes(body)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def validation_error(request, error):
    return JSONResponse({'detail': str(error)[:300]}, status_code=400)


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class JobInput(BaseModel):
    source: str = Field(min_length=5, max_length=3000)
    video: bool = True
    audio: bool = True
    images: bool = True
    articles: bool = True
    skip_saved: bool = True
    limit: int = Field(default=50, ge=1, le=1000)


class CookieInput(BaseModel):
    cookies: str = Field(min_length=1, max_length=131072)


class LoginAction(BaseModel):
    x: float = Field(ge=0, le=1200)
    y: float = Field(ge=0, le=800)
    end_x: float = Field(default=-1, ge=-1, le=1200)
    end_y: float = Field(default=-1, ge=-1, le=800)


class LoginInput(BaseModel):
    text: str = Field(default='', max_length=128)
    key: Literal['', 'Tab', 'Backspace', 'Enter'] = ''


@app.get('/healthz')
def health():
    return {'status': 'ok', 'version': '1.0.0'}


@app.post('/api/login')
async def login(body: Credentials, request: Request):
    now = time.time()
    key = (request.client.host, body.username)
    for k in list(login_attempts):
        if not login_attempts[k] or login_attempts[k][-1] < now - 300:
            login_attempts.pop(k, None)
    if len(login_attempts) > 10000:
        raise HTTPException(429, '请求过多，请稍后再试')
    attempts = [t for t in login_attempts.get(key, []) if t > now - 300]
    if len(attempts) >= 8:
        raise HTTPException(429, '尝试次数过多，请 5 分钟后重试')
    login_attempts[key] = attempts + [now]
    row = db.query('SELECT * FROM users WHERE username=?', (body.username,), one=True)
    if not row or not await asyncio.to_thread(verify_password, body.password, row['password']):
        raise HTTPException(401, '用户名或密码不正确')
    login_attempts.pop(key, None)
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires<?', (now,))
    db.execute('INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)',
               (token_hash(token), row['id'], now + config.SESSION_SECONDS))
    response = JSONResponse({'username': row['username'], 'role': row['role']})
    response.set_cookie('vault_session', token, httponly=True, secure=config.SECURE_COOKIE,
                        samesite='strict', max_age=config.SESSION_SECONDS)
    return response


@app.post('/api/logout')
def logout(request: Request):
    db.execute('DELETE FROM sessions WHERE token=?', (token_hash(request.cookies.get('vault_session', '')),))
    response = JSONResponse({'ok': True})
    response.delete_cookie('vault_session')
    return response


@app.get('/api/me')
def me(user=Depends(require_user)):
    connected = db.query('SELECT updated FROM connections WHERE user_id=?', (user['id'],), one=True)
    total_items = db.query('SELECT COUNT(*) n FROM items i JOIN jobs j ON j.id=i.job_id '
                          'WHERE j.user_id=? AND i.status="done"', (user['id'],), one=True)['n']
    return {**user, 'connection': connected, 'usage': db.usage(user['id']),
            'total_items': total_items,
            'quota': config.USER_QUOTA_MB * 1024 * 1024, 'max_items': config.MAX_ITEMS,
            'ffmpeg': bool(shutil.which('ffmpeg'))}


@app.get('/api/users')
def users(admin=Depends(require_admin)):
    return db.query('SELECT id,username,role,created FROM users ORDER BY created')


@app.post('/api/users', status_code=201)
def create_user(body: Credentials, admin=Depends(require_admin)):
    try:
        db.execute('INSERT INTO users(id,username,password,role,created) VALUES(?,?,?,?,?)',
                   (uuid.uuid4().hex, valid_username(body.username), password_hash(body.password), 'member', time.time()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '用户名已存在')
    return {'ok': True}


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


@app.post('/api/password')
def change_password(body: PasswordChange, user=Depends(require_user)):
    row = db.query('SELECT password FROM users WHERE id=?', (user['id'],), one=True)
    if not verify_password(body.old_password, row['password']):
        raise HTTPException(400, '当前密码不正确')
    with db.connection() as c:
        c.execute('UPDATE users SET password=? WHERE id=?', (password_hash(body.new_password), user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
    return {'ok': True}


def own_job(job_id, user):
    row = db.query('SELECT * FROM jobs WHERE id=? AND user_id=?', (job_id, user['id']), one=True)
    if not row:
        raise HTTPException(404, '任务不存在')
    return row


def job_json(row):
    return {**row, 'options': json.loads(row['options']), 'warnings': json.loads(row['warnings'])}


@app.get('/api/jobs')
def list_jobs(user=Depends(require_user)):
    rows = db.query('SELECT * FROM jobs WHERE user_id=? ORDER BY created DESC LIMIT 200', (user['id'],))
    return [job_json(row) for row in rows]


@app.post('/api/jobs', status_code=201)
def create_job(body: JobInput, user=Depends(require_user)):
    url = input_url(body.source)
    options = body.model_dump(exclude={'source'})
    if not any(options[k] for k in ('video', 'audio', 'images', 'articles')):
        raise ValueError('至少选择一种内容类型')
    if body.limit > config.MAX_ITEMS:
        raise ValueError(f'单次最多获取 {config.MAX_ITEMS} 个作品')
    if db.usage(user['id']) >= config.USER_QUOTA_MB * 1024 * 1024:
        raise HTTPException(409, '存储空间已用完，请删除不需要的任务')
    encoded = json.dumps(options, sort_keys=True)
    with db.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = c.execute('SELECT * FROM jobs WHERE user_id=? AND status IN ("queued","discovering","downloading")', (user['id'],)).fetchall()
        for row in rows:
            if row['url'] == url and row['options'] == encoded:
                return job_json(dict(row))
        if len(rows) >= 5:
            raise HTTPException(409, '最多同时排队 5 个任务，请等待当前任务完成')
        ident = uuid.uuid4().hex
        c.execute('INSERT INTO jobs(id,user_id,url,options,status,message,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                  (ident, user['id'], url, encoded, 'queued', '已加入队列', time.time(), time.time()))
    return job_json(own_job(ident, user))


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str, user=Depends(require_user)):
    job = own_job(job_id, user)
    items = db.query('SELECT * FROM items WHERE job_id=? ORDER BY published DESC', (job_id,))
    for item in items:
        item['metadata'] = json.loads(item['metadata'])
        item['files'] = db.query('SELECT id,name,size,mime FROM files WHERE item_id=?', (item['id'],))
    return {**job_json(job), 'items': items}


@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str, user=Depends(require_user)):
    job = own_job(job_id, user)
    if job['status'] in ACTIVE:
        db.update_job(job_id, status='cancelled', message='已取消，已保存的文件仍可下载')
    return {'ok': True}


@app.post('/api/jobs/{job_id}/retry')
async def retry_job(job_id: str, user=Depends(require_user)):
    job = own_job(job_id, user)
    if job_id in exporting_jobs:
        raise HTTPException(409, '任务正在导出，请下载完成后再重试')
    if job['status'] in ACTIVE or job_id in running_jobs:
        raise HTTPException(409, '任务仍在运行')
    count = db.query('SELECT COUNT(*) n FROM jobs WHERE user_id=? AND status IN ("queued","discovering","downloading")', (user['id'],), one=True)['n']
    if count >= 5:
        raise HTTPException(409, '最多同时排队 5 个任务')
    db.update_job(job_id, status='queued', message='重新排队，已完成作品将保留')
    return {'ok': True}


@app.delete('/api/jobs/{job_id}')
async def delete_job(job_id: str, user=Depends(require_user)):
    job = own_job(job_id, user)
    if job_id in exporting_jobs:
        raise HTTPException(409, '任务正在导出，请下载完成后再删除')
    if job['status'] in ACTIVE or job_id in running_jobs:
        raise HTTPException(409, '请先取消任务并等待停止后再删除')
    shutil.rmtree(config.DATA / 'jobs' / job_id, ignore_errors=True)
    db.execute('DELETE FROM jobs WHERE id=? AND user_id=?', (job_id, user['id']))
    return {'ok': True}


@app.get('/api/library')
def library(user=Depends(require_user)):
    items = db.query('SELECT i.*,j.created FROM items i JOIN jobs j ON j.id=i.job_id '
                     'WHERE j.user_id=? AND EXISTS(SELECT 1 FROM files f WHERE f.item_id=i.id) '
                     'ORDER BY j.created DESC,i.published DESC LIMIT 1000', (user['id'],))
    for item in items:
        item['metadata'] = json.loads(item['metadata'])
        item['files'] = db.query('SELECT id,name,size,mime FROM files WHERE item_id=?', (item['id'],))
    return items


def file_path(row):
    path = (config.DATA / row['path']).resolve()
    if not path.is_relative_to(config.DATA) or not path.is_file():
        raise HTTPException(404, '文件不存在')
    return path


@app.get('/api/files/{file_id}')
def get_file(file_id: str, download: bool = False, user=Depends(require_user)):
    row = db.query('SELECT * FROM files WHERE id=? AND user_id=?', (file_id, user['id']), one=True)
    if not row:
        raise HTTPException(404, '文件不存在')
    return FileResponse(file_path(row), media_type=row['mime'], filename=row['name'],
                        content_disposition_type='attachment' if download else 'inline')


@app.get('/api/jobs/{job_id}/export')
async def export_job(job_id: str, user=Depends(require_user)):
    job = own_job(job_id, user)
    if job['status'] in ACTIVE or job_id in running_jobs:
        raise HTTPException(409, '请等待任务结束后导出')
    rows = db.query('SELECT f.*,i.title,i.remote_id FROM files f JOIN items i ON i.id=f.item_id '
                    'WHERE f.job_id=? AND f.user_id=? ORDER BY i.remote_id,f.name', (job_id, user['id']))
    if not rows:
        raise HTTPException(404, '此任务暂时没有可导出的文件')
    if export_lock.locked():
        raise HTTPException(429, '正在打包其他任务，请稍后重试')
    await export_lock.acquire()
    exporting_jobs.add(job_id)
    path = None
    async def cleanup():
        try:
            if path:
                path.unlink(missing_ok=True)
        finally:
            exporting_jobs.discard(job_id)
            export_lock.release()
    try:
        total = sum(r['size'] for r in rows)
        export_dir = config.DATA / 'exports'
        export_dir.mkdir(exist_ok=True)
        if shutil.disk_usage(export_dir).free < total + 100 * 1024 * 1024:
            raise HTTPException(507, '服务器临时空间不足，请逐个下载文件')
        temp = tempfile.NamedTemporaryFile(prefix='douyin-vault-', suffix='.zip', dir=export_dir, delete=False)
        path = Path(temp.name)
        temp.close()
        def package():
            try:
                with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
                    z.writestr('任务信息.json', json.dumps({k: job_json(job)[k] for k in
                                ('url', 'options', 'status', 'warnings', 'created')}, ensure_ascii=False, indent=2))
                    for row in rows:
                        name = f'{row["remote_id"]}_{safe_name(row["title"], 45)}/{row["name"]}'
                        z.write(file_path(row), name)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        packaging = asyncio.create_task(asyncio.to_thread(package))
        try:
            await asyncio.shield(packaging)
        except asyncio.CancelledError:
            # The worker thread cannot be cancelled. Wait before removing its output.
            await packaging
            raise
    except BaseException:
        await cleanup()
        raise
    return ExportResponse(path, media_type='application/zip', filename=f'拾映_{job_id[:8]}.zip',
                          background=BackgroundTask(cleanup))


@app.post('/api/connection/cookies')
def import_cookies(body: CookieInput, user=Depends(require_user)):
    save_connection(user['id'], parse_cookies(body.cookies))
    return {'ok': True, 'message': '登录信息已加密保存，有效性将在获取时验证'}


@app.delete('/api/connection')
async def disconnect(user=Depends(require_user)):
    await pool.end_login(user['id'])
    db.execute('DELETE FROM connections WHERE user_id=?', (user['id'],))
    return {'ok': True}


@app.post('/api/connection/login')
async def begin_login(user=Depends(require_user)):
    try:
        await pool.start_login(user['id'])
    except ValueError:
        raise
    except Exception:
        log.warning('Browser launch failed; check installed Chromium and sandbox configuration')
        raise HTTPException(503, '浏览器启动失败，请让管理员检查 Chromium 安装及容器日志')
    return {'ok': True}


@app.get('/api/connection/login')
async def login_view(user=Depends(require_user)):
    session, ready = await pool.login_view(user['id'])
    # Confirmation often navigates the page. Persisted login must not depend on a screenshot.
    if ready:
        return {'ready': True, 'expires': session['expires'], 'image': ''}
    async with session['lock']:
        image = await session['page'].screenshot(type='jpeg', quality=70, timeout=8000)
    return {'ready': ready, 'expires': session['expires'], 'image': base64.b64encode(image).decode(),
            'phase': session.get('phase', 'waiting'), 'message': session.get('message', '')}


@app.get('/api/connection/diagnostics')
async def login_diagnostics(user=Depends(require_user)):
    session, ready = await pool.login_view(user['id'])
    diagnostic = pool.diagnostics.get(session['context'], {})
    return {'ready': ready, 'phase': session.get('phase', 'waiting'),
            'expires': session['expires'], 'qr': session.get('qr'),
            'pages': sum(not p.is_closed() for p in session['context'].pages),
            'blocked': list(diagnostic.get('blocked', [])),
            'failed': list(diagnostic.get('failed', []))}


@app.post('/api/connection/action')
async def login_action(body: LoginAction, user=Depends(require_user)):
    session, ready = await pool.login_view(user['id'])
    if ready:
        raise ValueError('抖音连接已经完成，可以关闭窗口')
    async with session['lock']:
        page = session['page']
        if body.end_x >= 0 and body.end_y >= 0:
            await page.mouse.move(body.x, body.y)
            await page.mouse.down()
            try:
                await page.mouse.move(body.end_x, body.end_y, steps=18)
            finally:
                await page.mouse.up()
        else:
            await page.mouse.click(body.x, body.y)
    return {'ok': True}


@app.post('/api/connection/input')
async def login_input(body: LoginInput, user=Depends(require_user)):
    if bool(body.text) == bool(body.key):
        raise ValueError('请输入文字或选择一个按键')
    session, ready = await pool.login_view(user['id'])
    if ready:
        raise ValueError('抖音连接已经完成，可以关闭窗口')
    async with session['lock']:
        if body.text:
            await session['page'].keyboard.insert_text(body.text)
        else:
            await session['page'].keyboard.press(body.key)
    # Never echo or persist user-entered verification codes.
    return {'ok': True}


@app.post('/api/connection/close')
async def close_login(user=Depends(require_user)):
    await pool.end_login(user['id'])
    return {'ok': True}


app.mount('/static', StaticFiles(directory=config.ROOT / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(config.ROOT / 'static' / 'index.html')
