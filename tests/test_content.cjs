const assert=require('node:assert/strict');
const content=require('../app/static/content.js');
const item={title:'文章标题',author:'作者',metadata:{url:'https://www.douyin.com/article/1000000000000000001'}};
const images=[{id:'a'.repeat(32),name:'image_001.jpg'},{id:'b'.repeat(32),name:'image_002.png'}];
const prefix=`# ${item.title}\n\n作者：${item.author}\n\n来源：${item.metadata.url}\n\n`;
const html=content.renderArticle(prefix+'## 小节\n\n**重点**与*强调*\n\n> 引用\n\n- 项目一\n- 项目二\n\n![配图](image_002.png width=1920 height=1080)\n\n<br/>正文',item,images);
assert.match(html,/<h2>小节<\/h2>/);assert.match(html,/<strong>重点<\/strong>/);assert.match(html,/<blockquote>/);assert.match(html,/<ul>/);
assert.ok(!html.includes('作者：作者'));assert.ok(!html.includes('&lt;br'));
assert.match(html,new RegExp('/api/files/'+'b'.repeat(32)));assert.ok(!html.includes('width=1920'));
const dangerous=content.renderArticle('<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n[坏链接](javascript:alert(3))\n\n[内部路径](/api/logout)\n\n![外图](https://example.test/p.png)',item,[]);
assert.ok(!dangerous.includes('<script>'));assert.ok(!dangerous.includes('<img '));assert.ok(!dangerous.includes('href="javascript:'));assert.ok(!dangerous.includes('href="/api/'));assert.ok(!dangerous.includes('src="https://'));
const old=content.renderArticle('![缺失](https://example.test/1.jpg)\n\n![第二张](https://example.test/2.jpg)',item,[images[1]],false);
assert.match(old,/尚未保存/);assert.equal((old.match(/data-preview-image=/g)||[]).length,1);assert.ok(old.includes(images[1].id));
// In partially saved Markdown, a remaining remote URL is a missing image, not image 1.
const partial=content.renderArticle('![已保存](image_001.jpg)\n\n![缺失](https://example.test/2.jpg)',item,[images[0]]);
assert.equal((partial.match(/data-preview-image=/g)||[]).length,1);assert.match(partial,/尚未保存/);
assert.ok(content.title('长文案'.repeat(100)).length<=91);
console.log('PASS: article formatting, local image mapping, safe links and escaped HTML');
