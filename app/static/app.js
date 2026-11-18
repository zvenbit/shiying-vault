'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = n => `<svg aria-hidden="true"><use href="#i-${n}"/></svg>`;
const names = {home:'采集工作台',library:'我的内容库',jobs:'任务记录',settings:'账号与连接',members:'成员管理'};
const kinds = {video:'视频',image:'图文',article:'文章',unknown:'待识别'};
const states = {queued:['排队中','working'],discovering:['正在读取','working'],downloading:['保存中','working'],completed:['已完成','ok'],partial:['部分完成','warn'],failed:['获取失败','error'],cancelled:['已取消','']};
const active = new Set(['queued','discovering','downloading']);
let user = null, jobs = [], library = [], view = 'home', filter = 'all', refreshing = false;
let loginTimer = null, loginBusy = false, toastTimer = null, modalMode = '', selectedJob = null;
let itemReturn = null, itemSequence = null, taskItems = [], modalRevision = 0;

function bytes(n) {if (!n) return '0 B'; const i = Math.min(3,Math.floor(Math.log(n)/Math.log(1024))); return `${(n/1024**i).toFixed(i ? 1 : 0)} ${['B','KB','MB','GB'][i]}`;}
function date(n) {return new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
function badge(status) {const [t,c] = states[status] || [status,'']; return `<span class="badge ${c}">${esc(t)}</span>`;}
function toast(message,error=false) {clearTimeout(toastTimer); const el=$('#toast'); const host=$('#image-viewer').open?$('#image-viewer'):$('#modal').open?$('#modal'):document.body;host.appendChild(el);el.textContent=message; el.className=`toast ${error?'error':''}`; el.hidden=false; toastTimer=setTimeout(()=>el.hidden=true,4500);}
async function api(path,method='GET',data) {
 const response=await fetch(`/api${path}`,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,credentials:'same-origin'});
 let result; try {result=await response.json();} catch {result={detail:'服务暂时无法响应，请稍后再试'};}
 if (!response.ok) {if(response.status===401 && path!=='/login') showAuth(); const detail=typeof result.detail==='string'?result.detail:'输入内容不符合要求，请检查后重试'; const error=new Error(detail); error.status=response.status; throw error;}
 return result;
}
function showAuth() {VaultGallery.clear();user=null; $('#auth').hidden=false; $('#shell').hidden=true; if($('#modal').open) $('#modal').close(); stopLogin();}
function showApp() {$('#auth').hidden=true; $('#shell').hidden=false; $('#username').textContent=user.username; $('#avatar').textContent=user.username.slice(0,1).toUpperCase(); $('#role').textContent=user.role==='admin'?'空间管理员':'空间成员'; $('#members-nav').hidden=user.role!=='admin'; $('#limit').max=user.max_items; $('#limit').value=Math.min(Number($('#limit').value),user.max_items); updateStats();}
function updateStats() {
 if(!user) return;
 $('#library-count').textContent=user.total_items; $('#stat-items').textContent=user.total_items;
 $('#stat-active').textContent=jobs.filter(j=>active.has(j.status)).length; $('#stat-size').textContent=bytes(user.usage);
 const percent=Math.min(100,Math.round(user.usage/user.quota*100)); $('#storage-bar').value=percent; $('#storage-percent').textContent=`${percent}%`; $('#storage-text').textContent=`${bytes(user.usage)} / ${bytes(user.quota)}`;
 $('#connection-label').textContent=user.connection?'已保存抖音连接':'访客模式'; $('#connection-dot').classList.toggle('connected',!!user.connection);
 $('#connection-description').textContent=user.connection?`已加密保存登录信息 · 更新于 ${date(user.connection.updated)}。有效性将在采集时验证。`:'未连接，可以先用访客模式尝试采集。';
 $('#disconnect').disabled=!user.connection;
}
function setView(next) {
 if(!names[next] || next==='members' && user?.role!=='admin') return;
 view=next; $$('.view').forEach(el=>el.hidden=el.id!==`view-${next}`); $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===next)); $('#page-title').textContent=names[next];
 if(next==='library') renderLibrary(); if(next==='members') loadMembers(); window.scrollTo({top:0,behavior:'instant'});
}
function empty(title,description,type='folder') {return `<div class="empty">${icon(type)}<div><h3>${esc(title)}</h3><p>${esc(description)}</p></div></div>`;}
function jobRow(job) {
 const progress=job.discovered?Math.round((job.finished+job.failed)/job.discovered*100):0;
 let title=job.url; const item=library.find(i=>i.job_id===job.id && i.author); if(item) title=`${item.author} · ${job.url.includes('/user/')||job.discovered>1?'博主作品':'单个作品'}`;
 return `<article class="job-row"><div class="job-icon">${icon('folder')}</div><div class="job-info"><h3 title="${esc(job.url)}">${esc(title)}</h3><p>${esc(job.message)}</p><div class="job-meta">${badge(job.status)}<span class="muted">${job.finished}/${job.discovered} 已处理${job.failed?` · ${job.failed} 个失败`:''}</span>${active.has(job.status)?`<progress aria-label="任务进度" class="job-progress" value="${progress}" max="100"></progress>`:''}<span class="muted">${date(job.created)}</span></div></div><div class="job-actions"><button class="button secondary small" data-job="${job.id}" data-action="detail">详情</button>${active.has(job.status)?`<button class="button secondary small" data-job="${job.id}" data-action="cancel">取消</button>`:`<button class="button secondary small" data-job="${job.id}" data-action="export">导出</button>${job.status!=='completed'?`<button class="button secondary small" data-job="${job.id}" data-action="retry">重试</button>`:''}<button class="text-button danger" data-job="${job.id}" data-action="delete">删除</button>`}</div></article>`;
}
function renderJobs() {
 $('#recent-count').textContent=jobs.length;
 $('#recent-jobs').innerHTML=jobs.length?`<div class="job-list">${jobs.slice(0,3).map(jobRow).join('')}</div>`:empty('还没有采集任务','贴入第一个链接，把喜欢的内容留在这里。');
 $('#all-jobs').innerHTML=jobs.length?`<div class="job-list">${jobs.map(jobRow).join('')}</div>`:empty('你的任务记录将在这里出现','创建采集任务后，可以随时查看进度和下载结果。','clock');
}
function renderLibrary() {
 const query=$('#library-search').value.trim().toLowerCase();
 const items=library.filter(i=>(filter==='all'||i.kind===filter) && `${i.title} ${i.author}`.toLowerCase().includes(query));
 $('#library-grid').innerHTML=items.length?items.map(item=>{
  const cover=item.files.find(f=>f.mime.startsWith('image/'));
  return `<button class="content-card" data-item="${item.id}"><div class="card-cover">${cover?`<img loading="lazy" src="/api/files/${cover.id}" alt="${esc(item.title.slice(0,40))}">`:icon(item.kind==='article'?'article':item.kind==='image'?'image':'video')}<span class="badge">${kinds[item.kind]||'内容'}${item.status==='failed'?' · 部分文件':''}</span></div><div class="card-info"><h3>${esc(item.title)}</h3><p><span>${esc(item.author||'作者信息未提供')}</span><span>${item.files.length} 个文件</span></p></div></button>`;
 }).join(''):empty(query||filter!=='all'?'没有找到匹配的内容':'你的内容库，等第一份收藏',query||filter!=='all'?'换个关键词或内容类型试试。':'采集完成后，视频、音频、图片和文章会出现在这里。');
}
async function refresh() {
 if(!user||refreshing) return; refreshing=true;
 try {const result=await Promise.all([api('/me'),api('/jobs'),api('/library')]); [user,jobs,library]=result; updateStats(); renderJobs(); if(view==='library')renderLibrary(); if(modalMode==='job'&&selectedJob) await showJob(selectedJob,false);}
 catch(error) {if(user) toast(error.message,true);} finally {refreshing=false;}
}
function openModal(title,html,mode) {VaultGallery.clear();modalRevision++;modalMode=mode; if(mode!=='item'){itemReturn=null;itemSequence=null;} $('#modal-title').textContent=title; $('#modal-content').innerHTML=html; if(!$('#modal').open) $('#modal').showModal(); $('#modal').scrollTop=0;}
function taskItem(item) {
 const saved=item.files.length>0;
 const status={done:'已保存',failed:'未完整保存',skipped:'已按类型跳过',pending:'处理中'}[item.status];
 const content=`<strong class="task-item-title">${esc(VaultContent.title(item.title))}</strong><span class="task-item-meta">${esc(item.author)} · ${kinds[item.kind]||'内容'} · ${status}${saved?` · ${item.files.length} 个文件`:''}</span>${item.error?`<span class="task-item-meta danger">${esc(item.error)}</span>`:''}${saved?'<span class="task-item-link">查看详情 →</span>':''}`;
 return saved?`<button type="button" class="task-item task-item-open" data-item="${item.id}" aria-label="查看详情：${esc(item.title.slice(0,90))}">${content}</button>`:`<div class="task-item">${content}</div>`;
}
async function showJob(id,open=true) {
 const revision=modalRevision;
 const job=await api(`/jobs/${id}`);
 // A response started before entering content (or closing the dialog) must not replace it.
 if(revision!==modalRevision || !open && (modalMode!=='job'||selectedJob!==id))return;
 selectedJob=id;
 taskItems=job.items;
 const content=`<p class="muted">${esc(job.url)}</p><div class="job-meta">${badge(job.status)}<span class="muted">${esc(job.message)}</span></div>${job.warnings.map(w=>`<p class="modal-warning">${esc(w)}</p>`).join('')}<div class="task-items">${job.items.length?job.items.map(taskItem).join(''):`<p class="muted">${job.filtered?'没有新作品，已有内容可在「我的内容库」查看。':active.has(job.status)?'正在等待作品列表；尚未保存文件。':'本次没有保存作品。'}</p>`}</div>`;
 if(open) openModal('采集任务详情',content,'job');
 else {
  const scrollTop=$('#modal').scrollTop, focusedItem=document.activeElement?.dataset.item;
  $('#modal-content').innerHTML=content;
  if(focusedItem)focusTaskItem(focusedItem);
  $('#modal').scrollTop=scrollTop;
 }
}
function focusTaskItem(id) {$$('#modal-content .task-item[data-item]').find(el=>el.dataset.item===id)?.focus({preventScroll:true});}
function returnToTask() {
 if(modalMode!=='item'||!itemReturn)return;
 const previous=itemReturn;
 selectedJob=previous.jobId;
 taskItems=previous.items;
 openModal('采集任务详情',previous.html,'job');
 focusTaskItem(previous.itemId);
 $('#modal').scrollTop=previous.scrollTop;
}
async function switchItem(offset) {
 if(modalMode!=='item'||!itemSequence)return;
 const next=itemSequence.items[itemSequence.index+offset];
 if(!next)return;
 try {await showItem(next.id,{sequence:itemSequence,previous:itemReturn,focus:offset<0?'previous-item':'next-item'});}
 catch(error){toast(error.message,true);}
}
function contentKeydown(event) {
 if(modalMode!=='item'||!$('#modal').open||event.defaultPrevented||event.isComposing||event.keyCode===229
    ||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||document.fullscreenElement)return;
 if(event.key!=='ArrowLeft'&&event.key!=='ArrowRight')return;
 // Native media controls and editable fields retain their own arrow-key behavior.
 if(event.composedPath().some(node=>node instanceof Element&&(node.isContentEditable||
    node.matches('input,textarea,select,video,audio,[role="textbox"],[role="combobox"],[role="slider"],[role="spinbutton"]'))))return;
 if(VaultGallery.keydown(event))return;
 event.preventDefault();
 if(!event.repeat)switchItem(event.key==='ArrowLeft'?-1:1);
}
async function showItem(id,navigation=null) {
 const revision=modalRevision;
 const previous=navigation?navigation.previous:modalMode==='job'&&selectedJob?{jobId:selectedJob,itemId:id,items:taskItems,html:$('#modal-content').innerHTML,scrollTop:$('#modal').scrollTop}:null;
 let sequence=navigation?.sequence||(previous?{jobId:previous.jobId,items:previous.items.filter(i=>i.files.length)}:null);
 let item=sequence?.items.find(i=>i.id===id)||library.find(i=>i.id===id);
 if(!item) {await refresh(); item=library.find(i=>i.id===id);} if(!item)return;
 // Library ordering spans tasks; use this task's own order for previous/next navigation.
 if(!sequence){const job=await api(`/jobs/${item.job_id}`);sequence={jobId:job.id,items:job.items.filter(i=>i.files.length)};item=sequence.items.find(i=>i.id===id);}
 if(revision!==modalRevision)return;
 if(!item)throw new Error('此内容已不在当前任务中，请刷新列表后重试。');
 itemReturn=previous;
 itemSequence={...sequence,index:sequence.items.findIndex(i=>i.id===id)};
 const video=item.files.find(f=>f.mime.startsWith('video/'));
 const audio=item.files.find(f=>f.mime.startsWith('audio/'));
 const images=item.files.filter(f=>f.mime.startsWith('image/')).sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));
 const article=item.files.find(f=>f.name==='article.md')||item.files.find(f=>f.name==='article.txt');
 const textFile=article||item.files.find(f=>f.name==='caption.txt');
 const original=/^https:\/\/www\.douyin\.com\/(video|note|article)\/\d+$/.test(item.metadata.url||'')?`<a class="text-button" href="${esc(item.metadata.url)}" target="_blank" rel="noopener noreferrer">查看原作品 ${icon('out')}</a>`:'';
 const heading=VaultContent.title(item.title);
 const pagination=`<div class="content-pagination" role="group" aria-label="同一任务内容切换"><button type="button" id="previous-item" class="button secondary small" aria-keyshortcuts="ArrowLeft" title="上一个（←）" ${itemSequence.index===0?'disabled':''}>上一个</button><span class="item-position" aria-live="polite">${itemSequence.index+1} / ${itemSequence.items.length}</span><button type="button" id="next-item" class="button secondary small" aria-keyshortcuts="ArrowRight" title="下一个（→）" ${itemSequence.index===itemSequence.items.length-1?'disabled':''}>下一个</button></div>`;
 const toolbar=`<div class="content-actions">${previous?'<button type="button" id="back-to-task" class="button secondary small" aria-keyshortcuts="Escape" title="返回任务详情（Esc）">← 返回任务详情</button>':''}${pagination}<button type="button" id="copy-content" class="button secondary small" disabled>一键复制内容</button><small class="content-shortcuts">← / → 切换内容 · Esc ${previous?'返回任务详情':'关闭详情'}</small></div>`;
 const media=video?`<video class="media-preview" src="/api/files/${video.id}" controls preload="metadata"></video>`:!article?VaultGallery.markup(images):'';
 const body=article?'<article class="article-body" id="content-text" aria-label="文章正文">正在读取正文…</article>':'<div class="caption-preview" id="content-text">正在读取文案…</div>';
 openModal(heading,`<p class="muted">${esc(item.author)} · ${kinds[item.kind]||'内容'} · ${item.files.length} 个文件</p>${toolbar}${item.error?`<p class="modal-warning">${esc(item.error)}</p>`:''}${media}${audio?`<audio class="wide" src="/api/files/${audio.id}" controls preload="metadata"></audio>`:''}${body}${article?VaultGallery.markup(images,true):''}${original}<div class="item-files">${item.files.map(f=>`<a class="file-link" href="/api/files/${f.id}?download=true" download><span>${esc(f.name)}</span><small>${bytes(f.size)} ↓</small></a>`).join('')}</div>`,'item');
 VaultGallery.mount(images);
 const back=$('#back-to-task');
 if(back)back.addEventListener('click',returnToTask);
 $('#previous-item').addEventListener('click',()=>switchItem(-1));
 $('#next-item').addEventListener('click',()=>switchItem(1));
 const focus=navigation?[$(`#${navigation.focus}`),$('#previous-item'),$('#next-item'),back].find(el=>el&&!el.disabled):back;
 focus?.focus({preventScroll:true});
 const preview=$('#content-text'), copy=$('#copy-content');
 copy.addEventListener('click',()=>copyContent(preview.innerText));
 try {
  let text=item.title;
  if(textFile){const response=await fetch(`/api/files/${textFile.id}`);if(!response.ok)throw new Error('内容读取失败');text=await response.text();}
  if(modalMode==='item'&&$('#content-text')===preview){
   if(article)preview.innerHTML=VaultContent.renderArticle(text,item,images,article.name==='article.md');
   else preview.textContent=readableContent(text);
   copy.disabled=!preview.innerText.trim();
  }
 } catch(error) {if($('#content-text')===preview)preview.textContent='内容读取失败，请关闭后重试。';}
}
function readableContent(text) {
 return String(text??'').replace(/(?:<|&lt;)\s*br\s*\/?\s*(?:>|&gt;)/gi,'\n')
  .replace(/\r\n?/g,'\n').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
}
async function copyContent(text) {
 try {
  if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');
  await navigator.clipboard.writeText(text);
 } catch(error) {
  // HTTP deployments may lack the modern Clipboard API; keep the user-initiated fallback inside the dialog.
  const field=document.createElement('textarea'), focused=document.activeElement;
  field.value=text;field.readOnly=true;field.style.position='fixed';field.style.opacity='0';
  $('#modal-content').appendChild(field);field.focus();field.select();
  let copied=false;
  try {copied=document.execCommand('copy');} catch {}
  finally {field.remove();focused?.focus();}
  if(!copied){toast('浏览器未允许复制，请选中正文手动复制。',true);return false;}
 }
 toast('内容已复制');return true;
}
async function downloadJob(id,button) {
 button.disabled=true; const old=button.textContent;button.textContent='打包中…';
 try {
  // Let the browser stream the archive to disk; do not allocate a multi-GB Blob in JavaScript.
  const response=await fetch(`/api/jobs/${id}`); if(!response.ok) throw new Error('任务不可访问');
  const job=await response.json(); if(!job.items.some(i=>i.files.length)) throw new Error('此任务还没有可导出的文件');
  const link=document.createElement('a');link.href=`/api/jobs/${id}/export`;link.download=`拾映_${id.slice(0,8)}.zip`; document.body.appendChild(link);link.click();link.remove();toast('已请求打包下载，请查看浏览器下载列表');
 } catch(e) {toast(e.message,true);} finally {button.disabled=false;button.textContent=old;}
}
async function loadMembers() {try {const members=await api('/users');$('#member-list').innerHTML=members.map(m=>`<div class="member-row"><span>${esc(m.username)}</span><span class="badge">${m.role==='admin'?'管理员':'成员'}</span></div>`).join('');}catch(e){toast(e.message,true);}}

async function pollLogin() {
 if(loginBusy||modalMode!=='login') return; loginBusy=true;
 try {const state=await api('/connection/login'); if(modalMode!=='login') return;
  const img=$('#login-browser'); if(img&&state.image) {img.src=`data:image/jpeg;base64,${state.image}`;img.hidden=false;}
  const controls=$('#login-input-controls');
  if(state.ready) {if(img)img.hidden=true;if(controls)controls.hidden=true; $('#login-state').textContent='连接成功，登录信息已加密保存。可以关闭窗口开始采集。'; stopLogin(); await api('/connection/close','POST').catch(()=>{}); await refresh().catch(()=>{}); return true;}
  if(controls&&state.phase==='verification')controls.open=true;
  $('#login-state').textContent=`${state.message||'请在下方页面选择扫码登录，用抖音扫码。如遇验证，可直接点击或拖动页面。'} 窗口将在 ${Math.max(0,Math.ceil((state.expires-Date.now()/1000)/60))} 分钟后关闭。`;
 } catch(e) {
  if(modalMode!=='login')return;
  const terminal=[400,401,404,410].includes(e.status);
  if($('#login-state')) $('#login-state').textContent=terminal?e.message:'连接状态暂时读取失败，正在自动重试，请稍候…';
  if(terminal) {const img=$('#login-browser');if(img)img.hidden=true;const controls=$('#login-input-controls');if(controls)controls.hidden=true;stopLogin();}
 } finally {loginBusy=false;}
}
function stopLogin() {if(loginTimer)clearInterval(loginTimer); loginTimer=null;}
async function sendLoginInput(payload, input) {
 const button=$('#login-type-send');if(button?.disabled)return;
 if(button)button.disabled=true;
 try {
  const request=api('/connection/input','POST',payload);
  if(input)input.value='';
  await request;await pollLogin();
 } catch(error) {toast(error.message,true);}
 finally {if(button)button.disabled=false;}
}
async function startLogin() {
 openModal('连接你的抖音账号','<div class="busy-inline"><span class="spinner"></span>正在打开独立登录窗口，首次可能需要几十秒…</div>','login');
 try {await api('/connection/login','POST');if(modalMode!=='login')return;
 $('#modal-content').innerHTML=`<p class="login-description" id="login-state">正在加载扫码页面…</p>
 <img id="login-browser" class="login-browser" alt="抖音独立登录窗口，可点击和拖动" draggable="false">
 <details id="login-input-controls" class="login-controls"><summary>手动输入手机号或验证码</summary>
 <p class="help-text">先点击上方抖音画面中的输入框，再在这里填写并点击「输入到抖音」。填写后请在抖音页面手动确认。</p>
 <form id="login-input-form" autocomplete="off"><label for="login-text">要输入的内容</label>
 <div class="login-input-row"><input id="login-text" type="password" autocomplete="off" maxlength="128" required placeholder="手机号或验证码，不会在这里留存"><button id="login-type-send" type="submit" class="button secondary">输入到抖音</button></div></form>
 <div class="login-key-row"><button type="button" class="button secondary small" data-login-key="Tab">下一输入框</button><button type="button" class="button secondary small" data-login-key="Backspace">退格</button><button type="button" class="button secondary small" data-login-key="Enter">确认键</button></div></details>
 <p class="help-text">此窗口只用于连接你自己的账号；身份验证由你本人完成。登录有效期由抖音决定。</p>`;
 $('#login-input-form').addEventListener('submit',event=>{event.preventDefault();const input=$('#login-text');if(input.value)sendLoginInput({text:input.value},input);});
 $$('#login-input-controls [data-login-key]').forEach(button=>button.addEventListener('click',()=>sendLoginInput({key:button.dataset.loginKey})));
 let start=null;const img=$('#login-browser');
 function pos(e){const r=img.getBoundingClientRect();return {x:Math.max(0,Math.min(1200,(e.clientX-r.left)/r.width*1200)),y:Math.max(0,Math.min(800,(e.clientY-r.top)/r.height*800))};}
 img.addEventListener('pointerdown',e=>{start=pos(e); img.setPointerCapture(e.pointerId);e.preventDefault();});
 img.addEventListener('pointerup',async e=>{if(!start)return;const end=pos(e);const data={...start};if(Math.hypot(end.x-start.x,end.y-start.y)>8){data.end_x=end.x;data.end_y=end.y;}start=null;try{await api('/connection/action','POST',data);await pollLogin();}catch(err){toast(err.message,true);}});
 img.addEventListener('pointercancel',()=>start=null);const ready=await pollLogin();if(modalMode==='login'&&!ready) loginTimer=setInterval(pollLogin,4000);
 } catch(e){$('#modal-content').innerHTML=`<p class="modal-warning">${esc(e.message)}</p><p class="help-text">可以关闭窗口，在「使用已有 Cookie 连接」中保存自己的登录信息。</p>`;}
}

document.addEventListener('click',async e=>{
 const nav=e.target.closest('[data-view]');if(nav){setView(nav.dataset.view);return;}
 const ft=e.target.closest('[data-filter]');if(ft){filter=ft.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('selected',b===ft));renderLibrary();return;}
 const item=e.target.closest('[data-item]');if(item){try{await showItem(item.dataset.item);}catch(err){toast(err.message,true);}return;}
 const action=e.target.closest('[data-action]');if(!action)return;
 const {job:id,action:act}=action.dataset;
 try {
  if(act==='detail')return await showJob(id);
  if(act==='export')return await downloadJob(id,action);
  if(act==='delete'){if(!confirm('删除这个任务及其所有已保存文件？此操作无法撤销。'))return;await api(`/jobs/${id}`,'DELETE');}
  else await api(`/jobs/${id}/${act}`,'POST');
  await refresh();toast(act==='retry'?'已加入重试队列':act==='cancel'?'已请求取消任务':'任务已删除');
 }catch(err){toast(err.message,true);}
});
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('button',e.target);b.disabled=true;$('#login-error').textContent='';try{await api('/login','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();user=await api('/me');showApp();await refresh();}catch(err){$('#login-error').textContent=err.message;}finally{b.disabled=false;}});
$('#collect-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('#collect-button');button.disabled=true;$('#collect-error').textContent='';const f=new FormData(e.target);const data={source:f.get('source'),limit:Number(f.get('limit'))};for(const k of ['video','audio','images','articles','skip_saved'])data[k]=f.has(k);try{await api('/jobs','POST',data);toast('采集任务已加入队列');await refresh();}catch(err){$('#collect-error').textContent=err.message;}finally{button.disabled=false;}});
$('#select-all').addEventListener('click',()=>{$$('#collect-form .format-options input[type="checkbox"]').forEach(i=>i.checked=true);});
$('#logout').addEventListener('click',async()=>{try{await api('/logout','POST');showAuth();}catch(e){toast(e.message,true);}});
$('#connection-shortcut').addEventListener('click',()=>setView('settings'));
$('#library-search').addEventListener('input',renderLibrary);
$('#open-login').addEventListener('click',startLogin);
$('#modal-close').addEventListener('click',()=>$('#modal').close());
$('#modal').addEventListener('keydown',contentKeydown);
$('#modal').addEventListener('cancel',event=>{if(modalMode==='item'&&itemReturn){event.preventDefault();returnToTask();}});
$('#modal').addEventListener('close',()=>{if(modalMode==='login'){stopLogin();api('/connection/close','POST').catch(()=>{});}VaultGallery.clear();modalRevision++;modalMode='';selectedJob=null;taskItems=[];itemReturn=null;itemSequence=null;$('#modal-content').innerHTML='';});
$('#disconnect').addEventListener('click',async()=>{if(!confirm('清除保存的抖音登录信息？已保存的作品不受影响。'))return;try{await api('/connection','DELETE');await refresh();toast('已清除抖音连接');}catch(e){toast(e.message,true);}});
$('#cookie-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('button',e.target);b.disabled=true;try{await api('/connection/cookies','POST',{cookies:$('#cookie-value').value});$('#cookie-value').value='';await refresh();toast('连接信息已加密保存');}catch(err){toast(err.message,true);}finally{b.disabled=false;}});
$('#password-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/password','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();showAuth();toast('密码已更新，请重新登录');}catch(err){toast(err.message,true);}});
$('#member-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/users','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();await loadMembers();toast('成员账号已创建');}catch(err){toast(err.message,true);}});
setInterval(()=>{if(!document.hidden)refresh();},3500);
(async()=>{try{user=await api('/me');showApp();await refresh();}catch{showAuth();}})();
