'use strict';
module.exports = [
  {id:'ui_case_selection',title:'勾选与移除基线',assertion:'版本与已选清单一致，移除后计数归零且交接入口禁用。'},
  {id:'ui_case_revision',title:'刷新保留所选版本',assertion:'刷新取得同一版本，复选框、清单和交接入口保持有效。'},
  {id:'ui_start_handoff',title:'所选用例交接到开始测试',assertion:'开始测试页保留对应应用和版本，未选设备时执行按钮禁用。'},
  {id:'ui_start_empty_guard',title:'移除最后一条用例',assertion:'交接后移除最后一条，计数归零，提示勾选基线且执行按钮禁用。'},
  {id:'ui_catalog_paging',title:'真实用例目录翻页与返回',assertion:'前两页与各自接口返回的 ID 一致且无重复，返回后第一页顺序一致。'},
];
