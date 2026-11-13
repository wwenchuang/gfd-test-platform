// Patch only the original DOCX parts that receive report text or receipt pictures.
window.TrackingWord = (() => {
  const w='http://schemas.openxmlformats.org/wordprocessingml/2006/main';
  const rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
  const wp='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing';
  const a='http://schemas.openxmlformats.org/drawingml/2006/main';
  const pic='http://schemas.openxmlformats.org/drawingml/2006/picture';
  const nodes=(el,name)=>Array.from(el.getElementsByTagNameNS('*',name));
  const put=(root,ns,tag,attrs={})=>{
    const el=root.ownerDocument.createElementNS(ns,tag);
    for(const [key,val] of Object.entries(attrs))el.setAttribute(key,String(val));
    root.append(el);return el;
  };
  async function xml(zip,path){
    const file=zip.file(path);if(!file)throw Error('原文件缺少 '+path);
    const doc=new DOMParser().parseFromString(await file.async('string'),'application/xml');
    if(nodes(doc,'parsererror').length)throw Error('原文件 XML 无法解析');
    return doc;
  }
  function serialize(zip,path,doc){zip.file(path,new XMLSerializer().serializeToString(doc));}
  function paragraph(doc,text){
    const p=doc.createElementNS(w,'w:p');
    put(put(p,w,'w:r'),w,'w:t').textContent=text;return p;
  }
  function picture(doc,rid,n,width,height){
    const p=doc.createElementNS(w,'w:p'), drawing=put(put(p,w,'w:r'),w,'w:drawing');
    const scale=Math.min(5000000/width,5100000/height),size={cx:Math.round(width*scale),cy:Math.round(height*scale)};
    const inline=put(drawing,wp,'wp:inline');put(inline,wp,'wp:extent',size);put(inline,wp,'wp:docPr',{id:n,name:'接口上报 '+n});
    const data=put(put(inline,a,'a:graphic'),a,'a:graphicData',{uri:pic});
    const image=put(data,pic,'pic:pic'),nv=put(image,pic,'pic:nvPicPr');
    put(nv,pic,'pic:cNvPr',{id:n,name:'接口回执'});put(nv,pic,'pic:cNvPicPr');
    const fill=put(image,pic,'pic:blipFill');put(fill,a,'a:blip').setAttributeNS(rel,'r:embed',rid);
    put(put(fill,a,'a:stretch'),a,'a:fillRect');
    const sp=put(image,pic,'pic:spPr'),transform=put(sp,a,'a:xfrm');
    put(transform,a,'a:off',{x:0,y:0});put(transform,a,'a:ext',size);
    put(put(sp,a,'a:prstGeom',{prst:'rect'}),a,'a:avLst');return p;
  }
  async function download(file,entries){
    const zip=await JSZip.loadAsync(await file.arrayBuffer());
    const path='word/document.xml',rp='word/_rels/document.xml.rels',ct='[Content_Types].xml';
    const doc=await xml(zip,path),rels=await xml(zip,rp),types=await xml(zip,ct);
    if(!nodes(types,'Default').some(x=>x.getAttribute('Extension')?.toLowerCase()==='png')){
      put(types.documentElement,types.documentElement.namespaceURI,'Default',{Extension:'png',ContentType:'image/png'});
    }
    const targets=new Map(nodes(rels,'Relationship').map(e=>[e.getAttribute('Id'),e.getAttribute('Target')]));
    const anchors=new Map();
    for(const p of nodes(doc,'p')){
      for(const ole of nodes(p,'OLEObject')){
        const target=targets.get(ole.getAttributeNS(rel,'id'));
        if(target)anchors.set(target.split('/').pop(),p);
      }
    }
    const tables=nodes(doc,'tbl').filter(t=>nodes(t,'t').some(e=>e.textContent.trim()==='第一展示位'));
    const body=nodes(doc,'body')[0];
    let serial=Math.max(0,...nodes(doc,'docPr').map(e=>Number(e.getAttribute('id'))||0));
    const media=new Map(), sections=new Map();
    for(const entry of entries){
      const parts=String(entry.origin||'').match(/^(.*) \/ (.*) 第 (\d+) 行$/);
      const anchor=parts&&anchors.get(parts[1]);
      const row=parts && parts[1]===file.name && tables.length===1
        ? Array.from(tables[0].children).filter(e=>e.localName==='tr')[Number(parts[3])-1] : null;
      const cell=row && Array.from(row.children).filter(e=>e.localName==='tc').at(-1);
      const key=cell||anchor||body;
      if(!sections.has(key))sections.set(key,doc.createDocumentFragment());
      const fragment=sections.get(key);
      fragment.append(paragraph(doc,[entry.origin,entry.action,entry.status,entry.reason].filter(Boolean).join('　')));
      for(const receipt of entry.receipts||[]){
        if(!receipt.png)continue;
        const encoded=receipt.png.replace(/^data:image\/png;base64,/,''),raw=atob(encoded.slice(0,32));
        if(raw.slice(1,4)!=='PNG'||raw.length<24)throw Error('接口证据图片无效');
        const size=offset=>Array.from(raw.slice(offset,offset+4)).reduce((v,c)=>v*256+c.charCodeAt(0),0);
        let rid=media.get(receipt.id);
        if(!rid){
          do{rid='trackingReceipt'+(++serial);}while(targets.has(rid)||zip.file('word/media/'+rid+'.png'));
          zip.file('word/media/'+rid+'.png',encoded,{base64:true});
          put(rels.documentElement,rels.documentElement.namespaceURI,'Relationship',{Id:rid,Target:'media/'+rid+'.png',Type:rel+'/image'});
          media.set(receipt.id,rid);
        }
        fragment.append(picture(doc,rid,++serial,size(16),size(20)));
      }
    }
    for(const [target,fragment] of sections){
      if(target===body)body.insertBefore(fragment,Array.from(body.children).find(x=>x.localName==='sectPr')||null);
      else if(target.localName==='tc')target.append(fragment);
      else target.after(fragment);
    }
    serialize(zip,path,doc);serialize(zip,rp,rels);serialize(zip,ct,types);
    const blob=await zip.generateAsync({type:'blob',compression:'DEFLATE'});
    const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;
    link.download=file.name.replace(/\.docx$/i,'')+'-埋点实测.docx';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  return {download};
})();
