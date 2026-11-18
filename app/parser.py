"""Pure parsing of public Douyin page responses; no signature generation."""
import html
import json
import re
from urllib.parse import urlsplit, parse_qs

ID = r'\d{15,25}'
PAGE_HOSTS = {'douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com', 'www.iesdouyin.com'}


def input_url(text):
    text = html.unescape(text).replace('\\/', '/').replace('\\:', ':').replace('\\_', '_')
    match = re.search(r'https?://[^\s<>"\u3000]+', text)
    if not match:
        raise ValueError('请粘贴包含链接的抖音分享内容或博主主页链接，纯口令暂不支持')
    url = match.group().rstrip('，。；！）)]}》')
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in PAGE_HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError('仅支持 HTTPS 抖音分享、作品或博主主页链接')
    if p.hostname != 'v.douyin.com' and not re.match(r'^/(user/|video/|note/|article/|share/)', p.path):
        raise ValueError('请提供博主主页或具体作品链接')
    return url


def post_id(url):
    p = urlsplit(url)
    match = re.search(r'/(?:video|note|article)/(\d{15,25})', p.path)
    value = match.group(1) if match else parse_qs(p.query).get('modal_id', [''])[0]
    return value if re.fullmatch(ID, value) else ''


def urls(value):
    if isinstance(value, str):
        if value.startswith('http://'):
            value = 'https://' + value[7:]
        return [value] if value.startswith('https://') else []
    if isinstance(value, list):
        return [u for item in value for u in urls(item)]
    if isinstance(value, dict):
        return urls(value.get('url_list') or value.get('urlList') or value.get('url') or value.get('src') or [])
    return []


def walk_posts(data, depth=0):
    if depth > 12:
        return
    if isinstance(data, dict):
        ident = str(data.get('aweme_id') or data.get('awemeId') or '')
        if re.fullmatch(ID, ident) and any(k in data for k in ('desc', 'video', 'images', 'article_info', 'articleInfo')):
            yield data
            return
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from walk_posts(value, depth + 1)
    elif isinstance(data, list):
        for item in data:
            yield from walk_posts(item, depth + 1)


def normalize(raw):
    ident = str(raw.get('aweme_id') or raw.get('awemeId'))
    author = raw.get('author') or {}
    article = raw.get('article_info') or raw.get('articleInfo') or {}
    images = raw.get('images') or raw.get('image_post_info', {}).get('images') or []
    video = raw.get('video') or {}
    kind = 'article' if article or raw.get('aweme_type') == 163 else ('image' if images else 'video')
    variants = video.get('bit_rate') or video.get('bitRate') or []
    variants = sorted(variants, key=lambda v: v.get('bit_rate') or v.get('bitRate') or 0, reverse=True)
    video_urls = []
    for variant in variants:
        video_urls += urls(variant.get('play_addr') or variant.get('playAddr'))
    video_urls += urls(video.get('play_addr') or video.get('playAddr') or video.get('play_addr_h264'))
    music = raw.get('music') or {}
    content = article.get('article_content') or article.get('articleContent') or {}
    # The live detail endpoint serializes {markdown, long_article_abstract} as JSON text.
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            content = {}
    markdown = content.get('markdown', '') if isinstance(content, dict) else ''
    if not isinstance(markdown, str):
        markdown = ''
    article_images = re.findall(r'!\[[^\]]*\]\((https://[^\s)]+)(?:\s+[^)]*)?\)', markdown)
    title = article.get('article_title') or article.get('articleTitle') or raw.get('desc') or f'作品 {ident}'
    return {
        'id': ident, 'title': str(title), 'author': author.get('nickname', ''), 'kind': kind,
        'published': int(raw.get('create_time') or raw.get('createTime') or 0),
        'url': f'https://www.douyin.com/{"article" if kind == "article" else "note" if kind == "image" else "video"}/{ident}',
        'video_urls': list(dict.fromkeys(video_urls)),
        'images': ([urls(i.get('display_image') or i) for i in images if isinstance(i, dict)]
                   + [[u] for u in dict.fromkeys(article_images)]),
        'audio_urls': urls(music.get('play_url') or music.get('playUrl')),
        'cover_urls': urls(video.get('cover') or video.get('origin_cover')),
        'text': markdown, 'article_has_more': article.get('has_more') is True,
        'statistics': raw.get('statistics', {}),
    }


def safe_name(value, maximum=75):
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(value)).strip(' .')
    return value[:maximum] or '未命名'
