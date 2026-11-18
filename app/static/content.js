(function(root,factory){
 if(typeof module==='object'&&module.exports)module.exports=factory(require('./vendor/markdown-it.min.js'));
 else root.VaultContent=factory(root.markdownit);
})(typeof globalThis!=='undefined'?globalThis:this,function(MarkdownIt){
 'use strict';
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function readable(text){return String(text??'').replace(/(?:<|&lt;)\s*br\s*\/?\s*(?:>|&gt;)/gi,'\n').replace(/\r\n?/g,'\n').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();}
 function title(text,max=90){const value=String(text??'').replace(/\s+/g,' ').trim();return value.length>max?value.slice(0,max)+'…':value;}
 function renderArticle(text,item,images,fromMarkdownFile=true){
  let source=String(text??'');
  const prefix=`# ${item.title.replace(/\n/g,' ')}\n\n作者：${item.author}\n\n来源：${item.metadata.url}\n\n`;
  if(fromMarkdownFile&&source.startsWith(prefix))source=source.slice(prefix.length);
  source=readable(source).replace(/(!\[[^\]]*\]\([^\s)]+)(?:\s+(?:width|height)=\d+)+\)/g,'$1)');
  const md=new MarkdownIt({html:false,breaks:true,linkify:false,typographer:false});
  const byName=new Map(images.map(f=>[f.name,f]));
  const remoteOrder=new Map();
  function imageFile(url){
   if(byName.has(url))return byName.get(url);
   // Legacy TXT files retain remote URLs. Match by original ordinal, never by array offset,
   // because a missing first image must not make the second image replace it.
   if(!fromMarkdownFile&&/^https?:\/\//i.test(url)){
    if(!remoteOrder.has(url))remoteOrder.set(url,remoteOrder.size+1);
    const stem=`image_${String(remoteOrder.get(url)).padStart(3,'0')}.`;
    return images.find(f=>f.name.startsWith(stem));
   }
  }
  md.renderer.rules.image=(tokens,index)=>{
   const token=tokens[index], file=imageFile(token.attrGet('src')||'');
   if(!file||!/^[a-f\d]{32}$/i.test(file.id))return '<span class="missing-image">［此配图尚未保存］</span>';
   const number=images.indexOf(file)+1, alt=token.content||`配图 ${number}`;
   return `<button type="button" class="article-image" data-preview-image="${file.id}" aria-label="放大配图 ${number}"><img src="/api/files/${file.id}" alt="${escape(alt)}" loading="lazy"></button>`;
  };
  const tokens=md.parse(source,{});
  function safeLinks(list){
   const tags=[];
   for(const token of list){
    if(token.type==='link_open'){
     let allowed=false;
     try{const u=new URL(token.attrGet('href'));allowed=['https:','http:'].includes(u.protocol)&&!u.username&&!u.password;}catch{}
     tags.push(allowed);
     if(allowed){token.attrSet('target','_blank');token.attrSet('rel','noopener noreferrer');}
     else{token.tag='span';token.attrs=null;}
    }else if(token.type==='link_close'&&!tags.pop())token.tag='span';
    if(token.children)safeLinks(token.children);
   }
  }
  safeLinks(tokens);
  return md.renderer.render(tokens,md.options,{});
 }
 return {readable,title,renderArticle};
});
