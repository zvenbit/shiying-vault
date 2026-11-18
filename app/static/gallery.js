'use strict';
const VaultGallery=(()=>{
 const $=selector=>document.querySelector(selector);
 let files=[],index=0,origin=null;
 const viewer=$('#image-viewer');
 function markup(images,compact=false){
  if(!images.length)return '';
  const thumbs=images.map((f,i)=>`<button type="button" class="gallery-thumb" data-gallery-index="${i}" aria-label="${compact?'放大':'查看'}第 ${i+1} 张图片"><img src="/api/files/${f.id}" alt="配图 ${i+1}" loading="lazy"></button>`).join('');
  return `<section class="image-gallery ${compact?'gallery-compact':''}" aria-label="作品图集"><div class="gallery-heading"><strong>配图 · ${images.length} 张</strong>${compact?'':'<span class="gallery-index"></span>'}</div>${compact?'':`<button type="button" class="gallery-hero" data-preview-image="${images[0].id}" aria-label="放大当前图片"><img class="gallery-image" src="/api/files/${images[0].id}" alt="配图 1"></button><div class="gallery-controls"><button type="button" class="button secondary small gallery-previous" data-gallery-step="-1">上一张</button><span class="muted">点击图片放大</span><button type="button" class="button secondary small gallery-next" data-gallery-step="1">下一张</button><a class="text-button gallery-download" href="/api/files/${images[0].id}?download=true" download>下载原图 ↓</a></div>`}<div class="gallery-thumbnails">${thumbs}</div></section>`;
 }
 function mount(images){files=images;index=0;paint();}
 function paint(){
  if(!files.length)return;
  const file=files[index], gallery=$('#modal .image-gallery');
  if(gallery){
   const img=gallery.querySelector('.gallery-image');
   if(img){img.src=`/api/files/${file.id}`;img.alt=`配图 ${index+1}`;gallery.querySelector('.gallery-hero').dataset.previewImage=file.id;}
   const count=gallery.querySelector('.gallery-index');if(count)count.textContent=`${index+1} / ${files.length}`;
   const previous=gallery.querySelector('.gallery-previous'),next=gallery.querySelector('.gallery-next');
   if(previous)previous.disabled=index===0;if(next)next.disabled=index===files.length-1;
   const download=gallery.querySelector('.gallery-download');if(download)download.href=`/api/files/${file.id}?download=true`;
   gallery.querySelectorAll('.gallery-thumb').forEach((el,i)=>{el.classList.toggle('selected',i===index);el.setAttribute('aria-current',String(i===index));});
  }
  if(viewer.open){
   $('#viewer-image').src=`/api/files/${file.id}`;$('#viewer-image').alt=`配图 ${index+1}`;
   $('#viewer-index').textContent=`${index+1} / ${files.length}`;
   $('#viewer-previous').disabled=index===0;$('#viewer-next').disabled=index===files.length-1;
   $('#viewer-download').href=`/api/files/${file.id}?download=true`;
   $('#image-stage').scrollTop=0;$('#image-stage').scrollLeft=0;
  }
 }
 function move(step){const next=index+step;if(next<0||next>=files.length)return;index=next;paint();}
 function open(id){
  const next=files.findIndex(f=>f.id===id);if(next<0)return;
  index=next;
  if(!viewer.open){origin={scrollTop:$('#modal').scrollTop,focus:document.activeElement};$('#image-stage').classList.remove('original-size');$('#viewer-zoom').textContent='查看原尺寸';viewer.showModal();}
  paint();
 }
 function clear(){origin=null;if(viewer.open)viewer.close();files=[];index=0;}
 function keydown(event){
  if(!event.target.closest?.('.image-gallery'))return false;
  event.preventDefault();if(!event.repeat)move(event.key==='ArrowLeft'?-1:1);return true;
 }
 document.addEventListener('click',event=>{
  const preview=event.target.closest('[data-preview-image]');
  if(preview&&preview.closest('#modal')){open(preview.dataset.previewImage);return;}
  const thumb=event.target.closest('[data-gallery-index]');
  if(thumb){index=Number(thumb.dataset.galleryIndex);paint();if(thumb.closest('.gallery-compact'))open(files[index].id);return;}
  const step=event.target.closest('[data-gallery-step]');if(step)move(Number(step.dataset.galleryStep));
 });
 $('#viewer-close').addEventListener('click',()=>viewer.close());
 $('#viewer-previous').addEventListener('click',()=>move(-1));$('#viewer-next').addEventListener('click',()=>move(1));
 $('#viewer-zoom').addEventListener('click',()=>{const original=$('#image-stage').classList.toggle('original-size');$('#viewer-zoom').textContent=original?'适应窗口':'查看原尺寸';});
 viewer.addEventListener('keydown',event=>{
  if(event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||event.isComposing)return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();event.stopPropagation();if(!event.repeat)move(event.key==='ArrowLeft'?-1:1);}
 });
 viewer.addEventListener('close',()=>{
  const previous=origin;origin=null;
  if(previous&&$('#modal').open){if(previous.focus?.isConnected)previous.focus.focus({preventScroll:true});$('#modal').scrollTop=previous.scrollTop;}
  $('#viewer-image').removeAttribute('src');
 });
 return {markup,mount,clear,keydown};
})();
