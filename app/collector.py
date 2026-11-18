import asyncio
import json
import re
from urllib.parse import urlsplit
from .parser import walk_posts, normalize, post_id, input_url, ID
from .browser import pool


class CollectionError(Exception):
    pass


class Collector:
    def __init__(self, context, report, cancelled):
        self.context = context
        self.report = report
        self.cancelled = cancelled
        self.posts = {}
        self.detail_ids = set()
        self.detail_problems = {}
        self.links = {}
        self.pending = set()
        self.has_more = None
        self.profile_name = ''
        self.page = None
        self.current_detail = None
        self.list_problem = ''
        self.filtered_ids = set()

    def check(self):
        if self.cancelled():
            raise asyncio.CancelledError()

    def on_response(self, response):
        path = urlsplit(response.url).path
        if not ('/aweme/' in path or '/article/' in path):
            return
        is_post = '/aweme/post/' in path
        is_detail = '/aweme/detail/' in path or ('/article/' in path and '/detail' in path)
        if not is_post and not is_detail:
            return
        task = asyncio.create_task(self.capture(response, is_post, is_detail))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def capture(self, response, is_post, is_detail):
        try:
            if int(response.headers.get('content-length', '0')) > 8 * 1024 * 1024:
                return
            data = await response.json()
            if is_detail and isinstance(data, dict):
                filtered = data.get('filter_detail') or {}
                ident = str(filtered.get('aweme_id') or '') if isinstance(filtered, dict) else ''
                if (re.fullmatch(ID, ident) and not data.get('aweme_detail')
                        and filtered.get('filter_reason') == 'status_self_see'):
                    self.detail_problems[ident] = '抖音返回作品不可用：作品已删除或权限不允许访问，请更换公开作品链接'
            if is_post:
                self.list_problem = ''
                if not isinstance(data, dict) or not isinstance(data.get('aweme_list'), list):
                    self.list_problem = '抖音页面已加载，但作品接口未返回有效数据。请在「账号与连接」中连接抖音后重试；若仍失败，请稍后重试或检查服务器出口网络'
            if is_post and 'has_more' in data:
                self.has_more = bool(data['has_more'])
            for raw in walk_posts(data):
                post = normalize(raw)
                if is_detail and self.current_detail and post['id'] != self.current_detail:
                    continue
                self.posts[post['id']] = post
                if is_detail:
                    self.detail_problems.pop(post['id'], None)
                    self.detail_ids.add(post['id'])
        except Exception:
            if is_post:
                self.list_problem = '抖音页面已加载，但作品接口返回空响应或非 JSON 内容。请在「账号与连接」中连接抖音后重试；若仍失败，请稍后重试或检查服务器出口网络'

    async def settle(self):
        if self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)

    async def read_links(self):
        # Only actual links in the visible profile's content area, excluding recommendations.
        anchors = await self.page.locator(
            '[id^="semiTabPanel"] a[href], #user_detail_element a[href], '
            '[data-e2e="user-post-list"] a[href]').evaluate_all(
            '(els) => els.map(a => ({url:a.href,title:a.innerText || a.getAttribute("aria-label") || ""}))')
        for a in anchors:
            ident = post_id(a['url'])
            if re.fullmatch(ID, ident) and urlsplit(a['url']).hostname == 'www.douyin.com':
                self.links[ident] = a

    async def blocked(self):
        text = (await self.page.locator('body').inner_text())[:24000]
        if any(s in text for s in ('请完成下方验证', '拖动滑块', '安全验证', '访问过于频繁')):
            return '抖音要求安全验证。请在「连接抖音」中手动完成验证后重试'
        if self.list_problem:
            return self.list_problem
        if '服务异常' in text:
            return '抖音页面提示服务异常，请更新登录或稍后重试'
        return ''

    async def discover(self, url, limit, exclude_ids=None):
        exclude_ids = set(exclude_ids or ())
        self.filtered_ids.clear()
        self.page = await self.context.new_page()
        self.page.on('response', self.on_response)
        try:
            await self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
            await asyncio.sleep(3)
        except Exception as e:
            reason = pool.diagnostics.get(self.context, {}).get('navigation')
            raise CollectionError(reason or '抖音页面加载失败，请检查服务器网络是否可访问抖音') from e
        self.check()
        # Validate the resolved short-link target. A profile redirect is a profile task.
        resolved = input_url(self.page.url)
        ident = post_id(resolved)
        if ident:
            await self.settle()
            if ident in exclude_ids:
                self.filtered_ids.add(ident)
                return [], []
            self.links[ident] = {'url': resolved, 'title': ''}
            return [ident], []
        if '/user/' not in urlsplit(resolved).path:
            raise CollectionError('分享链接未跳转到作品或博主主页')
        stagnant = 0
        previous = 0
        reached_end = False
        for _ in range(80):
            self.check()
            await self.settle()
            await self.read_links()
            observed = list(dict.fromkeys([*self.posts, *self.links]))
            self.filtered_ids.update(set(observed) & exclude_ids)
            ids = [ident for ident in observed if ident not in exclude_ids]
            self.report(len(ids), f'正在读取博主作品列表，已过滤 {len(self.filtered_ids)} 个已保存作品')
            if len(ids) >= limit:
                return ids[:limit], ['已达到本次设置的作品数量上限；不代表博主全部作品。']
            text = await self.page.locator('body').inner_text()
            reached_end = self.has_more is False or any(s in text for s in ('暂时没有更多了', '没有更多作品', '已加载全部'))
            if reached_end:
                break
            # Previously saved pages still count as progress while looking for unseen works.
            stagnant = stagnant + 1 if len(observed) == previous else 0
            if stagnant >= 5:
                break
            previous = len(observed)
            await self.page.mouse.wheel(0, 1100)
            await asyncio.sleep(1.6)
        if not ids and not self.filtered_ids:
            reason = await self.blocked()
            raise CollectionError(reason or '未获取到作品。可能需要登录、账号无公开作品或页面结构已变化；请连接抖音后重试')
        warnings = [] if reached_end else ['列表停止加载，已保存本次发现的作品；无法确认已获取博主全部作品。']
        return ids[:limit], warnings

    async def detail(self, ident):
        self.check()
        if ident in self.detail_problems:
            raise CollectionError(self.detail_problems[ident])
        cached = self.posts.get(ident)
        link = self.links.get(ident, {})
        url = link.get('url') or (cached or {}).get('url') or f'https://www.douyin.com/video/{ident}'
        if cached and cached['kind'] != 'article' and (cached['video_urls'] or cached['images']):
            return cached
        self.current_detail = ident
        await self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
        for _ in range(8):
            self.check()
            await asyncio.sleep(0.8)
            await self.settle()
            if ident in self.detail_problems:
                raise CollectionError(self.detail_problems[ident])
            if ident in self.detail_ids:
                break
        post = self.posts.get(ident)
        if (post and post['kind'] == 'article' and ident in self.detail_ids and post['text']
                and not post.get('article_has_more', False)):
            post['text_source'] = 'aweme_detail'
            return post
        # Long-article list responses may contain a 499-character preview. Never export that as full text.
        if '/article/' in self.page.url or (post and post['kind'] == 'article'):
            article_locator = self.page.locator('article, [data-e2e="article-content"], '
                                               '[class*="article-content"], [class*="articleContent"]')
            read_article = '''
              els => els.map(e => ({text:e.innerText, images:Array.from(e.querySelectorAll('img')).map(i=>i.currentSrc||i.src)}))
                        .filter(x => x.text && x.text.length > 200).sort((a,b)=>b.text.length-a.text.length)[0] || null
            '''
            article = await article_locator.evaluate_all(read_article)
            if article:
                # Scroll through the article to trigger lazy images, then reread content.
                for _ in range(5):
                    self.check()
                    await self.page.mouse.wheel(0, 1800)
                    await asyncio.sleep(0.25)
                article = await article_locator.evaluate_all(read_article)
                if not article:
                    raise CollectionError('文章正文加载后不可读取，请稍后重试')
                post = post or {'id': ident, 'title': link.get('title') or (await self.page.title()).split(' - 抖音')[0],
                                'author': '', 'published': 0, 'url': self.page.url, 'kind': 'article',
                                'video_urls': [], 'audio_urls': [], 'images': [], 'cover_urls': [], 'statistics': {}}
                post['text'] = article['text']
                post['images'] = [[u] for u in article['images'] if u.startswith('https://')]
                post['text_source'] = 'article_dom'
                return post
            raise CollectionError('未能读取文章完整正文，已拒绝将列表摘要当作全文保存')
        if post and (post['video_urls'] or post['images']):
            return post
        raise CollectionError(await self.blocked() or '页面未返回可下载的作品数据，可能需要登录或作品已不可用')

    async def close(self):
        for task in list(self.pending):
            task.cancel()
        await asyncio.gather(*list(self.pending), return_exceptions=True)
