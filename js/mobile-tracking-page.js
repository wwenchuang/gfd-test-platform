// First-level analytics workspace. Viewing a report does not require a phone.
window.MobileTrackingPage = (() => {
  let disposeCurrent = null, entry = 'reports';
  const add = (parent,tag,text='') => {
    const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;
  };
  function leave(){
    if(disposeCurrent)disposeCurrent();
    disposeCurrent=null;
  }
  function open(tab='reports'){
    entry=tab==='new'?'new':'reports';
    return activateWorkflow('mobile_tracking');
  }
  function mount(){
    if(activeWorkflow!=='mobile_tracking'||!canAccessWorkflow('mobile_tracking'))return false;
    const area=document.getElementById('editor-area');
    if(area.querySelector('[data-tracking-workspace]'))return true;
    leave();
    const identity=uiSimpleIdentity();
    document.getElementById('app').classList.add('ui-simple-active');
    area.classList.remove('editor-open');area.replaceChildren();toggleLibrary(false);
    const page=add(area,'main');page.className='tracking-workspace';page.dataset.trackingWorkspace='';
    const active=()=>page.isConnected&&activeWorkflow==='mobile_tracking'&&identity===uiSimpleIdentity()&&canAccessWorkflow('mobile_tracking');
    const head=add(page,'header');head.className='tracking-page-head';
    const title=add(head,'div');add(title,'h1','埋点测试');
    add(title,'p','查看手机上报的事件、接口回执和逐项测试结论。');
    const runAllowed=hasPermission('ui.execute');
    const nav=add(page,'nav');nav.className='tracking-view-nav';nav.setAttribute('aria-label','埋点测试视图');
    const reportsButton=add(nav,'button','实测报告');reportsButton.type='button';
    const newButton=runAllowed?add(nav,'button','新建测试'):null;if(newButton)newButton.type='button';
    const reports=add(page,'section');reports.dataset.trackingReports='';
    const setup=add(page,'section');setup.setAttribute('aria-label','新建测试');
    const history=window.TrackingHistory.mount(reports,active);
    let created=false;
    function choose(tab) {
      const isNew=tab==='new'&&runAllowed;
      reports.hidden=isNew;setup.hidden=!isNew;
      reportsButton.setAttribute('aria-pressed',String(!isNew));newButton?.setAttribute('aria-pressed',String(isNew));
      if(isNew&&!created){created=true;window.MobileTracking.attach(setup,true);}
    }
    reportsButton.onclick=()=>choose('reports');
    if(newButton)newButton.onclick=()=>choose('new');
    page.addEventListener('tracking-report-saved',()=>{if(active())history.refresh();});
    page.addEventListener('tracking-refresh',()=>{if(active()){history.refresh();Array.from(setup.querySelectorAll('button')).find(b=>b.textContent==='刷新设备')?.click();}});
    choose(entry);entry='reports';
    disposeCurrent=()=>{history();page.remove();document.getElementById('app')?.classList.remove('ui-simple-active');};
    return true;
  }
  return {open,mount,leave};
})();
