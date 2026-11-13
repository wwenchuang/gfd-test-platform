// Compare only events owned by the completed action. Keep the wire code visible.
window.MobileTrackingReport = (() => {
  const pending='待实测', difference='差异待确认';
  const wireCode=value=>String(value||'').split(/[：:]/,1)[0].trim().toUpperCase();
  const canonical=code=>({CCZ:'CC',CSZ:'CS'}[code]||code);
  const fields=new Set('page pageTitle ele eleTitle tab subTab topicId topicName element elementId modelType modelName resourceType resourceId resourceName modelId firstExposurePosition exposurePage pagePre keyword existResult functionUse tabClick listClick position channel source duration actDuration traceId requestId listRequestId rank favoriteType videoId contentId mediaType playDuration watchDuration isTop clickArea searchWord contentName videoName browseId batchId viewBatchId'.split(' '));
  const privateNames=new Set(['keyword','topicName','modelName','resourceName','traceId','requestId','listRequestId','searchWord','contentName','videoName','browseId','batchId','viewBatchId']);
  function publicObject(item){
    return Object.fromEntries(Object.entries(item||{}).filter(([k,v])=>fields.has(k) && ['string','number','boolean'].includes(typeof v)).map(([k,v])=>{
      const text=String(v).slice(0,160);
      return [k,(privateNames.has(k)&&text&&!/^已脱敏/.test(text))||/https?:\/\/|@|(?:^|\D)1[3-9]\d{9}(?:\D|$)/.test(text)?'已脱敏':text];
    }));
  }
  function sanitize(e,group,ids,endpoint){
    const time=n=>Number.isFinite(n)&&n>0&&n<1e11?n:null;
    const short=s=>String(s||'').slice(0,100);
    let address='';
    try{
      const url=new URL(endpoint?.includes('://')?endpoint:'https://'+endpoint);
      if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password&&endpoint)address=url.origin+url.pathname;
    }catch{}
    return {group,ids,endpoint:address,method:e.method==='POST'?'POST':'',reqId:/^[A-Za-z0-9_-]{1,96}$/.test(e.reqId||'')?e.reqId:'',
      index:Number.isInteger(e.index)?e.index:null,time:time(e.time),sent:time(e.sent),event:short(e.event),name:short(e.name),
      requestType:short(e.requestType),contents:(Array.isArray(e.contents)&&e.contents.length?e.contents:[e.fields]).map(publicObject),
      httpStatus:Number.isInteger(e.httpStatus)?e.httpStatus:null,businessCode:Number.isInteger(e.businessCode)?e.businessCode:null,
      ack:['已接收','上报失败'].includes(e.ack)?e.ack:'回执未识别',responseMessage:['success','ok','成功'].includes(e.responseMessage)?e.responseMessage:''};
  }
  function variants(rule){
    const pairs=Array.from((rule[3]||'').matchAll(/\(["']([\w-]+)["'],\s*["']([^"']+)["']\)/g),m=>m[2]);
    const action=rule[0]||'';
    if(pairs.length){const named=pairs.filter(label=>action.includes(label));return named.length?named:pairs;}
    const named=Array.from(action.matchAll(/[「“]([^」”]+)[」”]/g),m=>m[1]);
    if(named.length>1 && !action.includes('或'))return named;
    if(!/[、或]/.test(action)||!/点击/.test(action))return [];
    if(action.includes('或'))return []; // alternative ways of activating one control
    return action.replace(/^.*?点击/,'').split('、').map(s=>s.replace(/[「」“”]/g,'').replace(/等分类.*$/,'').trim()).filter(Boolean);
  }
  function requirements(rule,label){
    const text=rule[3]||'',expected={},unresolved=[],exposureContext=[];
    for(const raw of text.split(/[；;\n]/)){
      const line=raw.replaceAll('`','').trim().replace(/[,，]$/,'').trim();
      if(!line)continue;
      const pair=line.match(/^\(["']([\w-]+)["'],\s*["']([^"']+)["']\)$/);
      if(pair){if(pair[2]===label)Object.assign(expected,{ele:pair[1],eleTitle:pair[2]});continue;}
      const term=line.match(/^([a-zA-Z]\w*)\s*=\s*([^=]+)$/)||line.match(/^"(\w+)":\s*"([^"]+)"$/);
      if(term && ((term[1]==='pagePre' && term[2]==='上一级页面') || (term[1]==='exposurePage' && term[2]==='当前曝光页面')))exposureContext.push(term[1]);
      if(!term||/被点击|当前展示|按实际|搜索词|点击时|类型编码|上一级页面|当前曝光页面|当前选中|add\/remove|（/.test(term[2]))unresolved.push(line);
      else expected[term[1]]=term[2].trim();
    }
    if(text.includes('(')&&/\(["']/.test(text)&&expected.ele===undefined)unresolved.push('尚未确定当前入口对应的元素枚举');
    return {expected,unresolved,exposureContext};
  }
  function judge(rule,plan,captured,ambiguous){
    const result=(status,reason,e=null,actual=null)=>({status,reason,e,actual});
    if(!plan?.done)return result(pending,plan?.reason||'尚未执行手机操作');
    if(ambiguous)return result(pending,'同一操作路径对应多个相同事件条件，需区分触发动作');
    const type=wireCode(rule[1]);
    if(!/^[A-Z][\w]*$/.test(type))return result(pending,'需求未指定可核对的事件编码');
    const label=plan.path.at(-1),{expected,unresolved,exposureContext}=requirements(rule,label);
    const terms=Object.entries(expected);
    const options=[];
    for(const e of captured.filter(x=>x.ids.includes(plan.id))){
      const actualType=wireCode(e.event);
      if(canonical(type)!==canonical(actualType))continue;
      for(const obj of e.contents.length?e.contents:[{}]){
        const matched=terms.filter(([k,v])=>obj[k]===v).length;
        const identity=['ele','firstExposurePosition','resourceType'].some(key=>expected[key] && obj[key]===expected[key]) ||
          (type==='AV' && expected.page && obj.page===expected.page);
        if(terms.length && !matched && !identity)continue;
        const exactPosition=canonical(type)==='CS' && !!expected.firstExposurePosition && obj.firstExposurePosition===expected.firstExposurePosition;
        const received=e.ack==='已接收' && e.httpStatus>=200 && e.httpStatus<300 && e.businessCode===0 && !!(e.reqId&&e.method&&e.time&&e.sent&&e.endpoint);
        const missingContext=exactPosition && received ? exposureContext.filter(k=>!String(obj[k]??'').trim()) : [];
        options.push({e,obj,matched,identity,missingContext});
      }
    }
    if(!options.length)return result(pending,'已执行操作，窗口内未找到与该需求对应的事件');
    // An explicitly rejected delivery remains visible even when a retry succeeds.
    options.sort((a,b)=>b.matched-a.matched||Number(b.e.ack==='上报失败')-Number(a.e.ack==='上报失败')||b.missingContext.length-a.missingContext.length);
    const {e,obj,matched,identity,missingContext}=options[0];
    if(matched<terms.length){
      const mismatch=terms.filter(([k,v])=>obj[k]!==v).map(([k,v])=>k+' 预期 '+v+'，实收 '+(obj[k]??'未见')).join('；');
      return result(identity?difference:pending,mismatch+(identity?'':'；待确认事件归属'),e,obj);
    }
    if(!e.reqId||!e.endpoint||!e.method||!e.time||!e.sent||e.httpStatus==null||e.businessCode==null)return result(pending,'缺少可关联的请求、时间或业务回执，需更新 Runner 后复测',e,obj);
    if(rule[2] && missingContext.length)return result(difference,'实收 '+e.event+' 的首展位 '+expected.firstExposurePosition+' 已匹配，缺少方案必填字段 '+missingContext.join('、'),e,obj);
    if(unresolved.length || (!terms.length && rule[5]))return result(pending,'已收到 '+e.event+'，待核对 '+(unresolved.join('；')||'原表未明示的内容或触发条件'),e,obj);
    if(rule[2] && e.ack==='上报失败')return result('失败','业务字段匹配，上报请求未被成功接收',e,obj);
    if(!rule[2])return result('失败','实际出现了方案要求不出现的事件',e,obj);
    if(rule[6])return result(pending,'基础字段匹配；还需核对 '+rule[6],e,obj);
    if(e.ack!=='已接收'||e.httpStatus<200||e.httpStatus>=300||e.businessCode!==0)return result(pending,'未取得一致的成功回执',e,obj);
    return result('通过',(type===wireCode(e.event)?'':'原表 '+type+' 与实收 '+e.event+' 已按同一事件核对。')+'需求字段匹配，HTTP '+e.httpStatus+'，业务码 0',e,obj);
  }
  function report(rules,plans,events){
    const details=plans.map(p=>{
      const rule=rules[p.source];
      const ambiguous=plans.some(q=>q.id!==p.id && q.batch && q.batch===p.batch && canonical(wireCode(rules[q.source][1]))===canonical(wireCode(rule[1])) && rules[q.source][3]===rule[3]);
      return {plan:p,...judge(rule,p,events,ambiguous)};
    });
    const sources=rules.map((rule,source)=>{
      const parts=details.filter(d=>d.plan.source===source);
      const missing=variants(rule).filter(label=>!parts.some(d=>d.plan.done && d.plan.path.at(-1)===label));
      const status=parts.some(d=>d.status==='失败')?'失败':parts.some(d=>d.status===difference)?difference:parts.length && !missing.length && parts.every(d=>d.status==='通过')?'通过':pending;
      const reason=[...new Set(parts.map(d=>d.reason))];
      if(missing.length)reason.push('尚未验证入口：'+missing.join('、'));
      if(!reason.length)reason.push('尚未生成或执行步骤');
      return {rule,parts,status,reason:reason.join('；')};
    });
    return {sources,details};
  }
  const when=value=>value?new Date(value*1000).toISOString().replace('T',' ').replace('Z',' UTC'):'未提供';
  function evidenceImage(events){
    const e=events[0],canvas=document.createElement('canvas');
    const context=canvas.getContext('2d'),width=920,font='17px sans-serif';
    context.font=font;const lines=[];
    function line(value){
      let row='';
      for(const char of String(value)){if(row && context.measureText(row+char).width>width-48){lines.push(row);row='';}row+=char;}
      lines.push(row);
    }
    line('请求编号  '+e.reqId+'    '+when(e.sent));line(e.method+'  '+e.endpoint+(e.requestType?'?type='+encodeURIComponent(e.requestType):''));
    line('HTTP '+(e.httpStatus??'未知')+'    业务码 '+(e.businessCode??'未知')+'    '+e.responseMessage+'    '+e.ack);
    for(const item of events.slice(0,5)){line(item.event+'  '+item.name+'    '+when(item.time));for(const content of item.contents.slice(0,3))line(JSON.stringify(content));}
    if(lines.length>60 || events.length>5||events.some(item=>item.contents.length>3)){
      lines.length=Math.min(lines.length,60);
      line('图内为部分字段，完整保留的观测数据见抓包事件表。');
    }
    canvas.width=width;canvas.height=90+lines.length*26;
    context.fillStyle='#ffffff';context.fillRect(0,0,width,canvas.height);context.fillStyle='#18394a';context.fillRect(0,0,width,54);
    context.fillStyle='#ffffff';context.font='bold 22px sans-serif';context.fillText('接口上报与回执 · 脱敏摘录',24,35);
    context.fillStyle='#203847';context.font=font;lines.forEach((value,i)=>context.fillText(value,24,83+i*26));
    return {base64:canvas.toDataURL('image/png'),width,height:canvas.height};
  }
  return {report,sanitize,evidenceImage,when};
})();
