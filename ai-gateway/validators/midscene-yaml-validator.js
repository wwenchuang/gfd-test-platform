import yaml from 'js-yaml';

const ALLOWED_FLOW_ITEMS = new Set([
  'sleep', 'runAdbShell', 'launch', 'terminate',
  'aiTap', 'aiAction', 'aiAssert', 'aiWaitFor', 'aiInput', 'aiScroll',
  'aiHover', 'aiDoubleClick', 'aiRightClick', 'aiLocate', 'aiKeyboardPress',
  'recordToReport', 'ai'
]);
const FORBIDDEN_FLOW_ITEMS = new Set(['repeat', 'click', 'tap', 'wait', 'loop']);

function actionKeys(flowItem) {
  if (!flowItem || typeof flowItem !== 'object' || Array.isArray(flowItem)) return [];
  return Object.keys(flowItem).filter(key => !['timeout', 'errorMessage', 'name', 'deepLocate'].includes(key));
}

function validateFlowItem(flowItem, taskIndex, flowIndex, errors) {
  const label = `task 第 ${taskIndex + 1} 条 flow 第 ${flowIndex + 1} 步`;
  if (!flowItem || typeof flowItem !== 'object' || Array.isArray(flowItem)) {
    errors.push(`${label} 必须是对象`);
    return;
  }

  const keys = actionKeys(flowItem);
  if (!keys.length) {
    errors.push(`${label} 缺少动作名`);
    return;
  }
  if (keys.length > 1) {
    errors.push(`${label} 同时包含多个动作：${keys.join(', ')}`);
  }

  for (const key of keys) {
    if (FORBIDDEN_FLOW_ITEMS.has(key)) {
      errors.push(`禁止使用 ${key}，当前 Midscene 不支持该 flowItem`);
      continue;
    }
    if (!ALLOWED_FLOW_ITEMS.has(key)) {
      errors.push(`${label} 存在未知动作：${key}`);
      continue;
    }
    const value = flowItem[key];
    if (key === 'sleep' && typeof value !== 'number') {
      errors.push(`${label} 的 sleep 必须是数字`);
    }
    if (key !== 'sleep' && (!value || typeof value !== 'string')) {
      errors.push(`${label} 的 ${key} 描述必须是非空字符串`);
    }
  }
}

export function validateMidsceneYaml(yamlText) {
  const errors = [];
  let parsed;
  try {
    parsed = yaml.load(String(yamlText || ''));
  } catch (error) {
    return {
      success: true,
      valid: false,
      errors: [`YAML 解析失败：${error.message}`],
    };
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    errors.push('YAML 顶层必须是对象');
    return {success: true, valid: false, errors};
  }

  const platform = parsed.android ? 'android' : (parsed.ios ? 'ios' : '');
  if (!platform) {
    errors.push('顶层必须包含 android 或 ios');
    return {success: true, valid: false, errors};
  }

  const platformConfig = parsed[platform] || {};
  const tasks = platformConfig.tasks;
  if (!Array.isArray(tasks)) {
    errors.push(`${platform}.tasks 必须是数组`);
    return {success: true, valid: false, errors};
  }

  tasks.forEach((task, taskIndex) => {
    if (!task || typeof task !== 'object' || Array.isArray(task)) {
      errors.push(`task 第 ${taskIndex + 1} 条必须是对象`);
      return;
    }
    if (!task.name || typeof task.name !== 'string') {
      errors.push(`task 第 ${taskIndex + 1} 条必须包含 name`);
    }
    if (!Array.isArray(task.flow)) {
      errors.push(`task 第 ${taskIndex + 1} 条必须包含 flow 数组`);
      return;
    }
    task.flow.forEach((flowItem, flowIndex) => validateFlowItem(flowItem, taskIndex, flowIndex, errors));
  });

  return {
    success: true,
    valid: errors.length === 0,
    errors,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const valid = validateMidsceneYaml(`
android:
  tasks:
    - name: "关节龙打印"
      flow:
        - sleep: 1000
        - aiTap: "首页搜索入口"
        - aiAction: "输入关节龙并提交搜索"
        - aiAssert: "搜索结果展示关节龙模型"
`);
  const invalid = validateMidsceneYaml(`
android:
  tasks:
    - name: "错误用例"
      flow:
        - repeat: 3
        - click: "按钮"
        - sleep: "1000"
`);
  if (!valid.valid) {
    console.error(valid);
    process.exit(1);
  }
  if (invalid.valid || invalid.errors.length < 3) {
    console.error(invalid);
    process.exit(1);
  }
  console.log(JSON.stringify({ok: true, valid, invalid}, null, 2));
}
