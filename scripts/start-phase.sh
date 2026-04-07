#!/bin/bash
#
# start-phase.sh — 一键启动一个阶段的完整开发流程
#
# 用法:
#   ./scripts/start-phase.sh <feature-name>
#
# 示例:
#   ./scripts/start-phase.sh workflow-intervention-flow
#
# 完整流程:
#   1. 从 feature 文档创建 GitHub Issue
#   2. 启动后端 agent（如果有 backend checklist）
#   3. 启动前端 agent（如果有 frontend checklist）
#   4. 打印状态查看命令
#

set -euo pipefail

FEATURE_NAME="${1:?用法: $0 <feature-name>}"
FEATURE_DIR="collaboration/features"

# ─── 校验 ───
if [ ! -f "${FEATURE_DIR}/${FEATURE_NAME}.md" ]; then
  echo "错误: 找不到 ${FEATURE_DIR}/${FEATURE_NAME}.md"
  echo ""
  echo "可用的 feature:"
  ls ${FEATURE_DIR}/*.md 2>/dev/null | grep -v checklist | sed 's|.*/||;s|\.md$||' | sort
  exit 1
fi

HAS_BACKEND=$( [ -f "${FEATURE_DIR}/${FEATURE_NAME}-backend-checklist.md" ] && echo "yes" || echo "no" )
HAS_FRONTEND=$( [ -f "${FEATURE_DIR}/${FEATURE_NAME}-frontend-checklist.md" ] && echo "yes" || echo "no" )

echo "========================================="
echo "  DeerFlow 开发阶段启动"
echo "========================================="
echo "Feature: ${FEATURE_NAME}"
echo "后端: ${HAS_BACKEND}"
echo "前端: ${HAS_FRONTEND}"
echo ""

# ─── Step 1: 创建 Issue ───
echo ">>> Step 1: 创建 GitHub Issue"
ISSUE_OUTPUT=$(bash scripts/create-issue.sh "$FEATURE_NAME" 2>&1)
echo "$ISSUE_OUTPUT"

# 提取 issue number
ISSUE_URL=$(echo "$ISSUE_OUTPUT" | grep -oE 'https://github.com/[^ ]+/issues/[0-9]+' | head -1)
ISSUE_NUM=$(echo "$ISSUE_URL" | grep -oE '[0-9]+$')

if [ -z "$ISSUE_NUM" ]; then
  echo "警告: 无法提取 Issue 编号，请手动 spawn"
  exit 1
fi

echo ""
echo "Issue #${ISSUE_NUM} 已创建"
echo ""

# ─── Step 2: 启动 agents ───
echo ">>> Step 2: 启动 Agent"

if [ "$HAS_BACKEND" = "yes" ]; then
  echo "  启动后端 agent..."
  ao spawn backend "$ISSUE_NUM" 2>&1 || echo "  (后端 spawn 需要在 ao start 后执行)"
fi

if [ "$HAS_FRONTEND" = "yes" ]; then
  echo "  启动前端 agent..."
  ao spawn frontend "$ISSUE_NUM" 2>&1 || echo "  (前端 spawn 需要在 ao start 后执行)"
fi

# ─── 完成 ───
echo ""
echo "========================================="
echo "  开发已启动！"
echo "========================================="
echo ""
echo "查看进度:  ao status --watch"
echo "进入后端:  ao open be-1"
echo "进入前端:  ao open fe-1"
echo ""
echo "开发完成后启动 Review:"
echo "  ao spawn review ${ISSUE_NUM}"
echo ""
echo "让 agent 自审:"
echo "  ao send be-1 \"\$(cat prompts/self-review.md)\""
echo "  ao send fe-1 \"\$(cat prompts/self-review.md)\""
