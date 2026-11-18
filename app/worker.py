import asyncio
import json
import shutil
import time
import tempfile
import uuid
from pathlib import Path
from . import config, db
from .browser import pool
from .collector import Collector, CollectionError
from .network import download
from .storage import ItemStore

ACTIVE = {'queued', 'discovering', 'downloading'}
running_jobs = set()
running_users = set()


def intact_saved_items(user_id, exclude_job_id='', job_id=None):
    """Only this member's successful, still-present files qualify for skipping."""
    rows = db.query('SELECT i.id,i.remote_id,f.path,f.size FROM items i JOIN jobs j ON j.id=i.job_id '
                    'JOIN files f ON f.item_id=i.id WHERE j.user_id=? AND i.status="done" AND j.id!=? '
                    'AND (? IS NULL OR j.id=?)', (user_id, exclude_job_id, job_id, job_id))
    items = {}
    for row in rows:
        ident, valid = items.get(row['id'], (row['remote_id'], True))
        path = (config.DATA / row['path']).resolve()
        try:
            present = path.is_relative_to(config.DATA) and path.is_file() and path.stat().st_size == row['size']
        except OSError:
            present = False
        items[row['id']] = (ident, valid and present)
    return {item_id: ident for item_id, (ident, valid) in items.items() if valid}


def saved_post_ids(user_id, exclude_job_id=''):
    return set(intact_saved_items(user_id, exclude_job_id).values())


def cancelled(job_id):
    row = db.query('SELECT status FROM jobs WHERE id=?', (job_id,), one=True)
    return not row or row['status'] == 'cancelled'


async def extract_audio(source, target, check):
    if not shutil.which('ffmpeg'):
        raise ValueError('服务器未安装 FFmpeg，无法提取音频')
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
        '-protocol_whitelist', 'file,pipe', '-i', str(source), '-vn',
        '-codec:a', 'libmp3lame', '-q:a', '2', str(target),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        for _ in range(360):
            if check():
                raise asyncio.CancelledError()
            try:
                code = await asyncio.wait_for(process.wait(), timeout=0.5)
                if code or not target.exists() or not target.stat().st_size:
                    raise ValueError('音频提取失败：视频可能没有音轨或格式不受支持')
                return
            except asyncio.TimeoutError:
                continue
        raise ValueError('音频提取超时')
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def save_item(job, item_id, post):
    options = json.loads(job['options'])
    def check():
        return cancelled(job['id'])

    store = ItemStore(job, item_id, check)
    errors = []

    async def fetch(candidates, name, image=False):
        existing = store.reusable(name)
        if existing:
            return existing
        with tempfile.TemporaryDirectory(prefix='.download-', dir=store.root) as tmp:
            staged = Path(tmp) / name
            _, mime = await download(candidates, staged,
                                     min(config.MAX_FILE_MB * 1024 * 1024, store.budget(name)),
                                     check, expected='image' if image else 'media')
            if image:
                ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
                       'image/gif': '.gif', 'image/avif': '.avif', 'image/heic': '.heic'}.get(mime, '.img')
                name = str(Path(name).with_suffix(ext))
            elif name == 'music.mp3':
                ext = {'audio/mp4': '.m4a', 'audio/aac': '.aac', 'audio/x-wav': '.wav',
                       'audio/wav': '.wav', 'audio/ogg': '.ogg', 'audio/webm': '.webm'}.get(mime, '.mp3')
                name = str(Path(name).with_suffix(ext))
            return store.publish(staged, name, mime)

    kind = post['kind']
    wanted = (kind == 'video' and (options['video'] or options['audio']) or
              kind == 'image' and (options['images'] or options['audio']) or
              kind == 'article' and options['articles'])
    if not wanted:
        return 'skipped', ''

    if kind == 'video':
        video_path = store.reusable('video.mp4')
        audio_path = store.reusable('audio.mp3') if options['audio'] else None
        if options['video'] or (options['audio'] and not audio_path):
            try:
                video_path = await fetch(post['video_urls'], 'video.mp4')
            except ValueError as e:
                errors.append(str(e))
        if video_path and options['audio'] and not audio_path:
            try:
                with tempfile.TemporaryDirectory(prefix='.audio-', dir=store.root) as tmp:
                    staged = Path(tmp) / 'audio.mp3'
                    await extract_audio(video_path, staged, check)
                    audio_path = store.publish(staged, 'audio.mp3', 'audio/mpeg')
            except ValueError as e:
                errors.append(str(e))
        # Keep a successfully downloaded source when extraction fails, so a retry can reuse it.
        if video_path and not options['video'] and audio_path:
            store.remove('video.mp4')

    if kind in ('image', 'article') and (options['images'] or kind == 'article'):
        replacements = {}
        for index, candidates in enumerate(post['images'], 1):
            try:
                result = await fetch(candidates, f'image_{index:03d}.tmp', image=True)
                for u in candidates:
                    replacements[u] = result.name
            except ValueError as e:
                errors.append(f'图片 {index}：{e}')
        if kind == 'article':
            text = post['text']
            for url, name in replacements.items():
                text = text.replace(url, name)
            store.write_text('article.md', f'# {post["title"].replace(chr(10), " ")}\n\n'
                             f'作者：{post["author"]}\n\n来源：{post["url"]}\n\n{text}\n', 'text/markdown')
            store.write_text('article.txt', post['text'], 'text/plain')

    if kind == 'image' and options['audio']:
        if post['audio_urls']:
            try:
                await fetch(post['audio_urls'], 'music.mp3')
            except ValueError as e:
                errors.append(str(e))
        else:
            errors.append('此图文未提供背景音频')

    if kind == 'video' and post['cover_urls']:
        try:
            await fetch(post['cover_urls'], 'cover.tmp', image=True)
        except ValueError:
            pass

    metadata = {k: post.get(k) for k in ('id', 'title', 'author', 'kind', 'published', 'url', 'statistics', 'text_source')}
    metadata.update(files=[name for name in store.names() if name not in ('metadata.json', 'caption.txt')],
                    errors=errors, captured_at=int(time.time()))
    store.write_text('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2), 'application/json')
    store.write_text('caption.txt', post['title'], 'text/plain')
    db.execute('UPDATE items SET metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item_id))
    return ('failed' if errors else 'done'), '；'.join(errors)


def recount(job_id):
    rows = db.query('SELECT status,COUNT(*) AS n FROM items WHERE job_id=? GROUP BY status', (job_id,))
    counts = {r['status']: r['n'] for r in rows}
    db.update_job(job_id, finished=counts.get('done', 0) + counts.get('skipped', 0), failed=counts.get('failed', 0))


async def run_job(job):
    context = None
    collector = None
    try:
        context = await pool.context(job['user_id'])
        collector = Collector(context,
                              lambda n, msg: db.update_job(job['id'], discovered=n, message=msg),
                              lambda: cancelled(job['id']))
        options = json.loads(job['options'])
        previous = db.query('SELECT remote_id,kind FROM items WHERE job_id=? ORDER BY rowid', (job['id'],))
        excluded = saved_post_ids(job['user_id'], job['id']) if options.get('skip_saved', True) else set()
        # An explicit retry finishes this task's own incomplete items.
        excluded.difference_update(r['remote_id'] for r in previous)
        ids, warnings = await collector.discover(job['url'], options['limit'], exclude_ids=excluded)
        if cancelled(job['id']):
            raise asyncio.CancelledError()
        ids = list(dict.fromkeys([r['remote_id'] for r in previous] + ids))[:options['limit']]
        filtered = len(collector.filtered_ids)
        intact = intact_saved_items(job['user_id'], job_id=job['id'])
        for row in previous:
            if row['remote_id'] not in collector.links and row['kind'] in ('video', 'image', 'article'):
                path = {'video': 'video', 'image': 'note', 'article': 'article'}[row['kind']]
                collector.links[row['remote_id']] = {'url': f'https://www.douyin.com/{path}/{row["remote_id"]}', 'title': ''}
        db.update_job(job['id'], status='downloading', discovered=len(ids), warnings=warnings, filtered=filtered)
        if not ids and filtered:
            db.update_job(job['id'], status='partial' if warnings else 'completed', finished=0, failed=0,
                          message=f'没有新作品，已自动过滤 {filtered} 个已保存作品。可在内容库查看。')
            return
        for index, ident in enumerate(ids):
            if cancelled(job['id']):
                raise asyncio.CancelledError()
            existing = db.query('SELECT * FROM items WHERE job_id=? AND remote_id=?', (job['id'], ident), one=True)
            if existing and (existing['status'] == 'skipped' or
                             existing['status'] == 'done' and existing['id'] in intact):
                continue
            item_id = existing['id'] if existing else uuid.uuid4().hex
            db.update_job(job['id'], message=f'正在保存第 {index + 1} / {len(ids)} 个作品')
            try:
                post = await collector.detail(ident)
                db.execute('INSERT INTO items(id,job_id,remote_id,title,author,kind,published) VALUES(?,?,?,?,?,?,?) '
                           'ON CONFLICT(job_id,remote_id) DO UPDATE SET title=excluded.title,author=excluded.author,'
                           'kind=excluded.kind,published=excluded.published,status="pending",error=""',
                           (item_id, job['id'], ident, post['title'], post['author'], post['kind'], post['published']))
                status, error = await save_item(job, item_id, post)
                db.execute('UPDATE items SET status=?,error=? WHERE id=?', (status, error, item_id))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message = str(e)[:300] if isinstance(e, (CollectionError, ValueError)) else '作品处理失败，请更新登录后重试'
                db.execute('INSERT OR IGNORE INTO items(id,job_id,remote_id,title,kind) VALUES(?,?,?,?,?)',
                           (item_id, job['id'], ident, f'作品 {ident}', 'unknown'))
                db.execute('UPDATE items SET status="failed",error=? WHERE id=?', (message, item_id))
            recount(job['id'])
            await asyncio.sleep(0.8)
        if cancelled(job['id']):
            raise asyncio.CancelledError()
        result = db.query('SELECT finished,failed FROM jobs WHERE id=?', (job['id'],), one=True)
        status = 'partial' if result['failed'] or warnings else 'completed'
        if result['failed'] == len(ids):
            status = 'failed'
        file_count = db.query('SELECT COUNT(*) n FROM files WHERE job_id=?', (job['id'],), one=True)['n']
        message = '任务已结束，可查看作品或导出文件'
        if filtered:
            message += f'；已过滤 {filtered} 个已保存作品'
        if not file_count:
            message = '未保存文件，请查看作品错误或调整所选内容类型'
            if status == 'completed':
                status = 'partial'
        db.update_job(job['id'], status=status, message=message)
    except asyncio.CancelledError:
        # Cancellation is also used for process shutdown and timeouts. Caller owns final state.
        raise
    except Exception as e:
        msg = str(e)[:300] if isinstance(e, (CollectionError, ValueError)) else '获取失败，请检查服务器浏览器组件、网络及抖音登录状态'
        if not cancelled(job['id']):
            db.update_job(job['id'], status='failed', message=msg)
    finally:
        if collector:
            await collector.close()
        if context:
            await context.close()


async def worker_loop():
    while True:
        await pool.reap()
        with db.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            # Concurrent workers may process different members, but never race the same
            # member's duplicate check against an unfinished download.
            placeholders = ','.join('?' for _ in running_users)
            query = 'SELECT * FROM jobs WHERE status="queued"'
            if running_users:
                query += f' AND user_id NOT IN ({placeholders})'
            row = c.execute(query + ' ORDER BY created LIMIT 1', tuple(running_users)).fetchone()
            job = dict(row) if row else None
            if job:
                c.execute('UPDATE jobs SET status="discovering",message="正在打开抖音",updated=? WHERE id=?',
                          (time.time(), job['id']))
        if not job:
            await asyncio.sleep(1)
            continue
        try:
            running_jobs.add(job['id'])
            running_users.add(job['user_id'])
            await asyncio.wait_for(run_job(job), timeout=config.JOB_TIMEOUT)
        except asyncio.TimeoutError:
            if not cancelled(job['id']):
                db.update_job(job['id'], status='failed', message='任务超时，已保存的内容仍可下载；可点击重试')
        except asyncio.CancelledError:
            if getattr(asyncio.current_task(), 'cancelling', lambda: 0)():
                raise
            if cancelled(job['id']):
                continue
            raise
        finally:
            running_jobs.discard(job['id'])
            running_users.discard(job['user_id'])
