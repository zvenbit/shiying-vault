// Run the real login polling code with deterministic HTTP responses and a tiny DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8');

function fixture(responses) {
  const elements = new Map();
  const timers = new Map();
  let sequence = 0;
  const document = {querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, {textContent: '', src: ''});
    return elements.get(selector);
  }};
  const requests = [];
  const context = vm.createContext({document, Date, Math, console,
    setInterval(fn) { const id = ++sequence; timers.set(id, fn); return id; },
    clearInterval(id) { timers.delete(id); },
    async fetch(url, options) {
      requests.push({url, options});
      const response = responses.shift();
      if (response instanceof Error) throw response;
      assert.ok(response, 'unexpected HTTP request');
      return {ok: response.status === 200, status: response.status, async json() { return response.body; }};
    }
  });
  vm.runInContext(source.slice(0, source.indexOf("document.addEventListener('click'")), context);
  vm.runInContext("refresh = async () => {}; modalMode = 'login'; loginTimer = setInterval(pollLogin, 4000);", context);
  return {context, timers, elements, requests, tick: () => vm.runInContext('pollLogin()', context)};
}

(async () => {
  const h = fixture([
    {status: 503, body: {detail: 'temporary screenshot failure'}},
    {status: 200, body: {ready: true, image: '', expires: Date.now()/1000 + 60}},
    {status: 200, body: {ok: true}}
  ]);
  await h.tick();
  assert.equal(h.timers.size, 1, 'a transient failure must not freeze the confirmation image');
  await h.tick();
  assert.equal(h.timers.size, 0, 'stop after verified success');
  assert.match(h.elements.get('#login-state').textContent, /连接成功/);
  assert.equal(h.elements.get('#login-browser').hidden, true, 'hide stale phone-confirmation image on success');
  const expired = fixture([{status: 400, body: {detail: '连接窗口已过期，请重新打开'}}]);
  await expired.tick();
  assert.equal(expired.timers.size, 0, 'expired sessions stop and prompt for a fresh window');
  assert.match(expired.elements.get('#login-state').textContent, /已过期/);
  const network = fixture([new Error('Failed to fetch')]);
  await network.tick();
  assert.equal(network.timers.size, 1, 'network interruptions remain retryable');
  const verify = fixture([{status:200,body:{ready:false,phase:'verification',message:'请完成身份验证',image:'fixture',expires:Date.now()/1000+300}}]);
  await verify.tick();
  assert.equal(verify.elements.get('#login-input-controls').open,true);
  assert.match(verify.elements.get('#login-state').textContent,/请完成身份验证/);
  assert.equal(verify.timers.size,1,'verification must continue polling for a real login cookie');
  const typing=fixture([{status:200,body:{ok:true}},{status:200,body:{ready:false,expires:Date.now()/1000+60}}]);
  await vm.runInContext("const entry=document.querySelector('#login-text');entry.value='synthetic-code';sendLoginInput({text:entry.value},entry)",typing.context);
  assert.equal(typing.elements.get('#login-text').value,'','clear local verification input after sending');
  assert.equal(typing.requests[0].url,'/api/connection/input');
  assert.deepEqual(JSON.parse(typing.requests[0].options.body),{text:'synthetic-code'});
  console.log('PASS: temporary failure recovers and confirmed login is displayed');
})().catch(error => {console.error(error); process.exitCode = 1;});
