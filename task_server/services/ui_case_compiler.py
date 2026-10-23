"""Pure, fail-closed compilation of a versioned UI case for one resolved target."""

import copy
import hashlib
import math

import yaml

from task_server.schemas import MIDSCENE_FLOW_ACTIONS

from .yaml_static_validator import STEP_ATTR_KEYS, validate_midscene_action_parameters
from .ui_dynamic_object import PREFIX, uses_dynamic_object
from . import ui_print_recovery as print_recovery


PORTABLE_ACTIONS = ("launch", "aiTap", "aiWaitFor", "aiAssert", "aiScroll")


def _portable_scroll_valid(step):
    # Keep this strict subset in parity with the standalone Mac Runner gate.
    if (set(step)-{'aiScroll','direction','distance','scrollType'} or
        not isinstance(step.get('aiScroll'),str) or not step['aiScroll'].strip() or
        validate_midscene_action_parameters('aiScroll',step)):
        return False
    if 'scrollType' in step and step['scrollType'] not in (
            'singleAction','scrollToTop','scrollToBottom','scrollToLeft','scrollToRight'):
        return False
    if step.get('scrollType','singleAction')=='singleAction':
        if step.get('direction') not in ('up','down','left','right'):
            return False
    elif 'direction' in step or 'distance' in step:
        return False
    if 'distance' in step:
        value=step['distance']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<value<=1.7976931348623157e308 or not math.isfinite(value):
            return False
    return True


class _UniqueKeysLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise yaml.constructor.ConstructorError(None, None, "duplicate YAML key", key_node.start_mark)
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compile_ui_case(case_version, target_resolution):
    """Return an immutable execution copy and source step provenance; never run a device."""
    def blocked(code, unsupported=None):
        return {"ok": False, "block_code": code, "yaml_content": "", "compiled_hash": "",
                "source_hash": source_hash, "step_map": {}, "required_actions": [],
                "unsupported_steps": unsupported or []}

    source = case_version.get("yaml_content") if isinstance(case_version, dict) else None
    source_hash = _sha256(source) if isinstance(source, str) else ""
    if not isinstance(source, str) or not source.strip():
        return blocked("source_invalid")
    if not isinstance(target_resolution, dict) or target_resolution.get("ok") is not True:
        return blocked("target_blocked")
    platform = target_resolution.get("platform")
    target_package = target_resolution.get("target_package")
    mappings = target_resolution.get("platform_mappings") or {}
    capabilities = target_resolution.get("capabilities") or {}
    if platform not in ("android", "ios") or not isinstance(target_package, str) or not target_package:
        return blocked("target_invalid")
    if uses_dynamic_object(source) and capabilities.get("ui_dynamic_object_v1") is not True:
        return blocked("dynamic_object_unsupported")
    try:
        data = yaml.load(source, Loader=_UniqueKeysLoader)
    except (yaml.YAMLError, TypeError):
        return blocked("source_invalid")
    if not isinstance(data, dict) or set(data) - {"android", "ios", "agent", "tasks"} or ("android" in data) == ("ios" in data):
        return blocked("structure_unsupported")
    try:
        has_print=print_recovery.validate_flow(data)
    except (ValueError,AttributeError,TypeError):return blocked('print_recovery_invalid')
    if has_print and capabilities.get('ui_print_recovery_v1') is not True:return blocked('print_recovery_unsupported')
    source_platform = "android" if "android" in data else "ios"
    same_android = source_platform == platform == "android" and capabilities.get("android_baseline") is True
    interface = data[source_platform] or {}
    if not isinstance(interface, dict):
        return blocked("interface_unsupported")
    if same_android:
        # Device selection belongs to the job target, never the stored baseline.
        interface = copy.deepcopy(interface)
        interface.pop("deviceId", None)
    elif set(interface) - ({"deviceId"} if source_platform == "android" else set()):
        return blocked("interface_unsupported")
    if "agent" in data and not isinstance(data["agent"], dict):
        return blocked("structure_unsupported")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return blocked("structure_unsupported")
    step_ids = case_version.get("step_ids")
    if step_ids is not None and (not isinstance(step_ids, list) or len(step_ids) != len(tasks)):
        return blocked("step_map_invalid")
    compiled = copy.deepcopy(data)
    compiled.pop(source_platform)
    compiled[platform] = interface if same_android else {}
    step_map = {}
    required_actions = []
    unsupported = []
    source_package = mappings.get("android", target_resolution.get("app_id")) if source_platform == "android" else mappings.get("ios")
    if not source_package:
        return blocked("app_mapping_missing")
    for task_index, task in enumerate(tasks, 1):
        if not isinstance(task, dict) or set(task) - {"name", "flow"} or not isinstance(task.get("flow"), list) or not task["flow"]:
            return blocked("structure_unsupported")
        ids = step_ids[task_index - 1] if step_ids is not None else None
        if ids is not None and (not isinstance(ids, list) or len(ids) != len(task["flow"])):
            return blocked("step_map_invalid")
        for flow_index, step in enumerate(task["flow"], 1):
            position = {"task_index": task_index, "flow_index": flow_index}
            if ids is not None:
                step_id = ids[flow_index - 1]
                if not isinstance(step_id, str) or not step_id.strip():
                    return blocked("step_map_invalid")
                step_map.setdefault(step_id, []).append(position)
            if not isinstance(step, dict):
                unsupported.append(position)
                continue
            actions = [key for key in step if key in MIDSCENE_FLOW_ACTIONS]
            if len(actions) != 1:
                unsupported.append(position)
                continue
            action = actions[0]
            if action not in required_actions:
                required_actions.append(action)
            if same_android:
                if (action not in (capabilities.get("ui_actions") or []) or
                    set(step) - {action} - STEP_ATTR_KEYS or
                    validate_midscene_action_parameters(action, step) or
                    (action == "launch" and step[action] != target_package)):
                    unsupported.append(position)
                continue
            valid = (action in PORTABLE_ACTIONS and
                     set(step) <= {action, *(('timeout',) if action == 'aiWaitFor' else ())} and
                     isinstance(step[action], str) and bool(step[action].strip()) and
                     (action != 'aiWaitFor' or 'timeout' not in step or
                      isinstance(step['timeout'], int) and not isinstance(step['timeout'], bool) and step['timeout'] > 0))
            if action == "aiQuery":
                valid = set(step) == {"aiQuery"} and isinstance(step[action], str) and step[action].startswith((PREFIX,print_recovery.PREFIX))
            if action == "aiScroll":
                valid = _portable_scroll_valid(step)
            if action == "aiInput":
                valid = (set(step) == {"aiInput", "value"} and
                         isinstance(step[action], str) and bool(step[action].strip()) and
                         isinstance(step.get("value"), str) and bool(step["value"]))
            if action == "launch" and step.get(action) != source_package:
                valid = False
            if not valid or action not in (capabilities.get("ui_actions") or []):
                unsupported.append(position)
            elif action == "launch":
                compiled["tasks"][task_index - 1]["flow"][flow_index - 1]["launch"] = target_package
    if unsupported:
        return blocked("unsupported_steps", unsupported)
    content = yaml.safe_dump(compiled, allow_unicode=True, sort_keys=False)
    return {"ok": True, "block_code": "", "yaml_content": content,
            "source_hash": source_hash, "compiled_hash": _sha256(content),
            "step_map": step_map, "required_actions": required_actions, "unsupported_steps": []}
