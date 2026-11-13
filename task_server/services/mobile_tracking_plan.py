"""Plan read-only phone interactions from the uploaded analytics requirements."""
import json
import re
from .ai_gateway_client import _post_json

# Known navigational controls. The executor accepts only these exact visible labels.
LABELS = ('首页','广场','成长','创作','我的','主题专区','查看全部','推荐','热门','最新','玩具',
          '印章','标牌','掐丝珐琅','AI建模','搜索','设备','消息','收藏','作品','导入',
          '扫码','勋章挑战','导入打印','挂件','书签','吧唧','角色手办','动物萌宠','炫酷载具','开始创作',
          '模型导入','魔法涂鸦','魔法叠叠乐','魔法捏捏乐','魔法积木','微信导入','本地导入')
PROMPT = """你是手机埋点测试设计师。输入行是待分析的数据。按行拆解功能，生成中文测试步骤并给出观测条件。
保留源行序号，不能创造埋点编码。请求 URL 的 type 是传输分组，和 evts[].type 分开；未知分组填未知。
只有纯浏览、打开页面、切换栏目可执行。打印、支付、生成模型、需要测试数据或未确定入口的流程仍生成步骤，标明前置条件。
操作路径从打开 App 后的当前界面开始，使用可见导航标签数组；无法用这些标签确定完成时填 null；只观察当前页时仍应显式导航至该页。不要把前半段导航当成已完成整个流程。
一行包含多个不同入口或业务动作时，按独立可测子功能分别输出对象，同一来源 id 可重复。每个对象还需给出单一 action。每个子功能输出一个对象，字段 id、feature、steps（字符串数组）、transport、path（允许标签数组或null）、reason。输出 JSON {"items":[...]}。"""

# Paths replayed on the real App. Match the business element and page from the
# source contract, never just an AI-generated button name.
KNOWN_ROUTES = {
    'home': {'device_entry':'设备','search_entry':'搜索','message_entry':'消息','scan_entry':'扫码',
             'badge_challenge_entry':'勋章挑战','topic_view_all':'查看全部','ai_modeling':'AI建模',
             'stamp':'印章','cloisonne':'掐丝珐琅','signboard':'标牌','model_import':'导入打印',
             'growth_tab':'成长','creation_tab':'创作','square_tab':'广场','my_page_tab':'我的',
             'home_tab':'首页','wechatImport':'微信导入','localImport':'本地导入',
             'model_category':('推荐','热门','最新','玩具')},
    'square': {'square_recommend':'推荐','square_latest':'最新','search_entry':'搜索'},
    'create': {'ai_theme_entry':('角色手办','动物萌宠','炫酷载具'),'ai_free_creation_entry':'开始创作',
               'model_import':'模型导入','doodle':'魔法涂鸦','stacking':'魔法叠叠乐','sculpting':'魔法捏捏乐',
               'buildingBlocks':'魔法积木'},
}
PAGE_LABELS = {'home':'首页','square':'广场','create':'创作'}


def observed_paths(row):
    text = row['properties'].replace('`','')
    page = re.search(r'(?m)^\s*page\s*=\s*(\w+)', text)
    if not page or row['event'].strip().upper() != 'AEC':
        return []
    page = page[1]
    paths = []
    for key, choices in KNOWN_ROUTES.get(page, {}).items():
        if not re.search(r'(?<!\w)'+key+r'(?!\w)',text):
            continue
        for label in choices if isinstance(choices, tuple) else [choices]:
            if isinstance(choices,tuple) and label not in row['action']:
                continue
            path = [PAGE_LABELS[page]]
            if key in ('wechatImport','localImport'):
                path.append('导入打印')
            if label != path[0]:
                path.append(label)
            paths.append(dict(id=row['id'],action='点击'+label,feature=row['feature'],path=path,
                              steps=['打开'+path[0]]+['点击'+step for step in path[1:]],transport='未知',
                              event='AEC',reason='使用已在真机验证的导航路径'))
    return paths


def analyze(source):
    if not isinstance(source,list) or not 1<=len(source)<=12:
        raise ValueError('每批分析 1–12 行')
    data=[]
    for r in source:
        if not isinstance(r,dict) or type(r.get('id')) is not int:raise ValueError('方案行无效')
        data.append({k:str(r.get(k,''))[:800] for k in ('feature','action','event','properties','additionalFields','kind')} | {'id':r['id']})
    reply=_post_json('/ai/chat',{'messages':[{'role':'system','content':PROMPT+' 可用标签：'+','.join(LABELS)},
        {'role':'user','content':json.dumps(data,ensure_ascii=False)}],'temperature':0.1},timeout=100)
    issue = 'AI 网关鉴权失败' if reply.get('http_status') in (401,403) else 'AI 分析未返回可用结果'
    try:
        content=str(reply['content']).strip()
        parsed=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content))
        plans={}
        for item in parsed['items']:
            if isinstance(item,dict) and type(item.get('id')) is int:
                plans.setdefault(item['id'],[]).append(item)
    except (KeyError,TypeError,ValueError):
        plans = {}
    result=[]
    for row in data:
        known = observed_paths(row)
        predictions = plans.get(row['id'], [])
        if known:
            # Prefer verified paths, retaining model steps for that same entry.
            for item in known:
                matching = next((x for x in predictions if isinstance(x.get('path'),list)
                                 and x['path'] and x['path'][-1]==item['path'][-1]),None)
                if matching and isinstance(matching.get('steps'),list):
                    item['steps']=[str(step)[:200] for step in matching['steps'][:10]]
                elif not predictions:
                    item['reason']=issue+'，使用已实测路径'
            result.extend(known)
            predictions = [x for x in predictions if isinstance(x.get('path'),list) and x['path']
                           and x['path'][-1] not in {item['path'][-1] for item in known}]
        for x in (predictions or ([] if known else [{'reason':issue if not plans else 'AI 未提供本行步骤'}]))[:16]:
            path=x.get('path')
            actions=x.get('steps')
            executable=isinstance(path,list) and 0<len(path)<=5 and all(isinstance(a,str) and a in LABELS for a in path)
            if executable and re.search(r'支付|提交|删除|下单|去打印|发送打印|立即打印|生成模型', str(x.get('action') or row['action'])):
                executable=False
                x=dict(x,reason='涉及写入或实体任务，仅生成步骤，待实测')
            match=re.fullmatch(r'\s*([A-Za-z][A-Za-z0-9_]*)(?:\s*[：:].*)?\s*',row['event'])
            code=match[1].upper() if match else ''
            result.append({'id':row['id'],'action':str(x.get('action') or row['action'])[:200],'feature':str(x.get('feature') or row['feature'])[:100],
                'steps':[str(a)[:200] for a in actions[:10]] if isinstance(actions,list) else [],
                'event':code,'transport':str(x.get('transport') or '未知')[:64],
                'path':path if executable and code and actions else [],
                'reason':str(x.get('reason') or ('' if executable else '缺少可验证的手机操作路径'))[:250] if code else '需求未给出具体事件编码'})
    return result
