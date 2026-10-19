// Uploaded HTML is never executed: the server rebuilds data with a pinned renderer.
(() => {
  let active=null;
  function close(){const old=active;if(!old)return;active=null;old.controller?.abort();clearTimeout(old.timeout);old.dialog.querySelector('iframe')?.remove();old.dialog.close();old.dialog.remove();old.focus?.isConnected&&old.focus.focus();}
  function open(options){
    close();const dialog=document.createElement('dialog');dialog.className='ui-replay-dialog';dialog.setAttribute('aria-label','执行回放');
    dialog.innerHTML='<header><div><strong>执行回放</strong><p>按时间轴查看执行画面，点击步骤定位操作。</p></div><button type="button" class="btn-sm" data-replay-close>关闭</button></header><div class="ui-replay-toolbar"><label hidden data-replay-choice>执行记录 <select data-replay-select></select></label><button type="button" class="btn-sm" data-replay-retry>重新加载</button></div><p data-replay-message role="status">正在读取回放记录…</p><div data-replay-frame></div>';
    const state={dialog,focus:document.activeElement,version:0};active=state;document.body.append(dialog);dialog.showModal();
    const q=s=>dialog.querySelector(s),valid=()=>active===state&&dialog.isConnected&&options.isCurrent()!==false;
    q('[data-replay-close]').onclick=close;dialog.oncancel=e=>{e.preventDefault();close();};dialog.onclose=()=>{if(active===state)close();};
    const errors={replay_unavailable:'该记录没有可播放的 Midscene 数据，可到执行截图查看证据。',replay_data_invalid:'回放数据不完整，请联系管理员检查原始报告。',replay_image_invalid:'回放图片格式不支持，请查看执行截图。'};
    let artifacts=[];
    async function load(selected){
      state.controller?.abort();clearTimeout(state.timeout);const version=++state.version,controller=new AbortController();state.controller=controller;state.timeout=setTimeout(()=>controller.abort(),60000);
      const current=()=>valid()&&state.version===version;
      q('[data-replay-frame]').replaceChildren();q('[data-replay-message]').textContent='正在加载执行画面…';q('[data-replay-retry]').disabled=true;
      try{
        const base='/ui-executions/'+encodeURIComponent(options.batchId);
        if(!selected){const data=await options.request(base+'/evidence',{signal:controller.signal});if(!current())return;
          if(!data?.ok)throw new Error('回放列表读取失败，请重试。');
          artifacts=(data.artifacts||[]).filter(a=>a.kind==='html'&&/^[a-f0-9]{64}$/.test(a.artifact_id));
          if(!artifacts.length){q('[data-replay-message]').textContent='本次尚未上传可播放的回放报告，请稍后重新加载。';return;}
          const select=q('[data-replay-select]');select.replaceChildren();artifacts.forEach((a,i)=>{const o=document.createElement('option');o.value=a.artifact_id;o.textContent='执行记录 '+(i+1);select.append(o);});q('[data-replay-choice]').hidden=artifacts.length<2;selected=artifacts[0].artifact_id;
        }
        const result=await options.request(base+'/artifacts/'+selected+'/replay',{signal:controller.signal});if(!current())return;
        if(!result?.ok||typeof result.document!=='string')throw new Error(errors[result?.code]||'回放读取失败，请重试。');
        const frame=document.createElement('iframe');frame.title='Midscene 执行回放';frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');
        frame.onload=()=>{if(current())q('[data-replay-message]').textContent='点击 Play 播放，点击时间轴或步骤查看对应画面。';};
        frame.srcdoc=result.document;q('[data-replay-frame]').append(frame);
      }catch(e){if(current())q('[data-replay-message]').textContent=errors[e.code]||(e.name==='AbortError'?'回放加载超时，请重新加载。':e.message||'回放加载失败，请重试。');}
      finally{if(current()){clearTimeout(state.timeout);q('[data-replay-retry]').disabled=false;}}
    }
    q('[data-replay-select]').onchange=e=>load(e.target.value);q('[data-replay-retry]').onclick=()=>load();load();
  }
  window.UiExecutionReplay={open,close};
})();
