"""Offline integration checks. Synthetic fixtures are never shipped as user content."""
import asyncio
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
import zipfile
from contextlib import contextmanager
import httpx
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock

TEMP = tempfile.TemporaryDirectory(prefix='douyin-vault-test-')
PASSWORD = secrets.token_urlsafe(20)
os.environ.update(VAULT_DATA=TEMP.name, ADMIN_PASSWORD=PASSWORD, DISABLE_WORKER='1',
                  PUBLIC_URL='http://localhost:8765')
from fastapi.testclient import TestClient
from app import db, config, worker
from app.main import app
from app.parser import input_url, post_id, normalize, walk_posts, safe_name
from app.network import allowed_url, validate_url, download
from app.security import parse_cookies, save_connection, load_connection
from app.collector import Collector, CollectionError

PROFILE = 'https://www.douyin.com/user/MS4wLjABAAAA_test_author'
POST = '1000000000000000001'


class ParserTests(unittest.TestCase):
    def test_shared_text_and_escaped_url(self):
        self.assertEqual(input_url('文章 &#x20; https\\://v.douyin.com/\\_TEST_ONLY/'), 'https://v.douyin.com/_TEST_ONLY/')

    def test_rejects_ssrf_and_userinfo(self):
        for value in ['http://www.douyin.com/user/a','https://localhost/user/a',
                      'https://www.douyin.com.evil.com/user/a','https://a@www.douyin.com/user/a',
                      'https://www.douyin.com:8080/user/a','只有口令 SYNTHETIC_SHARE_CODE']:
            with self.subTest(value=value), self.assertRaises(ValueError): input_url(value)

    def test_id_variants(self):
        self.assertEqual(post_id(f'https://www.douyin.com/article/{POST}'), POST)
        self.assertEqual(post_id(f'{PROFILE}?modal_id={POST}'), POST)

    def test_media_allowlist_boundaries(self):
        self.assertTrue(allowed_url('https://v3.douyincdn.com/a.mp4'))
        for url in ['https://127.0.0.1/a','https://douyincdn.com.attacker.test/a',
                    'file:///etc/passwd','https://u:p@douyin.com/a','https://douyin.com:8443/a']:
            self.assertFalse(allowed_url(url))

    def test_live_video_cdn_keeps_domain_and_scheme_boundaries(self):
        for host in ('v11-weba.douyinvod.com','v26-web.douyinvod.com'):
            self.assertTrue(allowed_url(f'https://{host}/video.mp4'))
        for url in ('https://douyinvod.com.attacker.test/video.mp4',
                    'https://evil-douyinvod.com/video.mp4',
                    'http://v11-weba.douyinvod.com/video.mp4',
                    'https://v11-weba.douyinvod.com:8443/video.mp4'):
            self.assertFalse(allowed_url(url))

    def test_current_douyin_page_static_resources_are_allowed(self):
        for url in ('https://lf-douyin-pc-web.douyinstatic.com/obj/app.js',
             'https://lf3-cdn-tos.bytegoofy.com/obj/app.js',
             'https://lf-cdn-tos.bytescm.com/obj/app.js',
             'https://lf-rc1.yhgfb-cn-static.com/obj/app.js',
             'https://lf-rc2.yhgfb-cn-static.com/obj/app.js',
             'https://lf-ucenter-web.yhgfb-cn-static.com/obj/app.js'):
            self.assertTrue(allowed_url(url))
        self.assertFalse(allowed_url('https://douyinstatic.com.attacker.test/app.js'))

    def test_official_verification_configuration_host_is_allowed(self):
        self.assertTrue(allowed_url('https://vcs.zijieapi.com/vc/setting'))
        for url in ('https://vcs.zijieapi.com.attacker.test/vc/setting',
                    'https://unrelated.zijieapi.com/vc/setting',
                    'http://vcs.zijieapi.com/vc/setting'):
            self.assertFalse(allowed_url(url))

    def test_secondary_verification_script_host_is_allowed(self):
        host='auth.zijieapi.com'
        self.assertTrue(allowed_url(f'https://{host}/official-sdk.js'))
        self.assertFalse(allowed_url(f'https://{host}.attacker.test/official-sdk.js'))
        self.assertFalse(allowed_url(f'https://subdomain.{host}/official-sdk.js'))

    def test_dns_private_address_is_rejected(self):
        result = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1',443))]
        with patch('app.network.socket.getaddrinfo', return_value=result):
            with self.assertRaises(ValueError): asyncio.run(validate_url('https://private-test.douyin.com/a'))

    def test_selects_highest_bitrate_and_photo_order(self):
        raw={'aweme_id':POST,'desc':'测试','video':{'bit_rate':[
            {'bit_rate':10,'play_addr':{'url_list':['https://v.douyincdn.com/low']}},
            {'bit_rate':100,'play_addr':{'url_list':['https://v.douyincdn.com/high']}}]}}
        self.assertTrue(normalize(raw)['video_urls'][0].endswith('/high'))
        photo=normalize({'aweme_id':POST,'desc':'图集','images':[{'url_list':['https://p.byteimg.com/1']},{'url_list':['https://p.byteimg.com/2']}]})
        self.assertEqual(photo['kind'],'image')
        self.assertEqual(photo['images'][1],['https://p.byteimg.com/2'])

    def test_article_text_and_images(self):
        raw={'aweme_id':POST,'desc':'文章','article_info':{'article_title':'完整文章','article_content':{'markdown':'正文\n![图](https://p.byteimg.com/a.jpg)'}}}
        item=normalize(raw)
        self.assertEqual(item['kind'],'article');self.assertEqual(item['title'],'完整文章')
        self.assertEqual(item['images'],[['https://p.byteimg.com/a.jpg']])
        self.assertEqual(len(list(walk_posts({'data':{'aweme_detail':raw}}))),1)

    def test_cookies_filter_other_domains(self):
        cookies=parse_cookies(json.dumps([{'name':'sessionid','value':'fake','domain':'.douyin.com'},
                                         {'name':'token','value':'other','domain':'.other.com'}]))['cookies']
        self.assertEqual(len(cookies),1)
        self.assertEqual(parse_cookies('Cookie: sessionid=fixture; foo=bar')['cookies'][1]['value'],'bar')

    def test_safe_filenames(self):
        self.assertNotIn('/',safe_name('../../a/b\x00'))


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_article_dom_rereads_images_after_lazy_loading(self):
        collector=Collector(AsyncMock(),lambda *args:None,lambda:False)
        collector.page=AsyncMock();collector.page.url=f'https://www.douyin.com/article/{POST}'
        article=AsyncMock();article.evaluate_all.side_effect=[
            {'text':'初始正文'*80,'images':[]},
            {'text':'加载后的完整正文'*80,'images':['https://p.byteimg.com/lazy.jpg']}]
        collector.page.locator=Mock(return_value=article)
        collector.posts[POST]=normalize({'aweme_id':POST,'desc':'文章夹具','aweme_type':163})
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            result=await collector.detail(POST)
        self.assertEqual(result['images'],[['https://p.byteimg.com/lazy.jpg']])
        self.assertEqual(result['text'],'加载后的完整正文'*80)

    async def test_private_or_deleted_video_reports_unavailable_without_login_advice(self):
        collector=Collector(AsyncMock(),lambda *args:None,lambda:False)
        collector.page=AsyncMock();collector.page.url=f'https://www.douyin.com/video/{POST}'
        body=AsyncMock();body.inner_text.return_value='';collector.page.locator=Mock(return_value=body)
        response=AsyncMock();response.headers={}
        response.json.return_value={'status_code':0,'aweme_detail':None,
            'filter_detail':{'aweme_id':POST,'filter_reason':'status_self_see'}}
        await collector.capture(response,False,True)
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            with self.assertRaisesRegex(CollectionError,'已删除或权限不允许'):
                await collector.detail(POST)

    async def test_secondary_verification_survives_late_qr_expiration(self):
        from app.browser import BrowserPool
        pool=BrowserPool();session={'expires':time.time()+10};response=AsyncMock()
        response.url='https://www.douyin.com/passport/web/check_qrconnect/'
        response.json.return_value={'data':{'error_code':2046,'status':'scanned'}}
        await pool.capture_login_response(session,response)
        self.assertEqual(session['phase'],'verification')
        deadline=session['expires']
        self.assertGreater(deadline,time.time()+250)
        response.json.return_value={'data':{'error_code':0,'status':'expired'}}
        await pool.capture_login_response(session,response)
        self.assertEqual(session['phase'],'verification')
        self.assertEqual(session['expires'],deadline)
        self.assertEqual(session['qr']['status'],'expired')
        self.assertEqual(session['qr']['code'],0)

    async def test_request_diagnostics_are_bounded_and_omit_tokens(self):
        from app.browser import BrowserPool
        pool=BrowserPool(); context=Mock(); pool.diagnostics[context]={}
        for i in range(100):
            request=Mock(url=f'https://host{i}.douyin.com/private-token?token=secret',resource_type='xhr')
            pool.record_request(context,'blocked',request)
        records=pool.diagnostics[context]['blocked']
        self.assertEqual(len(records),32)
        self.assertEqual(records[0],{'host':'host0.douyin.com','type':'xhr'})
        self.assertNotIn('secret',json.dumps(records))
        self.assertNotIn('private-token',json.dumps(records))

    async def test_verification_popup_becomes_active_and_closed_popup_returns_to_parent(self):
        from app.browser import BrowserPool
        pool=BrowserPool();context=AsyncMock();context.cookies.return_value=[]
        parent=Mock();popup=Mock();parent.is_closed.return_value=False;popup.is_closed.return_value=False
        context.pages=[parent,popup]
        pool.logins['fixture']={'context':context,'page':parent,'expires':time.time()+60,'lock':asyncio.Lock()}
        session,ready=await pool.login_view('fixture')
        self.assertFalse(ready);self.assertIs(session['page'],popup)
        popup.is_closed.return_value=True
        session,_=await pool.login_view('fixture')
        self.assertIs(session['page'],parent)

    async def test_qr_status_uses_platform_result_without_exposing_tokens(self):
        from app.browser import BrowserPool
        session={};response=AsyncMock()
        response.url='https://www.douyin.com/passport/web/check_qrconnect/'
        response.json.return_value={'data':{'status':'confirmed','error_code':0,'token':'synthetic-secret','redirect_url':'https://example.invalid/?secret=fixture'}}
        await BrowserPool().capture_login_response(session,response)
        self.assertEqual(session['phase'],'confirmed')
        self.assertIn('手机确认',session['message'])
        self.assertNotIn('synthetic-secret',json.dumps(session))
        self.assertNotIn('redirect_url',session)

    async def test_proxy_fake_ip_requires_independent_public_dns(self):
        original=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'Status':0,'Answer':[{'type':1,'data':'163.181.39.181'}]}))
        fake=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.76',443))]
        with patch('app.network.socket.getaddrinfo',return_value=fake),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
            await validate_url('https://fake-ip-regression.douyin.com/')

    async def test_fake_ip_does_not_allow_private_public_dns_answer(self):
        original=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'}]}))
        fake=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.77',443))]
        with patch('app.network.socket.getaddrinfo',return_value=fake),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
            with self.assertRaises(ValueError):await validate_url('https://fake-private-regression.douyin.com/')

    async def test_fake_ip_dns_failure_stays_blocked(self):
        original=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(503))
        fake=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.78',443))]
        with patch('app.network.socket.getaddrinfo',return_value=fake),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
            with self.assertRaises(ValueError):await validate_url('https://fake-failed-regression.douyin.com/')

    async def test_oversize_download_removes_partial(self):
        original=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'video/mp4'},content=b'0'*20))
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/'video.mp4'
            with patch('app.network.validate_url',new=AsyncMock()),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
                with self.assertRaises(ValueError):await download(['https://v.douyincdn.com/video'],destination,10,lambda:False)
            self.assertFalse(destination.exists());self.assertFalse(destination.with_suffix('.mp4.part').exists())

    async def test_html_challenge_is_not_saved_as_video(self):
        original=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/html'},text='<html>请登录</html>'))
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/'video.mp4'
            with patch('app.network.validate_url',new=AsyncMock()),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
                with self.assertRaises(ValueError):await download(['https://v.douyincdn.com/video'],destination,1024,lambda:False)
            self.assertFalse(destination.exists())

    async def test_redirect_is_revalidated(self):
        original=httpx.AsyncClient
        seen=[]
        transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/private'}))
        async def validate(url):
            seen.append(url)
            if not allowed_url(url):raise ValueError('blocked')
        with tempfile.TemporaryDirectory() as directory:
            with patch('app.network.validate_url',side_effect=validate),patch('app.network.httpx.AsyncClient',side_effect=lambda **kw:original(transport=transport)):
                with self.assertRaises(ValueError):await download(['https://v.douyincdn.com/video'],Path(directory)/'v.mp4',1024,lambda:False)
        self.assertEqual(seen,['https://v.douyincdn.com/video','http://127.0.0.1/private'])

    async def test_article_preview_is_not_treated_as_full_text(self):
        page=AsyncMock();page.url=f'https://www.douyin.com/article/{POST}'
        locator=AsyncMock();locator.evaluate_all.return_value=None
        page.locator=lambda _:locator
        collector=Collector(None,lambda *args:None,lambda:False);collector.page=page
        collector.posts[POST]=normalize({'aweme_id':POST,'article_info':{'article_content':{'markdown':'仅有列表预览'}}})
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            with self.assertRaisesRegex(CollectionError,'完整正文'):await collector.detail(POST)

    async def test_article_detail_decodes_serialized_full_content(self):
        full='合成长文段落。\n\n'*100+'![配图](https://p.byteimg.com/article.jpg)\n末尾段落。'
        raw={'aweme_id':POST,'article_info':{'article_title':'合成文章','has_more':False,
             'article_content':json.dumps({'long_article_abstract':'仅有摘要','markdown':full},ensure_ascii=False)}}
        collector=Collector(None,lambda *args:None,lambda:False)
        page=AsyncMock();page.url=f'https://www.douyin.com/article/{POST}';collector.page=page
        locator=AsyncMock();locator.evaluate_all.return_value=None;page.locator=lambda _:locator
        response=AsyncMock();response.headers={};response.json.return_value={'aweme_detail':raw}
        async def navigate(*args,**kwargs):await collector.capture(response,False,True)
        page.goto.side_effect=navigate
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            post=await collector.detail(POST)
        self.assertEqual(post['text'],full)
        self.assertEqual(post['text_source'],'aweme_detail')
        self.assertEqual(post['images'],[['https://p.byteimg.com/article.jpg']])

    async def test_article_detail_with_more_content_is_not_exported_as_complete(self):
        raw={'aweme_id':POST,'article_info':{'article_title':'合成未完文章','has_more':True,
             'article_content':{'markdown':'服务端只返回了一段，后续内容尚未返回。'}}}
        collector=Collector(None,lambda *args:None,lambda:False)
        page=AsyncMock();page.url=f'https://www.douyin.com/article/{POST}';collector.page=page
        locator=AsyncMock();locator.evaluate_all.return_value=None;page.locator=lambda _:locator
        response=AsyncMock();response.headers={};response.json.return_value={'aweme_detail':raw}
        async def navigate(*args,**kwargs):await collector.capture(response,False,True)
        page.goto.side_effect=navigate
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            with self.assertRaisesRegex(CollectionError,'完整正文'):await collector.detail(POST)

    async def test_discovery_filters_saved_ids_before_applying_new_item_limit(self):
        context=AsyncMock();page=AsyncMock();page.url=PROFILE;context.new_page.return_value=page
        page.on=Mock();body=AsyncMock();body.inner_text.return_value='';page.locator=lambda _:body
        collector=Collector(context,lambda *args:None,lambda:False)
        newer=str(int(POST)+1)
        reads=0
        async def read_links():
            nonlocal reads
            reads+=1
            collector.links[POST]={'url':f'https://www.douyin.com/article/{POST}'}
            if reads>=2:
                collector.links[newer]={'url':f'https://www.douyin.com/article/{newer}'}
                collector.has_more=False
        collector.read_links=read_links
        with patch('app.collector.asyncio.sleep',new=AsyncMock()):
            ids,_=await collector.discover(PROFILE,1,exclude_ids={POST})
        self.assertEqual(ids,[newer]);self.assertEqual(collector.filtered_ids,{POST})

    async def test_empty_platform_response_explains_login_instead_of_network_failure(self):
        response=AsyncMock();response.headers={};response.json.side_effect=ValueError('empty')
        collector=Collector(None,lambda *args:None,lambda:False)
        page=AsyncMock();locator=AsyncMock();locator.inner_text.return_value='服务异常，重新刷新拉取数据'
        page.locator=lambda _:locator;collector.page=page
        await collector.capture(response,True,False)
        reason=await collector.blocked()
        self.assertIn('页面已加载',reason)
        self.assertIn('连接抖音',reason)
        self.assertNotIn('网络不可访问',reason)


class APITests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.client.__enter__()
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD})
        self.assertEqual(r.status_code,200)
        self.user=self.client.get('/api/me').json()
        db.execute('DELETE FROM jobs')

    def tearDown(self):
        self.client.__exit__(None,None,None)

    def job(self, **kwargs):
        r=self.client.post('/api/jobs',json={'source':PROFILE,**kwargs})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def test_auth_cookie_and_csrf(self):
        client=TestClient(app)
        self.assertEqual(client.get('/api/jobs').status_code,401)
        r=self.client.post('/api/jobs',json={'source':PROFILE},headers={'Origin':'https://evil.test'})
        self.assertEqual(r.status_code,403)
        r=client.post('/api/login',json={'username':'admin','password':PASSWORD})
        self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])

    def test_job_dedup_and_type_validation(self):
        first=self.job();second=self.job();self.assertEqual(first['id'],second['id'])
        r=self.client.post('/api/jobs',json={'source':PROFILE,'video':False,'audio':False,'images':False,'articles':False})
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.client.post('/api/jobs',json={'source':PROFILE,'limit':config.MAX_ITEMS+1}).status_code,400)

    def test_member_isolation(self):
        job=self.job();username='m'+uuid.uuid4().hex[:8]
        self.assertEqual(self.client.post('/api/users',json={'username':username,'password':PASSWORD}).status_code,201)
        other=TestClient(app);other.post('/api/login',json={'username':username,'password':PASSWORD})
        self.assertEqual(other.get('/api/jobs').json(),[])
        for suffix in ['', '/export']:
            self.assertEqual(other.get('/api/jobs/'+job['id']+suffix).status_code,404)
        for suffix in ['/cancel','/retry']:
            self.assertEqual(other.post('/api/jobs/'+job['id']+suffix).status_code,404)
        self.assertEqual(other.delete('/api/jobs/'+job['id']).status_code,404)
        self.assertEqual(other.get('/api/users').status_code,403)

    def test_connection_is_encrypted_and_private(self):
        secret='fake-session-'+secrets.token_hex(8)
        r=self.client.post('/api/connection/cookies',json={'cookies':f'sessionid={secret}'})
        self.assertEqual(r.status_code,200)
        row=db.query('SELECT encrypted FROM connections WHERE user_id=?',(self.user['id'],),one=True)
        self.assertNotIn(secret,row['encrypted'])
        self.assertNotIn(secret,self.client.get('/api/me').text)
        self.assertEqual(load_connection(self.user['id'])['cookies'][0]['value'],secret)
        self.assertEqual(self.client.delete('/api/connection').status_code,200)

    def test_confirmed_login_survives_page_navigation_during_screenshot(self):
        from app.browser import pool
        context=AsyncMock();page=AsyncMock()
        page.is_closed=Mock(return_value=False);context.pages=[page]
        cookie={'name':'sessionid','value':'synthetic-confirmed-session','domain':'.douyin.com','path':'/'}
        context.cookies.return_value=[cookie]
        context.storage_state.return_value={'cookies':[cookie],'origins':[]}
        page.screenshot.side_effect=RuntimeError('page is navigating after phone confirmation')
        pool.logins[self.user['id']]={'context':context,'page':page,'expires':time.time()+60,'lock':AsyncMock()}
        try:
            response=self.client.get('/api/connection/login')
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['ready'])
            page.screenshot.assert_not_awaited()
            self.assertTrue(load_connection(self.user['id']))
        finally:
            pool.logins.pop(self.user['id'],None)

    def test_manual_login_input_is_sent_only_to_current_users_login_window(self):
        from app.browser import pool
        context=AsyncMock();context.cookies.return_value=[]
        page=AsyncMock()
        page.is_closed=Mock(return_value=False);context.pages=[page]
        pool.logins[self.user['id']]={'context':context,'page':page,'expires':time.time()+60,'lock':AsyncMock()}
        try:
            response=self.client.post('/api/connection/input',json={'text':'synthetic-code'})
            self.assertEqual(response.status_code,200)
            page.keyboard.insert_text.assert_awaited_once_with('synthetic-code')
            self.assertNotIn('synthetic-code',response.text)
            self.assertEqual(self.client.post('/api/connection/input',json={'key':'Control+L'}).status_code,422)
        finally:
            pool.logins.pop(self.user['id'],None)

    def test_login_diagnostics_have_no_screenshot_and_are_member_private(self):
        from app.browser import pool
        context=AsyncMock();context.cookies.return_value=[]
        page=AsyncMock();page.is_closed=Mock(return_value=False);context.pages=[page]
        pool.logins[self.user['id']]={'context':context,'page':page,'expires':time.time()+60,
                                    'lock':AsyncMock(),'phase':'verification',
                                    'qr':{'status':'scanned','code':2046,'http':200}}
        try:
            r=self.client.get('/api/connection/diagnostics')
            self.assertEqual(r.status_code,200)
            self.assertFalse(r.json()['ready']);self.assertEqual(r.json()['qr']['code'],2046)
            self.assertNotIn('image',r.json());page.screenshot.assert_not_awaited()
            anonymous=TestClient(app)
            self.assertEqual(anonymous.get('/api/connection/diagnostics').status_code,401)
            username='d'+uuid.uuid4().hex[:8]
            self.client.post('/api/users',json={'username':username,'password':PASSWORD})
            other=TestClient(app);other.post('/api/login',json={'username':username,'password':PASSWORD})
            self.assertEqual(other.get('/api/connection/diagnostics').status_code,400)
        finally:
            pool.logins.pop(self.user['id'],None)

    def test_cancel_retry_and_active_delete(self):
        job=self.job();url='/api/jobs/'+job['id']
        self.assertEqual(self.client.delete(url).status_code,409)
        self.client.post(url+'/cancel')
        worker.running_jobs.add(job['id'])
        self.assertEqual(self.client.post(url+'/retry').status_code,409)
        self.assertEqual(self.client.delete(url).status_code,409)
        worker.running_jobs.discard(job['id'])
        self.assertEqual(self.client.post(url+'/retry').status_code,200)

    def seed_file(self,job):
        item=uuid.uuid4().hex;file=uuid.uuid4().hex
        path=config.DATA/'jobs'/job['id']/item/'article.txt';path.parent.mkdir(parents=True);path.write_text('测试正文，不是真实文章')
        db.execute('INSERT INTO items(id,job_id,remote_id,title,kind,status) VALUES(?,?,?,?,?,?)',(item,job['id'],POST,'测试文章','article','done'))
        db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(file,job['id'],item,self.user['id'],str(path.relative_to(config.DATA)),'article.txt',path.stat().st_size,'text/plain'))
        db.update_job(job['id'],status='completed')
        return file,path

    def test_files_and_zip_export(self):
        job=self.job();file,path=self.seed_file(job)
        r=self.client.get('/api/files/'+file);self.assertEqual(r.status_code,200)
        self.assertIn('测试正文',r.text)
        other=TestClient(app);self.assertEqual(other.get('/api/files/'+file).status_code,401)
        username='f'+uuid.uuid4().hex[:8];self.client.post('/api/users',json={'username':username,'password':PASSWORD})
        other.post('/api/login',json={'username':username,'password':PASSWORD})
        self.assertEqual(other.get('/api/files/'+file).status_code,404)
        export=self.client.get(f'/api/jobs/{job["id"]}/export');self.assertEqual(export.status_code,200,export.text if export.status_code!=200 else '')
        with zipfile.ZipFile(io.BytesIO(export.content)) as z:
            self.assertIn('任务信息.json',z.namelist());self.assertTrue(any(n.endswith('/article.txt') for n in z.namelist()))
        self.assertEqual(list((config.DATA/'exports').glob('*.zip')),[])
        self.assertEqual(self.client.delete(f'/api/jobs/{job["id"]}').status_code,200)
        self.assertFalse(path.exists())

    def test_large_request_rejected(self):
        r=self.client.post('/api/connection/cookies',content='x'*270000,headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

    def test_restart_recovers_unfinished_task(self):
        job=self.job();db.update_job(job['id'],status='downloading')
        with TestClient(app) as restarted:
            self.assertEqual(db.query('SELECT status FROM jobs WHERE id=?',(job['id'],),one=True)['status'],'queued')

    def test_actual_audio_conversion_and_storage(self):
        if not shutil.which('ffmpeg'):self.skipTest('ffmpeg not installed')
        fixture=Path(TEMP.name)/'synthetic.mp4'
        subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','color=c=blue:s=160x90:d=0.5',
                        '-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(fixture)],check=True)
        job=self.job(video=True,audio=True);job['options']=json.dumps(job['options'])
        item=uuid.uuid4().hex
        db.execute('INSERT INTO items(id,job_id,remote_id,title,kind) VALUES(?,?,?,?,?)',(item,job['id'],POST,'合成测试视频','video'))
        post={'id':POST,'title':'合成测试视频','author':'测试夹具','kind':'video','url':f'https://www.douyin.com/video/{POST}','published':0,'video_urls':['fixture'],'cover_urls':[],'images':[],'audio_urls':[],'statistics':{}}
        async def fixture_download(urls,destination,*args,**kwargs):
            shutil.copyfile(fixture,destination);return destination.stat().st_size,'video/mp4'
        with patch('app.worker.download',side_effect=fixture_download):
            status,error=asyncio.run(worker.save_item(job,item,post))
        self.assertEqual(status,'done',error)
        files=db.query('SELECT * FROM files WHERE item_id=?',(item,));names={f['name'] for f in files}
        self.assertIn('video.mp4',names);self.assertIn('audio.mp3',names);self.assertIn('metadata.json',names)
        audio=next(f for f in files if f['name']=='audio.mp3')
        self.assertGreater(audio['size'],100)

    def test_article_exports_text(self):
        job=self.job();job['options']=json.dumps(job['options']);item=uuid.uuid4().hex
        db.execute('INSERT INTO items(id,job_id,remote_id,title,kind) VALUES(?,?,?,?,?)',(item,job['id'],POST,'测试文章','article'))
        post={'id':POST,'title':'测试文章','author':'测试作者','kind':'article','url':f'https://www.douyin.com/article/{POST}','published':0,'images':[],'text':'这里是合成的长文测试内容。','statistics':{},'text_source':'aweme_detail'}
        status,error=asyncio.run(worker.save_item(job,item,post));self.assertEqual(status,'done',error)
        files=db.query('SELECT name FROM files WHERE item_id=?',(item,));self.assertIn('article.md',{f['name'] for f in files})

    def retry_fixture(self,kind,name,content=b'previously saved fixture'):
        job=self.job();job['options']=json.dumps(job['options']);item=uuid.uuid4().hex;file=uuid.uuid4().hex
        root=config.DATA/'jobs'/job['id']/item;root.mkdir(parents=True)
        path=root/name;path.write_bytes(content)
        db.execute('INSERT INTO items(id,job_id,remote_id,title,kind,status) VALUES(?,?,?,?,?,?)',(item,job['id'],POST,'重试夹具',kind,'failed'))
        mime='video/mp4' if kind=='video' else 'image/jpeg' if kind=='image' else 'text/plain'
        db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(file,job['id'],item,job['user_id'],str(path.relative_to(config.DATA)),name,len(content),mime))
        post={'id':POST,'title':'重试夹具','author':'测试','kind':kind,'url':f'https://www.douyin.com/video/{POST}','published':0,'video_urls':['video-fixture'],'cover_urls':[],'images':[],'audio_urls':[],'statistics':{}}
        return job,item,file,path,post

    def test_retry_preserves_video_when_audio_extraction_fails(self):
        job,item,file,path,post=self.retry_fixture('video','video.mp4')
        original=path.read_bytes()
        async def failed_audio(source,target,check):
            target.write_bytes(b'incomplete audio')
            raise ValueError('synthetic extraction failure')
        with patch('app.worker.download',new=AsyncMock(side_effect=ValueError('synthetic network failure'))) as download,patch('app.worker.extract_audio',side_effect=failed_audio):
            status,_=asyncio.run(worker.save_item(job,item,post))
        self.assertEqual(status,'failed')
        self.assertTrue(path.exists(),'retry must keep the already saved video')
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.client.get('/api/files/'+file).content,original)
        download.assert_not_awaited()
        self.assertFalse((path.parent/'audio.mp3').exists())

    def test_retry_fetches_only_missing_images_and_keeps_download_links(self):
        job,item,file,path,post=self.retry_fixture('image','image_001.jpg')
        options=json.loads(job['options']);options['audio']=False;job['options']=json.dumps(options)
        post['images']=[['saved-image'],['missing-image']]
        with patch('app.worker.download',new=AsyncMock(side_effect=ValueError('synthetic failure'))) as download:
            status,_=asyncio.run(worker.save_item(job,item,post))
        self.assertEqual(status,'failed');self.assertTrue(path.exists())
        self.assertEqual([call.args[0] for call in download.await_args_list],[['missing-image']])
        async def fill_missing(urls,target,*args,**kwargs):
            self.assertEqual(urls,['missing-image']);target.write_bytes(b'new image');return 9,'image/jpeg'
        with patch('app.worker.download',side_effect=fill_missing):
            status,error=asyncio.run(worker.save_item(job,item,post))
        self.assertEqual(status,'done',error)
        self.assertEqual(self.client.get('/api/files/'+file).content,b'previously saved fixture')
        names=[r['name'] for r in db.query('SELECT name FROM files WHERE item_id=?',(item,))]
        self.assertEqual(names.count('image_001.jpg'),1);self.assertEqual(names.count('image_002.jpg'),1)
        self.assertEqual(names.count('metadata.json'),1);self.assertEqual(names.count('caption.txt'),1)

    def test_cancelled_retry_preserves_saved_images_and_cleans_staging(self):
        job,item,file,path,post=self.retry_fixture('image','image_001.jpg')
        post['images']=[['saved-image'],['missing-image']]
        async def cancel_download(urls,target,*args,**kwargs):
            target.write_bytes(b'partial');raise asyncio.CancelledError()
        with patch('app.worker.download',side_effect=cancel_download),self.assertRaises(asyncio.CancelledError):
            asyncio.run(worker.save_item(job,item,post))
        self.assertTrue(path.exists());self.assertEqual(self.client.get('/api/files/'+file).status_code,200)
        self.assertEqual([p.name for p in path.parent.iterdir()],['image_001.jpg'])

    def test_file_replacement_rolls_back_when_database_commit_fails(self):
        from app.storage import ItemStore
        job,item,file,path,_=self.retry_fixture('article','article.txt')
        store=ItemStore(job,item,lambda:False)
        real_connection=db.connection
        @contextmanager
        def fail_commit():
            with real_connection() as c:
                yield c
                raise ValueError('synthetic commit failure')
        with patch.object(store,'budget',return_value=10000),patch('app.storage.db.connection',new=fail_commit),self.assertRaisesRegex(ValueError,'commit failure'):
            store.write_text('article.txt','replacement text','text/plain')
        self.assertEqual(path.read_bytes(),b'previously saved fixture')
        self.assertEqual(self.client.get('/api/files/'+file).content,b'previously saved fixture')
        self.assertEqual([p.name for p in path.parent.iterdir()],['article.txt'])

    def test_quota_failure_does_not_overwrite_previous_text(self):
        from app.storage import ItemStore
        job,item,file,path,_=self.retry_fixture('article','article.txt')
        store=ItemStore(job,item,lambda:False)
        with patch('app.config.USER_QUOTA_MB',1),self.assertRaisesRegex(ValueError,'配额'):
            store.write_text('article.txt','x'*(2*1024*1024),'text/plain')
        self.assertEqual(path.read_bytes(),b'previously saved fixture')
        self.assertEqual(self.client.get('/api/files/'+file).status_code,200)
        self.assertEqual([p.name for p in path.parent.iterdir()],['article.txt'])

    def test_no_matching_types_reports_no_files(self):
        job=self.job(video=True,audio=False,images=False,articles=False)
        job['options']=json.dumps(job['options'])
        post=normalize({'aweme_id':POST,'desc':'合成图文','images':[{'url_list':['https://p.byteimg.com/test.jpg']}]})
        collector=AsyncMock();collector.discover.return_value=([POST],[]);collector.detail.return_value=post;collector.links={}
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector),patch('app.worker.asyncio.sleep',new=AsyncMock()):
            asyncio.run(worker.run_job(job))
        row=db.query('SELECT status,message FROM jobs WHERE id=?',(job['id'],),one=True)
        self.assertEqual(row['status'],'partial');self.assertIn('未保存文件',row['message'])

    def test_new_job_filters_saved_content_without_redownloading(self):
        old=self.job();self.seed_file(old)
        job=self.job();job['options']=json.dumps(job['options'])
        collector=AsyncMock();collector.links={};collector.filtered_ids={POST}
        async def discover(url,limit,exclude_ids=None):
            self.assertIn(POST,exclude_ids)
            return [],[]
        collector.discover.side_effect=discover
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector):
            asyncio.run(worker.run_job(job))
        collector.detail.assert_not_awaited()
        row=db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)
        self.assertEqual(row['status'],'completed');self.assertEqual(row['filtered'],1)
        self.assertIn('没有新作品',row['message'])
        self.assertEqual(len(self.client.get('/api/library').json()),1)

    def test_saved_id_filter_is_private_and_missing_files_are_retryable(self):
        old=self.job();_,path=self.seed_file(old)
        self.assertEqual(worker.saved_post_ids(self.user['id']),{POST})
        self.assertEqual(worker.saved_post_ids('another-user'),set())
        db.execute('UPDATE items SET status="failed" WHERE job_id=?',(old['id'],))
        self.assertEqual(worker.saved_post_ids(self.user['id']),set())
        db.execute('UPDATE items SET status="done" WHERE job_id=?',(old['id'],))
        path.unlink()
        self.assertEqual(worker.saved_post_ids(self.user['id']),set())

    def test_retry_rechecks_done_items_with_missing_registered_files(self):
        job=self.job()
        file_id,path=self.seed_file(job)
        db.update_job(job['id'],status='partial')
        path.unlink()
        self.assertEqual(self.client.post('/api/jobs/'+job['id']+'/retry').status_code,200)
        job=db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)
        collector=AsyncMock();collector.links={};collector.filtered_ids=set()
        collector.discover.return_value=([POST],[])
        collector.detail.return_value=normalize({'aweme_id':POST,'desc':'补齐夹具','article_info':{'article_content':{'markdown':'恢复的完整正文'}}})
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector),patch('app.worker.asyncio.sleep',new=AsyncMock()):
            asyncio.run(worker.run_job(job))
        collector.detail.assert_awaited_once_with(POST)
        self.assertEqual(self.client.get('/api/files/'+file_id).text,'恢复的完整正文')
        self.assertEqual(db.query('SELECT COUNT(*) n FROM items',one=True)['n'],1)
        self.assertEqual(db.query('SELECT status FROM jobs WHERE id=?',(job['id'],),one=True)['status'],'completed')

    def test_retry_keeps_intact_done_item_without_refetching(self):
        job=self.job();file_id,path=self.seed_file(job);original=path.read_bytes()
        job=db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)
        collector=AsyncMock();collector.links={};collector.filtered_ids=set()
        collector.discover.return_value=([POST],[])
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector):
            asyncio.run(worker.run_job(job))
        collector.detail.assert_not_awaited()
        self.assertEqual(self.client.get('/api/files/'+file_id).content,original)

    def test_export_prevents_retry_and_delete_until_response_cleanup(self):
        from app.main import export_job, retry_job, delete_job
        from fastapi import HTTPException
        import inspect
        job=self.job();file_id,path=self.seed_file(job)
        async def exercise():
            response=await export_job(job['id'],self.user)
            try:
                for action in (retry_job,delete_job):
                    with self.assertRaises(HTTPException) as caught:
                        result=action(job['id'],self.user)
                        if inspect.isawaitable(result):await result
                    self.assertEqual(caught.exception.status_code,409)
                self.assertTrue(path.exists())
            finally:
                await response.background()
            self.assertFalse(Path(response.path).exists())
            result=retry_job(job['id'],self.user)
            if inspect.isawaitable(result):await result
        asyncio.run(exercise())

    def test_invalid_export_range_releases_lock_and_temporary_file(self):
        from app.main import export_lock, exporting_jobs
        job=self.job();self.seed_file(job)
        for header,status in [('bytes=invalid',400),('bytes=99999999-',416)]:
            with self.subTest(header=header):
                response=self.client.get('/api/jobs/'+job['id']+'/export',headers={'Range':header})
                self.assertEqual(response.status_code,status)
                self.assertFalse(export_lock.locked());self.assertFalse(exporting_jobs)
                self.assertEqual(list((config.DATA/'exports').glob('*.zip')),[])
        self.assertEqual(self.client.get('/api/jobs/'+job['id']+'/export').status_code,200)

    def test_export_disconnect_releases_lock_and_temporary_file(self):
        from app.main import export_job, export_lock, exporting_jobs
        job=self.job();self.seed_file(job)
        async def exercise():
            response=await export_job(job['id'],self.user)
            async def disconnected(message):
                raise OSError('synthetic disconnect')
            with self.assertRaises(OSError):
                await asyncio.wait_for(response({'type':'http','method':'GET','headers':[],
                    'asgi':{'spec_version':'2.4'}},AsyncMock(),disconnected), timeout=3)
            self.assertFalse(export_lock.locked());self.assertFalse(exporting_jobs)
            self.assertFalse(Path(response.path).exists())
        asyncio.run(exercise())

    def test_export_legacy_disconnect_cancels_stream_and_releases_file(self):
        from app.main import export_job, export_lock, exporting_jobs
        job=self.job();self.seed_file(job)
        async def exercise():
            response=await export_job(job['id'],self.user)
            started=asyncio.Event()
            async def receive():
                await started.wait()
                return {'type':'http.disconnect'}
            async def send(message):
                started.set()
                await asyncio.Future()
            await asyncio.wait_for(response({'type':'http','method':'GET','headers':[],
                'asgi':{'spec_version':'2.3'}},receive,send),timeout=3)
            self.assertFalse(export_lock.locked());self.assertFalse(exporting_jobs)
            self.assertFalse(Path(response.path).exists())
        asyncio.run(exercise())

    def test_cancelled_export_waits_for_packaging_before_cleanup(self):
        from app.main import export_job, export_lock, exporting_jobs
        import threading
        job=self.job();self.seed_file(job)
        started,release=threading.Event(),threading.Event()
        real_zip=zipfile.ZipFile
        def paused_zip(*args,**kwargs):
            started.set()
            if not release.wait(3):raise RuntimeError('fixture timed out')
            return real_zip(*args,**kwargs)
        async def exercise():
            with patch('app.main.zipfile.ZipFile',side_effect=paused_zip):
                task=asyncio.create_task(export_job(job['id'],self.user))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait,2))
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertTrue(export_lock.locked())
                    self.assertIn(job['id'],exporting_jobs)
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):await task
            self.assertFalse(export_lock.locked());self.assertFalse(exporting_jobs)
            self.assertEqual(list((config.DATA/'exports').glob('*.zip')),[])
        asyncio.run(exercise())

    def test_filter_can_be_disabled_for_another_download(self):
        old=self.job();self.seed_file(old)
        job=self.job(skip_saved=False);job['options']=json.dumps(job['options'])
        collector=AsyncMock();collector.links={};collector.filtered_ids=set()
        collector.discover.return_value=([POST],[])
        collector.detail.return_value=normalize({'aweme_id':POST,'desc':'重复保存测试','video':{}})
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector),patch('app.worker.save_item',new=AsyncMock(return_value=('done',''))) as save,patch('app.worker.asyncio.sleep',new=AsyncMock()):
            asyncio.run(worker.run_job(job))
        collector.discover.assert_awaited_once_with(PROFILE,50,exclude_ids=set())
        save.assert_awaited_once()
        self.assertEqual(db.query('SELECT COUNT(*) n FROM items',one=True)['n'],2)

    def test_workers_serialize_one_member_but_allow_other_members(self):
        first=self.job(limit=1);second=self.job(limit=2)
        username='w'+uuid.uuid4().hex[:8]
        self.client.post('/api/users',json={'username':username,'password':PASSWORD})
        other=TestClient(app);other.post('/api/login',json={'username':username,'password':PASSWORD})
        third=other.post('/api/jobs',json={'source':PROFILE}).json()
        async def exercise():
            first_started=asyncio.Event();other_started=asyncio.Event();second_started=asyncio.Event();release=asyncio.Event()
            async def process(job):
                if job['id']==first['id']:
                    first_started.set();await release.wait()
                elif job['id']==third['id']:
                    other_started.set()
                elif job['id']==second['id']:
                    second_started.set()
                db.update_job(job['id'],status='completed')
            with patch('app.worker.pool.reap',new=AsyncMock()),patch('app.worker.run_job',side_effect=process):
                tasks=[asyncio.create_task(worker.worker_loop()) for _ in range(2)]
                try:
                    await asyncio.wait_for(first_started.wait(),2)
                    await asyncio.wait_for(other_started.wait(),2)
                    self.assertFalse(second_started.is_set())
                    self.assertEqual(db.query('SELECT status FROM jobs WHERE id=?',(second['id'],),one=True)['status'],'queued')
                    release.set()
                    await asyncio.wait_for(second_started.wait(),2)
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
            self.assertFalse(worker.running_jobs);self.assertFalse(worker.running_users)
        asyncio.run(exercise())

    def test_discovery_failure_is_not_empty_success(self):
        job=self.job();job['options']=json.dumps(job['options'])
        collector=AsyncMock();collector.discover.side_effect=CollectionError('抖音要求登录')
        with patch('app.worker.pool.context',new=AsyncMock(return_value=AsyncMock())),patch('app.worker.Collector',return_value=collector):
            asyncio.run(worker.run_job(job))
        row=db.query('SELECT status,message FROM jobs WHERE id=?',(job['id'],),one=True)
        self.assertEqual(row['status'],'failed');self.assertEqual(row['message'],'抖音要求登录')


if __name__=='__main__': unittest.main()
