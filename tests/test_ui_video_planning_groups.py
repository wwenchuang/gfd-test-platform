import copy
import pytest
from task_server.services import ui_video_planning_groups as groups


def payload(count=38, images=60):
    frames=[{'frame_id':f'f{i}', 'timestamp_ms':i*100} for i in range(images)]
    events=[{'candidate_index':i,'action':'aiInput','target':'field','value':'',
             'start_ms':int(i*(images-1)*100/count),'end_ms':int(i*(images-1)*100/count),
             'frame_refs':[frames[min(images-1,int(i*images/count))]['frame_id']],
             'ordinary_outcome_frame_ids':[], 'observation':'full unverified prose',
             'uncertainties':['original question'], 'ordinary_outcome_domain':{'start_ms':0}}
            for i in range(count)]
    return {'candidate_events':events,'frames':frames,'image_frames':frames,
            'required_candidate_indexes':list(range(count)), 'multiple_scenarios':False,
            'raw_uncertainties':['root'], 'baseline_examples':[{'title':'real Top3'}]}


def reply(group):
    return {'events':[{'candidate_index':i,'action':'aiInput','target':'field','value':'',
                      'observation':'visible','uncertainties':[]}
                     for i in group['planning_scope']['owned_candidate_indexes']],
            'uncertainties':[], 'multiple_scenarios':False}


def test_allocation_retains_full_global_context_and_is_detached():
    full=payload(); before=copy.deepcopy(full)
    allocated=groups.partition(full)
    assert len(allocated)==2
    assert all(len(g['image_frames'])<=40 for g in allocated)
    assert all(g['frames']==full['frames'] and g['candidate_events']==full['candidate_events'] for g in allocated)
    assert all(g['baseline_examples']==full['baseline_examples'] for g in allocated)
    assert set(f['frame_id'] for g in allocated for f in g['image_frames'])==set(f['frame_id'] for f in full['image_frames'])
    assert allocated==groups.partition(full)
    allocated[0]['candidate_events'][0]['uncertainties'].append('changed')
    assert full==before


@pytest.mark.parametrize('count,images,expected',[(1,2,1),(34,40,1),(35,40,2),(34,41,2),(68,60,2)])
def test_capacity(count,images,expected):
    assert len(groups.partition(payload(count,images)))==expected


def test_overflow_and_unrepresentable_cut_fail_locally():
    with pytest.raises(ValueError):groups.partition(payload(69))
    full=payload(); full['candidate_events'][0]['end_ms']=10000
    with pytest.raises(ValueError):groups.partition(full)


def test_merge_cross_group_omission_and_global_question_preservation():
    full=payload(); allocated=groups.partition(full); replies=list(map(reply,allocated))
    index=replies[1]['events'].pop(0)['candidate_index']
    replies[1]['omitted_retries']=[{'candidate_index':index,'repeated_by':0,'reason':'explicit failure'}]
    replies[0]['title']='first'; replies[1]['title']='alternate'
    merged=groups.merge(full,allocated,replies)
    assert merged['omitted_retries'][0]['repeated_by']==0
    assert merged['uncertainties']==['root'] and merged['title']=='first'
    merged['events'][0]['value']='changed'
    assert replies[0]['events'][0]['value']==''


@pytest.mark.parametrize('bad',[True,-1,999])
def test_ownership_rejects_boolean_and_foreign_indices(bad):
    group=groups.partition(payload())[1]; result=reply(group)
    result['events'][0]['candidate_index']=bad
    with pytest.raises(groups.PlanningReplyError):groups.validate_reply(group,result)


def test_required_completeness_is_global_and_late_attached_outcome_allowed():
    group=groups.partition(payload())[0]; result=reply(group)
    result['events']=result['events'][:1]
    result['events'][0]['outcome']={'observation':'late', 'frame_refs':[group['image_frames'][-1]['frame_id']]}
    groups.validate_reply(group,result)


def test_print_marker_owned_by_cancel_only():
    full=payload(); allocated=groups.partition(full); result=reply(allocated[1])
    cancel=result['events'][0]['candidate_index']
    result['print_recovery']={'submit':0,'cancel':cancel,'confirm':cancel+1,'end':37}
    groups.validate_reply(allocated[1],result)
    with pytest.raises(groups.PlanningReplyError):groups.validate_reply(allocated[0],result)


def test_shared_boundary_and_selected_action_anchors_are_carried():
    full=payload(38,60)
    # Freeze a representable synthetic boundary by making every other cut cross
    # a candidate envelope, while equality at this cut remains legal.
    for i,event in enumerate(full['candidate_events']):
        event['start_ms']=i*100;event['end_ms']=(i+1)*100
    allocated=groups.partition(full)
    cut=allocated[1]['planning_scope']['owned_candidate_indexes'][0]
    boundary=full['candidate_events'][cut]['start_ms']
    ref=next(f['frame_id'] for f in full['image_frames'] if f['timestamp_ms']==boundary)
    full['candidate_events'][cut-1]['ordinary_outcome_frame_ids']=[ref]
    full['candidate_events'][0]['frame_refs']=['f59','metadata-only-unselected']
    allocated=groups.partition(full)
    assert 'f59' in {f['frame_id'] for f in allocated[0]['image_frames']}
    assert all('metadata-only-unselected' not in {f['frame_id'] for f in g['image_frames']} for g in allocated)
    owner=next(g for g in allocated if cut-1 in g['planning_scope']['owned_candidate_indexes'])
    assert ref in {f['frame_id'] for f in owner['image_frames']}


@pytest.mark.parametrize('failure',[True,False])
def test_cross_group_retry_is_decided_by_real_global_review(failure):
    from task_server.services.ui_video_recovery import review_retries
    full=payload()
    for e in full['candidate_events']:
        e['action']='aiTap';e['target']='same control';e['observation']='visible'
    allocated=groups.partition(full);cut=allocated[1]['planning_scope']['owned_candidate_indexes'][0]
    full['candidate_events'][cut-1]['observation']='明确失败' if failure else 'visible'
    allocated=groups.partition(full);replies=list(map(reply,allocated))
    for result in replies:
        for e in result['events']:e['action']='aiTap'
    replies[1]['events'].pop(0)
    replies[1]['omitted_retries']=[{'candidate_index':cut,'repeated_by':cut-1,'reason':'failure retry'}]
    merged=groups.merge(full,allocated,replies)
    if failure:
        _,required,audit=review_retries(merged,full['candidate_events'],full['required_candidate_indexes'])
        assert cut not in required and audit[0]['repeated_by']==cut-1
    else:
        with pytest.raises(ValueError,match='video_retry_invalid'):
            review_retries(merged,full['candidate_events'],full['required_candidate_indexes'])


@pytest.mark.parametrize('mutation',['unknown','wrong_action','duplicate','out_of_order','outcome_bool','bad_marker','bad_value'])
def test_closed_reply_contract(mutation):
    group=groups.partition(payload())[0];result=reply(group)
    if mutation=='unknown':result['extra']='wrong'
    if mutation=='wrong_action':result['events'][0]['action']='aiTap'
    if mutation=='duplicate':result['events'].insert(1,copy.deepcopy(result['events'][0]))
    if mutation=='out_of_order':result['events'].reverse()
    if mutation=='outcome_bool':result['events'][0]['outcome']={'observation':'visible','frame_refs':[True]}
    if mutation=='bad_marker':result['print_recovery']={'cancel':0,'submit':True,'confirm':2,'end':3}
    if mutation=='bad_value':result['events'][0]['value']=True
    with pytest.raises(groups.PlanningReplyError):groups.validate_reply(group,result)


@pytest.mark.parametrize('terminal',[True,False])
def test_cross_group_print_marker_uses_real_prepare_and_bind(terminal):
    from task_server.services.ui_video_print_recovery import prepare_group,bind_group
    from task_server.services.ui_video_recovery import review_retries
    full=payload();cut=groups.partition(full)[1]['planning_scope']['owned_candidate_indexes'][0]
    for index,target in ((cut-1,'检查无误，确认打印'),(cut,'取消打印'),(cut+1,'确认')):
        full['candidate_events'][index].update(action='aiTap',target=target)
    full['candidate_events'][cut+2].update(action='aiAssert',target='终态',
        observation='本次任务卡片已移除' if terminal else '设备空闲')
    allocated=groups.partition(full);replies=list(map(reply,allocated))
    for result in replies:
        for e in result['events']:
            e['action']=full['candidate_events'][e['candidate_index']]['action']
    owner=next(i for i,g in enumerate(allocated) if cut in g['planning_scope']['owned_candidate_indexes'])
    replies[owner]['print_recovery']={'submit':cut-1,'cancel':cut,'confirm':cut+1,'end':cut+2}
    merged=groups.merge(full,allocated,replies)
    if terminal:
        cleaned,required,audit=review_retries(prepare_group(merged,full['candidate_events'],full['required_candidate_indexes']),
                                            full['candidate_events'],full['required_candidate_indexes'])
        assert bind_group(cleaned,full['candidate_events'])=={'submit':cut-1,'cancel':cut,'confirm':cut+1}
        assert cleaned['events'][-1]['candidate_index']==37
    else:
        with pytest.raises(ValueError,match='video_retry_invalid'):
            prepare_group(merged,full['candidate_events'],full['required_candidate_indexes'])


def test_selection_string_and_cross_group_reference_survive_merge():
    full=payload();allocated=groups.partition(full);cut=allocated[1]['planning_scope']['owned_candidate_indexes'][0]
    for i in (0,cut):full['candidate_events'][i]['action']='aiTap'
    allocated=groups.partition(full);replies=list(map(reply,allocated))
    for result in replies:
        for e in result['events']:
            e['action']=full['candidate_events'][e['candidate_index']]['action']
            if e['candidate_index'] in (0,cut):
                e.update(target='{{本次对象}}详情入口',reusable_expected='页面显示{{本次对象}}与{{保留对象}}均保留')
            if e['candidate_index']==0:e['selection']='仅本次创建的测试对象'
    merged=groups.merge(full,allocated,replies)
    # The helper must accept normal string selection, including later refs in
    # the other request. It never approves the final authorization itself.
    assert merged['events'][0]['selection']=='仅本次创建的测试对象'
    assert merged['events'][cut]['target']=='{{本次对象}}详情入口'
