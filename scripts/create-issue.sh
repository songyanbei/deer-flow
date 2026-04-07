#!/bin/bash
#
# create-issue.sh — 从 feature 文档自动创建 GitHub Issue
#
# 用法:
#   ./scripts/create-issue.sh <feature-name>
#
# 示例:
#   ./scripts/create-issue.sh workflow-intervention-flow
#
# 它会:
#   1. 读取 collaboration/features/<name>.md 提取标题、目标、状态
#   2. 读取对应的 backend/frontend/test checklist
#   3. 自动创建 GitHub Issue，内容包含文档链接 + checklist
#   4. 打上对应的 label
#

set -euo pipefail

FEATURE_NAME="${1:?用法: $0 <feature-name>}"
FEATURE_DIR="collaboration/features"
FEATURE_DOC="${FEATURE_DIR}/${FEATURE_NAME}.md"
BACKEND_CHECKLIST="${FEATURE_DIR}/${FEATURE_NAME}-backend-checklist.md"
FRONTEND_CHECKLIST="${FEATURE_DIR}/${FEATURE_NAME}-frontend-checklist.md"
TEST_CHECKLIST="${FEATURE_DIR}/${FEATURE_NAME}-test-checklist.md"

# ─── 校验 ───
if [ ! -f "$FEATURE_DOC" ]; then
  echo "错误: 找不到 $FEATURE_DOC"
  echo "可用的 feature 文档:"
  ls ${FEATURE_DIR}/*.md 2>/dev/null | grep -v checklist | sed 's|.*/||;s|\.md$||' | sort
  exit 1
fi

# ─── 提取标题（第一行 # 开头）───
TITLE=$(head -1 "$FEATURE_DOC" | sed 's/^#\+\s*//' | sed 's/^Feature:\s*//')
if [ -z "$TITLE" ]; then
  TITLE="$FEATURE_NAME"
fi

# ─── 提取目标（## Goal 到下一个 ## 之间的内容）───
GOAL=$(awk '/^## Goal/{flag=1; next} /^## /{flag=0} flag' "$FEATURE_DOC" | head -20 | sed '/^$/d')
if [ -z "$GOAL" ]; then
  GOAL="详见需求文档"
fi

# ─── 提取 checklist 项（- [ ] 和 - [x] 开头的行）───
extract_checklist() {
  local file="$1"
  local label="$2"
  if [ ! -f "$file" ]; then
    echo "（无 ${label} checklist）"
    return
  fi
  # 只提取 checklist 项，保留层级
  grep -E '^\s*- \[([ x])\]' "$file" | head -30
}

BACKEND_ITEMS=$(extract_checklist "$BACKEND_CHECKLIST" "后端")
FRONTEND_ITEMS=$(extract_checklist "$FRONTEND_CHECKLIST" "前端")
TEST_ITEMS=$(extract_checklist "$TEST_CHECKLIST" "测试")

# ─── 判断有哪些 checklist ───
HAS_BACKEND=$( [ -f "$BACKEND_CHECKLIST" ] && echo "yes" || echo "no" )
HAS_FRONTEND=$( [ -f "$FRONTEND_CHECKLIST" ] && echo "yes" || echo "no" )
HAS_TEST=$( [ -f "$TEST_CHECKLIST" ] && echo "yes" || echo "no" )

# ─── 构造 labels ───
LABELS="feature"
[ "$HAS_BACKEND" = "yes" ] && LABELS="${LABELS},backend"
[ "$HAS_FRONTEND" = "yes" ] && LABELS="${LABELS},frontend"

# ─── 构造 Issue body ───
BODY=$(cat <<ISSUE_EOF
## 需求目标

${GOAL}

## 需求文档

| 文档 | 路径 |
|------|------|
| 完整方案 | \`${FEATURE_DOC}\` |
ISSUE_EOF
)

[ "$HAS_BACKEND" = "yes" ] && BODY="${BODY}
| 后端 Checklist | \`${BACKEND_CHECKLIST}\` |"

[ "$HAS_FRONTEND" = "yes" ] && BODY="${BODY}
| 前端 Checklist | \`${FRONTEND_CHECKLIST}\` |"

[ "$HAS_TEST" = "yes" ] && BODY="${BODY}
| 测试 Checklist | \`${TEST_CHECKLIST}\` |"

# 后端 checklist
if [ "$HAS_BACKEND" = "yes" ]; then
BODY="${BODY}

## 后端 Checklist

${BACKEND_ITEMS}

> 完整 checklist 见 \`${BACKEND_CHECKLIST}\`"
fi

# 前端 checklist
if [ "$HAS_FRONTEND" = "yes" ]; then
BODY="${BODY}

## 前端 Checklist

${FRONTEND_ITEMS}

> 完整 checklist 见 \`${FRONTEND_CHECKLIST}\`"
fi

# 测试 checklist
if [ "$HAS_TEST" = "yes" ]; then
BODY="${BODY}

## 测试 Checklist

${TEST_ITEMS}

> 完整 checklist 见 \`${TEST_CHECKLIST}\`"
fi

# 验收标准（从 feature doc 提取）
ACCEPTANCE=$(awk '/^## Acceptance|^## 验收/{flag=1; next} /^## /{flag=0} flag' "$FEATURE_DOC" | head -15 | sed '/^$/d')
if [ -n "$ACCEPTANCE" ]; then
BODY="${BODY}

## 验收标准

${ACCEPTANCE}"
fi

# ─── 创建 Issue ───
echo "正在创建 Issue..."
echo "标题: ${TITLE}"
echo "Labels: ${LABELS}"
echo ""

ISSUE_URL=$(gh issue create \
  --repo songyanbei/deer-flow \
  --title "${TITLE}" \
  --body "${BODY}" \
  2>&1)

echo "✅ Issue 已创建: ${ISSUE_URL}"
echo ""
echo "下一步:"
echo "  ao spawn backend <issue-number>"
echo "  ao spawn frontend <issue-number>"
