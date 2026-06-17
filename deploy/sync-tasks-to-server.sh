#!/bin/bash
# 同步 YAML 文件到服务器
# 用法: ./sync-tasks-to-server.sh

set -e

# 配置
SERVER_USER="root"
SERVER_HOST="101.34.197.12"
SERVER_TASK_DIR="/opt/midscene-tasks"
LOCAL_TASK_DIR="./server-tasks-all"

echo "========================================="
echo "  同步 YAML 文件到服务器"
echo "========================================="
echo ""

# 检查本地目录是否存在
if [ ! -d "$LOCAL_TASK_DIR" ]; then
    echo "错误: 本地目录 $LOCAL_TASK_DIR 不存在"
    exit 1
fi

# 统计文件数量
YAML_COUNT=$(find "$LOCAL_TASK_DIR" -name "*.yaml" -o -name "*.yml" | wc -l)
echo "本地 YAML 文件数量: $YAML_COUNT"
echo ""

# 确认同步
echo "即将同步到:"
echo "  服务器: ${SERVER_USER}@${SERVER_HOST}"
echo "  目标目录: ${SERVER_TASK_DIR}"
echo ""
read -p "是否继续? (y/N): " -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 创建远程目录
echo "1. 创建远程目录..."
ssh ${SERVER_USER}@${SERVER_HOST} "mkdir -p ${SERVER_TASK_DIR}"

# 同步文件
echo "2. 同步 YAML 文件..."
rsync -avz --delete \
    --include='*.yaml' \
    --include='*.yml' \
    --include='*/' \
    --exclude='*' \
    "${LOCAL_TASK_DIR}/" \
    ${SERVER_USER}@${SERVER_HOST}:${SERVER_TASK_DIR}/

echo ""
echo "========================================="
echo "  同步完成!"
echo "========================================="
echo ""

# 验证同步
echo "3. 验证服务器文件..."
ssh ${SERVER_USER}@${SERVER_HOST} "find ${SERVER_TASK_DIR} -name '*.yaml' -o -name '*.yml' | wc -l" | while read count; do
    echo "  服务器 YAML 文件数量: $count"
done

echo ""
echo "4. 检查 case_id 注释..."
MISSING_CASE_ID=$(ssh ${SERVER_USER}@${SERVER_HOST} "grep -L 'baseline.case_id' ${SERVER_TASK_DIR}/**/*.yaml 2>/dev/null | wc -l" || echo "0")
echo "  缺少 case_id 注释的文件数: $MISSING_CASE_ID"

if [ "$MISSING_CASE_ID" -gt 0 ]; then
    echo ""
    echo "警告: 部分文件缺少 baseline.case_id 注释"
    echo "建议在 Task 平台重新生成这些用例的桥接脚本"
fi

echo ""
echo "完成!"
