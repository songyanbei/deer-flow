#!/bin/bash
#
# requirements-flow.sh — 需求讨论全流程编排
#
# 用法:
#   ./scripts/requirements-flow.sh <command> <feature-name> [options]
#
# 命令:
#   init    <feature-name>  — 启动 Codex 生成初版文档
#   review  <feature-name>  — 启动 Claude 审查文档
#   revise  <feature-name>  — 让 Codex 根据 review 意见修改
#   publish <feature-name>  — 终版发布到 GitHub Issue
#   status  <feature-name>  — 查看当前状态
#
# 完整流程:
#   1. ./scripts/requirements-flow.sh init my-feature
#   2. ./scripts/requirements-flow.sh review my-feature
#   3. ./scripts/requirements-flow.sh revise my-feature    (如果 NEEDS_REVISION)
#   4. 重复 2-3 直到 APPROVED
#   5. ./scripts/requirements-flow.sh publish my-feature
#

set -euo pipefail

CMD="${1:?用法: $0 <init|review|revise|publish|status> <feature-name>}"
FEATURE="${2:?请指定 feature 名称}"
FEATURE_DIR="collaboration/features"
DOC="${FEATURE_DIR}/${FEATURE}.md"
REVIEW_NOTES="${FEATURE_DIR}/${FEATURE}-review-notes.md"

case "$CMD" in

  # ─────────────────────────────────────────
  # Step 1: Codex 生成初版文档
  # ─────────────────────────────────────────
  init)
    echo ">>> 启动 Codex 生成初版需求文档: ${FEATURE}"
    echo ""

    # Spawn Codex with requirements-writer prompt
    ao spawn --agent codex
    WRITER_SESSION=$(ao session ls 2>/dev/null | grep -oE 'df-[0-9]+' | tail -1)

    if [ -z "$WRITER_SESSION" ]; then
      echo "错误: 无法获取 session 名"
      exit 1
    fi

    echo "Writer session: ${WRITER_SESSION}"
    echo ""

    ao send "$WRITER_SESSION" "你是需求架构师。请参考 prompts/requirements-writer.md 的规范，为以下需求生成文档：

Feature name: ${FEATURE}

请生成以下文件到 collaboration/features/ 目录：
1. ${FEATURE}.md — 主需求文档（Status: draft）
2. ${FEATURE}-backend-checklist.md — 后端执行清单
3. ${FEATURE}-test-checklist.md — 测试执行清单
4. 如果涉及前端：${FEATURE}-frontend-checklist.md

等待用户提供需求描述后开始。"

    echo ""
    echo "Writer 已启动。进入 tmux 与 Codex 讨论需求："
    echo "  tmux ls  # 找到 session 名"
    echo "  tmux attach -t <session-name>"
    echo ""
    echo "文档生成完成后运行:"
    echo "  ./scripts/requirements-flow.sh review ${FEATURE}"
    ;;

  # ─────────────────────────────────────────
  # Step 2: Claude 审查文档
  # ─────────────────────────────────────────
  review)
    if [ ! -f "$DOC" ]; then
      echo "错误: 找不到 ${DOC}，请先运行 init"
      exit 1
    fi

    echo ">>> 启动 Claude 审查需求文档: ${FEATURE}"
    echo ""

    # Spawn Claude Code with requirements-reviewer prompt
    ao spawn
    REVIEWER_SESSION=$(ao session ls 2>/dev/null | grep -oE 'df-[0-9]+' | tail -1)

    if [ -z "$REVIEWER_SESSION" ]; then
      echo "错误: 无法获取 session 名"
      exit 1
    fi

    echo "Reviewer session: ${REVIEWER_SESSION}"
    echo ""

    # Determine round number
    if [ -f "$REVIEW_NOTES" ]; then
      ROUND=$(grep -c "## Review Round" "$REVIEW_NOTES" 2>/dev/null || echo "0")
      ROUND=$((ROUND + 1))
    else
      ROUND=1
    fi

    ao send "$REVIEWER_SESSION" "你是需求审查专家。请严格按照 prompts/requirements-reviewer.md 的规范审查以下需求文档：

Feature: ${FEATURE}
Review Round: ${ROUND}

需要审查的文件：
- 主文档: ${DOC}
- 后端 checklist: ${FEATURE_DIR}/${FEATURE}-backend-checklist.md
- 测试 checklist: ${FEATURE_DIR}/${FEATURE}-test-checklist.md
- 前端 checklist: ${FEATURE_DIR}/${FEATURE}-frontend-checklist.md（如果存在）
$([ -f "$REVIEW_NOTES" ] && echo "- 历史 review 记录: ${REVIEW_NOTES}")

参考项目现有架构：
- collaboration/architecture/backend.md
- backend/src/ 下的相关代码

审查完成后将意见写入: ${REVIEW_NOTES}
格式参照 prompts/requirements-reviewer.md 的输出格式。
写完后 commit 并 push。

铁律：只审查不修改需求文档和 checklist。"

    echo ""
    echo "Reviewer 已启动。查看进度："
    echo "  tmux ls  # 找到 session 名"
    echo "  tmux attach -t <session-name>"
    echo ""
    echo "审查完成后查看结果:"
    echo "  cat ${REVIEW_NOTES}"
    echo ""
    echo "如果 NEEDS_REVISION:"
    echo "  ./scripts/requirements-flow.sh revise ${FEATURE}"
    echo ""
    echo "如果 APPROVED:"
    echo "  ./scripts/requirements-flow.sh publish ${FEATURE}"
    ;;

  # ─────────────────────────────────────────
  # Step 3: Codex 根据 review 修改
  # ─────────────────────────────────────────
  revise)
    if [ ! -f "$REVIEW_NOTES" ]; then
      echo "错误: 找不到 ${REVIEW_NOTES}，请先运行 review"
      exit 1
    fi

    echo ">>> 启动 Codex 修改需求文档: ${FEATURE}"
    echo ""

    ao spawn --agent codex
    WRITER_SESSION=$(ao session ls 2>/dev/null | grep -oE 'df-[0-9]+' | tail -1)

    echo "Writer session: ${WRITER_SESSION}"
    echo ""

    ao send "$WRITER_SESSION" "你是需求架构师。Claude 审查了你的需求文档，提出了修改意见。

请按照 prompts/requirements-writer.md 中「收到 Review 意见后」的流程处理：

1. 阅读 review 意见: ${REVIEW_NOTES}
2. 逐条处理（采纳修改 / 拒绝说明理由）
3. 更新需求文档和 checklist
4. 在 review-notes.md 末尾追加你的回复
5. commit 并 push

需要处理的文件：
- 主文档: ${DOC}
- 后端 checklist: ${FEATURE_DIR}/${FEATURE}-backend-checklist.md
- 测试 checklist: ${FEATURE_DIR}/${FEATURE}-test-checklist.md
- 前端 checklist: ${FEATURE_DIR}/${FEATURE}-frontend-checklist.md（如果存在）
- Review 意见: ${REVIEW_NOTES}"

    echo ""
    echo "Writer 已启动修改。查看进度："
    echo "  tmux ls"
    echo "  tmux attach -t <session-name>"
    echo ""
    echo "修改完成后再次 review:"
    echo "  ./scripts/requirements-flow.sh review ${FEATURE}"
    ;;

  # ─────────────────────────────────────────
  # Step 4: 终版发布到 Issue
  # ─────────────────────────────────────────
  publish)
    if [ ! -f "$DOC" ]; then
      echo "错误: 找不到 ${DOC}"
      exit 1
    fi

    # Check status
    STATUS=$(grep -oP 'Status:\s*`\K[^`]+' "$DOC" 2>/dev/null || echo "unknown")
    if [ "$STATUS" != "final" ] && [ "$STATUS" != "reviewed" ]; then
      echo "警告: 文档状态为 '${STATUS}'，建议先通过 review (APPROVED) 后再发布"
      read -p "确认继续发布？(y/N) " confirm
      [ "$confirm" != "y" ] && exit 0
    fi

    echo ">>> 发布终版需求到 GitHub Issue: ${FEATURE}"
    echo ""

    # Use create-issue.sh
    bash scripts/create-issue.sh "$FEATURE"
    ;;

  # ─────────────────────────────────────────
  # 查看状态
  # ─────────────────────────────────────────
  status)
    echo "=== ${FEATURE} 需求状态 ==="
    echo ""

    if [ -f "$DOC" ]; then
      STATUS=$(grep -oP 'Status:\s*`\K[^`]+' "$DOC" 2>/dev/null || echo "unknown")
      echo "文档状态: ${STATUS}"
    else
      echo "文档状态: 未创建"
    fi

    echo ""
    echo "文件列表:"
    ls -la ${FEATURE_DIR}/${FEATURE}* 2>/dev/null || echo "  (无文件)"

    echo ""
    if [ -f "$REVIEW_NOTES" ]; then
      echo "最新 Review 结论:"
      grep -E "Verdict:|APPROVED|NEEDS_REVISION" "$REVIEW_NOTES" | tail -1
      echo ""
      echo "Review 轮次: $(grep -c 'Review Round' "$REVIEW_NOTES" 2>/dev/null || echo 0)"
    else
      echo "Review: 未开始"
    fi

    echo ""
    echo "活跃 session:"
    ao session ls 2>/dev/null | grep -v "notifier\|webhook" || echo "  (无)"
    ;;

  *)
    echo "未知命令: ${CMD}"
    echo "可用命令: init | review | revise | publish | status"
    exit 1
    ;;
esac
