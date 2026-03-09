第一阶段代码 Review 报告
一、总体评价
第一阶段的工程结构和架构设计是正确的，核心模块（Planner/Router/Executor/Graph/MCP Pool）均已实现，代码质量较高，有清晰的注释和日志。但存在 3 个关键 Bug 会导致系统实际无法工作，以及若干中等问题，下面逐一列出。

二、关键 Bug（需立即修复）
Bug 1：前端永远看不到任何 AI 回复 ⚠️ 最严重
文件：executor.py:132-137 + planner/node.py:134-141

问题：整个 multi_agent 图的执行过程中，外层 state["messages"] 从未被写入任何 AI 消息。Domain Agent 的输出只存入了 task_pool[].result 和 verified_facts，Planner 的最终答案存入了 final_result，但这两个字段对前端都是不可见的。

前端通过 useStream → thread.messages 展示对话，而 LangGraph SDK 的 messages-tuple 事件只跟踪 state.messages 的变化。用户发出消息后，整个多智能体流程运行完毕，但 state.messages 始终只有那一条 HumanMessage —— 用户界面上什么都看不到。

根因：Executor 捕获 domain agent 的输出后只写入 task_pool，Planner 写入 final_result，两处都没有向外层 state["messages"] 追加 AIMessage。

修复方向：Planner 在 execution_state = "DONE" 时，除了写 final_result，还需要返回 {"messages": [AIMessage(content=summary)], ...}。

Bug 2：多轮对话完全错乱 ⚠️
文件：planner/node.py:110-112


original_input: str = state.get("original_input") or _extract_original_input(state)
is_first_run = not task_pool
问题：用 not task_pool 判断"是否第一次规划"，会在第二次对话时完全失效。第一次对话结束后，task_pool 里存有 DONE/FAILED 的历史任务，状态被 checkpoint 持久化。用户发第二条消息时：

task_pool 不为空（含上一轮的 DONE 任务）→ is_first_run = False
Planner 误入 validate 模式，认为自己在验收上一轮的旧任务
original_input 是上一轮的旧问题（已从 checkpoint 加载）
_extract_original_input 只取 messages 列表中第一条 HumanMessage（也是上一轮的问题）
结果：第二条消息永远在对着上一轮的任务做"完成校验"，永远不会拆解新任务。

修复方向：需要一个机制在每次新的用户消息到来时清空 task_pool、重置 original_input。可以在 Planner 首次被调用时，通过对比 state["messages"] 中最后一条 HumanMessage 与 original_input 来判断是否为新对话轮次，并在不一致时强制进入 decompose 模式。

Bug 3：ask_clarification 中断后恢复逻辑缺失 ⚠️
文件：executor.py:116-124


if messages and isinstance(messages[-1], ToolMessage) and messages[-1].name == "ask_clarification":
    return Command(
        update={"messages": [messages[-1]]},
        goto=END,
    )
问题一：用户回答澄清问题后，前端通过 thread.submit() 提交新消息，LangGraph 从 START 重新执行图。此时 task_pool 中该任务状态为 RUNNING，Planner 检测到 RUNNING 任务后返回 execution_state = "RESUMING"，然后 Router 看到 RUNNING 任务，把它再次转到 Executor。

Executor 再次调用 domain_agent.ainvoke({"messages": [HumanMessage(content=context)]}, ...) —— 只传了原始任务描述，完全没有把用户的澄清回答传进去。Domain Agent 对用户回答一无所知。

问题二：Command(goto=END) 会终止外层图，但 ToolMessage 里的 tool_call_id 没有对应的 AIMessage（tool_call），ClarificationMiddleware 产生的 ToolMessage 写入 state.messages 时会违反 LangGraph 的消息完整性约束，可能触发 DanglingToolCallMiddleware 的补偿逻辑（因为 AIMessage 的 tool_call 在外层 state 里根本不存在）。

三、中等问题
问题 4：Domain Agent 执行时不必要的中间件副作用
文件：lead_agent/agent.py:207-251

make_lead_agent 在作为 domain agent 被调用时，会挂载完整的中间件链，包括：

TitleMiddleware：每次 domain agent 调用都会尝试生成线程标题（触发额外 LLM 调用）
MemoryMiddleware：把 domain agent 内部的任务描述+回复存入长期记忆，污染记忆质量
建议：Executor 调用 domain agent 时，应该通过 config 传入标记（如 is_domain_agent: True），_build_middlewares 据此跳过 TitleMiddleware 和 MemoryMiddleware。

问题 5：route_count 跨对话累积，最终触发误报防死循环
文件：router/semantic_router.py:88-99


route_count = (state.get("route_count") or 0) + 1
if route_count >= MAX_ROUTE_COUNT:  # 12
route_count 存在 checkpoint 状态中，跨多轮对话累积。用户在同一个 thread 里连续对话 12 次（每次 1 个任务），就会触发防死循环保护，后续所有请求都返回错误。

修复方向：route_count 应该是每轮对话的计数，在每次新的规划开始时（Planner decompose 阶段）重置为 0。

问题 6：merge_task_pool 缺少状态机保护
文件：thread_state.py:47


mapping[tid].update({k: v for k, v in task.items() if v is not None})
设计文档 Step 2.1 明确要求："状态机转换保护（RUNNING 不可被外部重置为 PENDING）"。当前实现对所有非 None 值无条件覆盖，没有阻止 DONE → PENDING 的回退。Phase 2 并行执行时这会导致数据竞争。Phase 1 串行下影响较小，但应尽早补上。

问题 7：测试脚本中 MemorySaver 未被注入
文件：test_multi_agent.py:22-25


checkpointer = MemorySaver()
graph = build_multi_agent_graph()
graph = graph.with_config({"recursion_limit": 50})  # MemorySaver 从未传给 graph!
MemorySaver 创建了但从未使用，图没有 checkpointer。这意味着测试不能验证多轮对话的状态持久化行为，与生产环境差异显著。

修复：build_multi_agent_graph() 应支持接收 checkpointer 参数：


graph = build_multi_agent_graph()
compiled = graph.compile(checkpointer=checkpointer)
问题 8：Planner prompt 指令与代码解析器不一致
文件：planner/prompt.py:14


5. Output ONLY valid JSON — no markdown fences, no explanation text.
代码解析器 _parse_planner_output 已经处理了 markdown fence（反引号包裹的代码块），这是好的防御性设计。但 VALIDATE_SYSTEM_PROMPT 也有同样的指令，且验证模式的两种输出格式（done=true / done=false）共用同一个解析器，如果 LLM 输出的 done=false 时使用了 list 格式（[{...}]），代码会把它包装为 {"done": False, "tasks": [...]}，但如果 LLM 用了 object 格式（{"done": false, "tasks": [...]}）就直接返回，两者都能工作。✓ 这部分没问题。

四、前后端交互逻辑
正常的部分 ✓
方面	状态
langgraph.json 注册 multi_agent 图	✓ 正确
前端 assistantId: "multi_agent"	✓ 与注册一致
context.thread_id 传给后端	✓ ThreadDataMiddleware 能拿到
streamMode: ["values", "messages-tuple", "custom"]	✓ 支持多模式
streamSubgraphs: true	✓ 能看到子图事件
有问题的部分 ✗
方面	问题
AI 回复可见性	Planner 的 final_result 不在 messages 中，前端看不到
Domain agent 流式输出	Domain agent 内部的 LLM 流式 token 因为是 ainvoke（非 stream），不会发到前端，用户看不到思考过程
task_running 自定义事件	前端有 onCustomEvent 处理 task_running，但 multi_agent 图的 executor 目前没有发任何 task_running 事件
五、第一阶段需求完成度
设计文档要求	实现状态	说明
AgentConfig 扩展（domain/hitl_keywords/mcp_servers）	✅ 完成	
list_domain_agents() 注册中心	✅ 完成	
ThreadState 扩展为黑板（task_pool/verified_facts/route_count）	✅ 完成	
Planner 节点（任务拆解+目标校验）	✅ 完成但有 Bug 2	
语义路由器（LLM路由+快速通道+防死循环）	✅ 完成但有 Bug 5	
Executor 节点（调用domain agent+写回结果）	✅ 完成但有 Bug 1、3、4	
LangGraph 图重构	✅ 完成	
MCP Pool 懒初始化	✅ 完成	
前端切换为 multi_agent 图	✅ 完成	
前端可见的 AI 回复	❌ 缺失	final_result 未写入 messages
多轮对话	❌ 缺失	task_pool 未重置
六、优先修复清单
按照紧迫程度：

Bug 1（最高）：Planner 在 DONE 时 return 中加入 "messages": [AIMessage(content=summary)]，让用户能看到回复
Bug 2（最高）：新消息到来时重置 task_pool 和 original_input，可通过对比最后一条 HumanMessage 实现
问题 5（高）：route_count 在 Planner decompose 开始时重置为 0
Bug 3（中）：完善 ask_clarification 的中断/恢复流程，将用户回答传递给 domain agent
问题 4（中）：domain agent 上下文中跳过 TitleMiddleware 和 MemoryMiddleware
问题 7（低）：测试脚本补全 checkpointer 注入