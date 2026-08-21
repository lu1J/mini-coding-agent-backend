"""Day15 LangGraph v2 细粒度编排专项测试。

覆盖：
1. low-risk read 全流程：model → executor → policy → tool → model → finished
2. high-risk edit 在 approval 处 interrupt
3. interrupt 前 workspace 文件没有变化、写工具没有被调用
4. approved resume：execute_verified_change → executor_state 推进 → Graph 自己继续 → finished
5. rejected resume：不产生文件副作用
6. executor out-of-order：阻止错误工具
7. policy block：不执行工具
8. tool failure：execute_tool → reflection → model retry
9. multiple tool calls：pending_tool_calls / cursor 正确推进
10. max_steps / premature final 核心路径
"""

import json

import pytest

langgraph = pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import app.agent.change_verifier as change_verifier_module
import app.agent.langgraph_v2_nodes as v2_nodes
from app.agent.langgraph_v2_workflow import build_fine_grained_graph


# ------------------------------------------------------------------ fakes


def make_fake_tool_call(tool_name, tool_args, call_id="call_v2_001"):
    return SimpleNamespaceProxy(
        id=call_id,
        function=SimpleNamespaceProxy(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def make_fake_response(message):
    return SimpleNamespaceProxy(choices=[SimpleNamespaceProxy(message=message)])


def make_final_response(content):
    return make_fake_response(
        SimpleNamespaceProxy(content=content, tool_calls=None)
    )


def make_tool_response(tool_calls):
    return make_fake_response(
        SimpleNamespaceProxy(content="", tool_calls=tool_calls)
    )


def patch_llm(monkeypatch, responses):
    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        lambda *a, **k: responses.pop(0),
    )


def make_step(index, title, suggested_tool, risk_level="low", reason="test"):
    return {
        "index": index,
        "title": title,
        "description": f"{title} 测试步骤",
        "suggested_tool": suggested_tool,
        "risk_level": risk_level,
        "reason": reason,
    }


def make_plan(steps, target_paths=None, intents=None, risk_level="low"):
    return {
        "objective": "测试任务",
        "intents": intents or ["read"],
        "target_paths": list(target_paths or []),
        "suggested_tools": [
            step["suggested_tool"]
            for step in steps
            if step.get("suggested_tool")
        ],
        "risk_level": risk_level,
        "complexity": "simple",
        "needs_approval": False,
        "estimated_steps": len(steps),
        "steps": steps,
        "warnings": [],
        "planner_meta": {"version": "2.1"},
    }


class SimpleNamespaceProxy:
    """与 SimpleNamespace 等价的极简命名空间对象。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_read_file_tool():
    calls = {"count": 0, "failed_paths": set()}

    def read_file(path: str):
        calls["count"] += 1
        if path in calls["failed_paths"]:
            return {
                "success": False,
                "result": f"文件不存在：{path}",
                "error": {
                    "type": "file_not_found",
                    "message": "文件不存在",
                    "detail": path,
                },
            }
        return {"success": True, "result": f"内容：{path}", "error": None}

    return read_file, calls


def make_list_files_tool():
    calls = {"count": 0}

    def list_files(path: str):
        calls["count"] += 1
        return {"success": True, "result": f"目录：{path}", "error": None}

    return list_files, calls


def make_run_command_tool():
    calls = {"count": 0}

    def run_command(command: str):
        calls["count"] += 1
        return {"success": True, "result": f"输出：{command}", "error": None}

    return run_command, calls


def make_edit_file_tool(workspace_root):
    calls = {"count": 0}

    def edit_file(path: str, old_text: str = "", new_text: str = ""):
        calls["count"] += 1
        target = workspace_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            content = target.read_text(encoding="utf-8")
            if old_text and old_text in content:
                content = content.replace(old_text, new_text, 1)
            else:
                content = content + "\n" + new_text
        else:
            content = new_text
        target.write_text(content, encoding="utf-8")
        return {"success": True, "result": f"已修改 {path}", "error": None}

    return edit_file, calls


def make_initial_state(user_message="测试任务", max_steps=8, thread_id="v2_test"):
    return {
        "thread_id": thread_id,
        "user_message": user_message,
        "max_steps": max_steps,
        "messages": [],
        "steps": [],
        "graph_events": [],
        "pending_tool_calls": [],
        "tool_cursor": 0,
        "model_round": 0,
        "step_counter": 0,
        "reflection_retry_count": 0,
        "premature_final_count": 0,
    }


def build_graph(monkeypatch, *, plan, available_tools, max_steps=8):
    monkeypatch.setattr(v2_nodes, "build_task_plan", lambda _msg: plan)
    return build_fine_grained_graph(
        checkpointer=InMemorySaver(),
        system_prompt="测试系统提示词",
        tools=[],
        available_tools=available_tools,
    )


def step_types(result):
    return [step["type"] for step in result.get("steps", [])]


# ------------------------------------------------------------------ 1. read 全流程


def test_v2_read_flow_finishes(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    graph = build_graph(monkeypatch, plan=plan, available_tools={"read_file": read_tool})

    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo.txt"})]
            ),
            make_final_response("文件读取完成。"),
        ],
    )

    result = graph.invoke(make_initial_state(), config={"configurable": {"thread_id": "v2_read"}})

    final = result["final_result"]
    assert final["status"] == "finished"
    assert final["answer"] == "文件读取完成。"
    assert final["graph"]["orchestrator"] == "langgraph_v2"
    assert final["graph"]["workflow_version"] == "2.0"
    assert final["run_id"]
    assert read_calls["count"] == 1

    assert step_types(result) == ["tool_call", "final_answer"]
    assert result["executor_state"]["status"] == "completed"
    assert result["model_round"] == 2

    events = [event["event"] for event in result["graph_events"]]
    assert "plan_created" in events
    assert "graph_finalized" in events


# ------------------------------------------------------------------ 1b. model 上下文完整


def test_v2_model_node_sends_user_message_and_system_prompt(monkeypatch, tmp_path):
    """回归：model_node 必须把 system_prompt 和原始 user_message 发给 LLM。

    曾出现真实 API 问题：v2 只发送 executor instruction，
    模型在不知道用户原文的情况下，把“新增注释”误解为“替换已有注释”，
    生成语法合法但语义错误的 edit_file 参数。
    """
    plan = make_plan(
        [make_step(0, "修改文件", "edit_file", risk_level="high")],
        target_paths=["demo_project/models.py"],
        intents=["edit"],
        risk_level="high",
    )
    edit_tool, _ = make_edit_file_tool(tmp_path)
    graph = build_graph(monkeypatch, plan=plan, available_tools={"edit_file": edit_tool})

    captured_messages = {}

    def fake_create(*args, **kwargs):
        captured_messages["messages"] = kwargs["messages"]
        return make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/models.py", "old_text": "x", "new_text": "y"},
                )
            ]
        )

    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        fake_create,
    )

    user_message = (
        "请在 demo_project/models.py 文件末尾新增注释 "
        "# LangGraph v2 fine-grained test 并运行测试"
    )
    interrupted = graph.invoke(
        make_initial_state(user_message=user_message),
        config={"configurable": {"thread_id": "v2_model_context"}},
    )
    assert interrupted.get("__interrupt__")

    messages = captured_messages["messages"]
    # 第一条：系统提示（工具使用规范，如 edit_file 前必须先读文件确认原文）
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "测试系统提示词"
    # 第二条：原始 user_message 原样进入模型上下文
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == user_message
    # 最后一条：executor instruction（当前步骤约束）
    assert messages[-1]["role"] == "system"
    assert "[Planner–Executor]" in messages[-1]["content"]


# ------------------------------------------------------------------ 2/3. edit interrupt


def test_v2_edit_interrupts_at_approval_without_side_effects(monkeypatch, tmp_path):
    plan = make_plan(
        [make_step(0, "修改文件", "edit_file", risk_level="high")],
        target_paths=["demo_project/v2_edit.txt"],
        intents=["edit"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    graph = build_graph(monkeypatch, plan=plan, available_tools={"edit_file": edit_tool})

    target = tmp_path / "demo_project" / "v2_edit.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("原始内容\n", encoding="utf-8")

    original_args = {
        "path": "demo_project/v2_edit.txt",
        "new_text": "hello v2",
    }
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [
                    make_fake_tool_call(
                        "edit_file",
                        original_args,
                    )
                ]
            )
        ],
    )

    interrupted = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_edit"}}
    )

    # 1) 在 approval 处 interrupt
    assert interrupted.get("__interrupt__")
    interrupt_value = interrupted["__interrupt__"][0].value
    assert interrupt_value["type"] == "approval_required"
    assert interrupt_value["tool_name"] == "edit_file"
    assert interrupted["status"] == "waiting_approval"
    assert interrupted["pending_action"]["tool_name"] == "edit_file"

    # 参数逐层透传：原始 arguments → pending_tool_calls → parsed_args → pending_action
    # （防止 Runtime 在状态传递过程中篡改模型生成的参数）
    assert interrupted["pending_action"]["tool_args"] == original_args
    assert interrupt_value["tool_args"] == original_args
    approval_step = [
        s for s in interrupted["steps"] if s["type"] == "approval_required"
    ][0]
    assert approval_step["tool_args"] == original_args

    # 2) interrupt 前：没有真实副作用、写工具没有被调用
    assert edit_calls["count"] == 0
    assert target.read_text(encoding="utf-8") == "原始内容\n"
    assert "approval_required" in step_types(interrupted)


# ------------------------------------------------------------------ 4. approved resume


def test_v2_approved_resume_graph_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(change_verifier_module, "WORKSPACE_ROOT", tmp_path)

    plan = make_plan(
        [make_step(0, "修改文件", "edit_file", risk_level="high")],
        target_paths=["demo_project/v2_edit.txt"],
        intents=["edit"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    graph = build_graph(monkeypatch, plan=plan, available_tools={"edit_file": edit_tool})

    responses = [
        make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_edit.txt", "new_text": "hello v2"},
                    call_id="call_edit",
                )
            ]
        ),
        make_final_response("修改完成。"),
    ]
    captured_messages = []

    def fake_create(*args, **kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    config = {"configurable": {"thread_id": "v2_edit_approved"}}
    interrupted = graph.invoke(make_initial_state(), config=config)
    assert interrupted.get("__interrupt__")
    assert interrupted["pending_action"]["tool_call_id"] == "call_edit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=config)

    # 协议回归（A）：approval 成功恢复后，第二次 LLM 调用的 messages 中，
    # assistant(tool_calls call_edit) 后面必须存在 tool(tool_call_id=call_edit)，
    # 否则 OpenAI 会返回 400 insufficient tool messages。
    assert len(captured_messages) == 2
    second = captured_messages[1]
    assistant_idx = next(
        i for i, m in enumerate(second) if m.get("tool_calls")
    )
    assert second[assistant_idx]["tool_calls"][0]["id"] == "call_edit"
    assert second[assistant_idx + 1]["role"] == "tool"
    assert second[assistant_idx + 1]["tool_call_id"] == "call_edit"

    final = resumed["final_result"]
    assert final["status"] == "finished"
    assert final["answer"] == "修改完成。"
    assert final["approval_result"]["status"] == "approved"
    assert final["verification_report"]["success"] is True

    # 写操作真实发生且经过 execute_verified_change 验证
    target = tmp_path / "demo_project" / "v2_edit.txt"
    assert target.exists()
    assert "hello v2" in target.read_text(encoding="utf-8")
    assert edit_calls["count"] == 1

    # executor_state 由审批推进，Graph 自己继续到 finished
    assert resumed["executor_state"]["status"] == "completed"
    assert resumed["executor_state"]["completed_steps"] == 1
    assert resumed["status"] == "finished"

    # 审批后的真实写操作被补进执行轨迹
    tool_steps = [s for s in resumed["steps"] if s["type"] == "tool_call"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["tool_name"] == "edit_file"
    assert tool_steps[0]["source"] == "approval_execution"
    assert tool_steps[0]["success"] is True
    assert tool_steps[0]["verification_status"] in ("passed", "passed_with_warnings")


# ------------------------------------------------------------------ 5. rejected resume


def test_v2_rejected_resume_no_side_effect(monkeypatch, tmp_path):
    plan = make_plan(
        [make_step(0, "修改文件", "edit_file", risk_level="high")],
        target_paths=["demo_project/v2_rejected.txt"],
        intents=["edit"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    graph = build_graph(monkeypatch, plan=plan, available_tools={"edit_file": edit_tool})

    responses = [
        make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_rejected.txt", "new_text": "x"},
                )
            ]
        )
    ]
    llm_call_count = {"count": 0}

    def fake_create(*args, **kwargs):
        llm_call_count["count"] += 1
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    config = {"configurable": {"thread_id": "v2_edit_rejected"}}
    interrupted = graph.invoke(make_initial_state(), config=config)
    assert interrupted.get("__interrupt__")

    resumed = graph.invoke(Command(resume={"approved": False}), config=config)

    # 协议回归（C）：拒绝后直接 finalize，不再调用模型
    # （assistant(tool_calls) 未配对的 tool message 不会进入新的 LLM 请求）。
    assert llm_call_count["count"] == 1

    assert resumed["status"] == "rejected"
    assert resumed["final_result"]["status"] == "rejected"
    assert resumed["final_result"]["approval_result"]["status"] == "rejected"

    # 拒绝：不产生任何文件副作用，写工具没有被调用
    assert edit_calls["count"] == 0
    assert not (tmp_path / "demo_project" / "v2_rejected.txt").exists()


# ------------------------------------------------------------------ 6. executor out-of-order


def test_v2_executor_out_of_order_blocks_wrong_tool(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    run_tool, run_calls = make_run_command_tool()
    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={"read_file": read_tool, "run_command": run_tool},
    )

    patch_llm(
        monkeypatch,
        [
            # 第一轮：调用计划外的 run_command → executor_gate 必须阻止
            make_tool_response(
                [make_fake_tool_call("run_command", {"command": "rm -rf /"})]
            ),
            # 第二轮：改为计划内的 read_file
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo.txt"})]
            ),
            make_final_response("读取完成。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_out_of_order"}}
    )

    assert result["final_result"]["status"] == "finished"
    assert run_calls["count"] == 0
    assert read_calls["count"] == 1

    blocked_steps = [s for s in result["steps"] if s["type"] == "executor_blocked"]
    assert len(blocked_steps) == 1
    assert blocked_steps[0]["tool_name"] == "run_command"
    assert blocked_steps[0]["error"]["type"] == "plan_step_violation"
    assert blocked_steps[0]["executor_decision"]["allowed"] is False
    assert result["executor_state"]["blocked_calls"] == 1


# ------------------------------------------------------------------ 7. policy block


def test_v2_policy_block_outside_scope_does_not_execute(monkeypatch, tmp_path):
    monkeypatch.setattr(change_verifier_module, "WORKSPACE_ROOT", tmp_path)

    plan = make_plan(
        [make_step(0, "修改文件", "edit_file", risk_level="high")],
        target_paths=["demo_project/plan_target.txt"],
        intents=["edit"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    graph = build_graph(monkeypatch, plan=plan, available_tools={"edit_file": edit_tool})

    patch_llm(
        monkeypatch,
        [
            # 第一轮：目标路径超出计划范围 → policy 必须拦截
            make_tool_response(
                [
                    make_fake_tool_call(
                        "edit_file",
                        {"path": "demo_project/other.txt", "new_text": "x"},
                    )
                ]
            ),
            # 第二轮：改为计划内路径 → require_approval → interrupt
            make_tool_response(
                [
                    make_fake_tool_call(
                        "edit_file",
                        {"path": "demo_project/plan_target.txt", "new_text": "ok"},
                    )
                ]
            ),
            # 第三轮（审批批准恢复后）：最终总结
            make_final_response("修改完成。"),
        ],
    )

    config = {"configurable": {"thread_id": "v2_policy_block"}}
    interrupted = graph.invoke(make_initial_state(), config=config)

    # policy block：不执行工具
    assert edit_calls["count"] == 0
    blocked_steps = [s for s in interrupted["steps"] if s["type"] == "policy_blocked"]
    assert len(blocked_steps) == 1
    assert blocked_steps[0]["tool_name"] == "edit_file"
    assert blocked_steps[0]["policy_decision"]["decision"] == "block"
    violation_codes = [
        v["code"]
        for v in blocked_steps[0]["policy_decision"]["violations"]
        if isinstance(v, dict)
    ]
    assert "write_outside_scope" in violation_codes

    # 第二轮计划内路径正常进入审批
    assert interrupted.get("__interrupt__")
    assert interrupted["pending_action"]["tool_args"]["path"] == "demo_project/plan_target.txt"

    # 审批批准后真正执行（路径在计划范围内 → 二次策略检查通过）
    resumed = graph.invoke(Command(resume={"approved": True}), config=config)
    assert resumed["final_result"]["status"] == "finished"
    assert edit_calls["count"] == 1
    assert (tmp_path / "demo_project" / "plan_target.txt").exists()


# ------------------------------------------------------------------ 8. tool failure → reflection → retry


def test_v2_tool_failure_reflection_retry(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    read_calls["failed_paths"].add("demo/missing.txt")
    graph = build_graph(monkeypatch, plan=plan, available_tools={"read_file": read_tool})

    patch_llm(
        monkeypatch,
        [
            # 第一轮：读取不存在的文件 → 失败 → reflection
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo/missing.txt"})]
            ),
            # 第二轮：模型给出失败总结 → degraded final accepted
            make_final_response("文件不存在，无法读取。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_failure"}}
    )

    assert result["final_result"]["status"] == "finished"
    assert read_calls["count"] == 1

    # reflection 必须是真实节点：产生独立 reflection 步骤
    reflection_steps = [s for s in result["steps"] if s["type"] == "reflection"]
    assert len(reflection_steps) == 1
    assert reflection_steps[0]["retry"] is True
    assert result["reflection_retry_count"] == 1
    assert result["tool_execution"]["success"] is False

    # 失败步骤被标记为 retrying，允许 degraded final
    failed_step = [s for s in result["steps"] if s["type"] == "tool_call"][0]
    assert failed_step["success"] is False
    final_step = [s for s in result["steps"] if s["type"] == "final_answer"][0]
    assert final_step["degraded"] is True

    # 失败自省反馈进入 messages
    feedback_messages = [
        m for m in result["messages"] if m.get("content", "").startswith("[失败自省]")
    ]
    assert len(feedback_messages) == 1
    assert "分析" in feedback_messages[0]["content"]


# ------------------------------------------------------------------ 9. multiple tool calls


def test_v2_multiple_tool_calls_cursor_progresses(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    list_tool, list_calls = make_list_files_tool()
    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={"read_file": read_tool, "list_files": list_tool},
    )

    patch_llm(
        monkeypatch,
        [
            # 第一轮：同时返回两个 tool_call（supporting list_files + read_file）
            make_tool_response(
                [
                    make_fake_tool_call("list_files", {"path": "demo"}, call_id="call_a"),
                    make_fake_tool_call("read_file", {"path": "demo/a.txt"}, call_id="call_b"),
                ]
            ),
            make_final_response("读取完成。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_multi"}}
    )

    assert result["final_result"]["status"] == "finished"
    assert list_calls["count"] == 1
    assert read_calls["count"] == 1

    tool_steps = [s for s in result["steps"] if s["type"] == "tool_call"]
    assert [s["tool_name"] for s in tool_steps] == ["list_files", "read_file"]

    # 两个 tool_call 都被处理：cursor 走到末尾
    assert result["tool_cursor"] == 2

    # 第一个是 supporting 工具，第二个完成计划步骤
    assert tool_steps[0]["executor_decision"]["decision"] == "allow_supporting_tool"
    assert result["executor_state"]["steps"][0]["supporting_calls_used"] == 1
    assert tool_steps[1]["executor_decision"]["decision"] == "allow_current_step"
    assert result["executor_state"]["completed_steps"] == 1


# ------------------------------------------------------------------ 10. max_steps / premature final


def test_v2_max_steps_reached(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    graph = build_graph(
        monkeypatch, plan=plan, available_tools={"read_file": read_tool}, max_steps=2
    )

    patch_llm(
        monkeypatch,
        [
            # 第一轮：read_file 完成步骤
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo.txt"})]
            ),
            # 第二轮：模型仍然调用工具 → final phase blocked → max_steps 退出
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo.txt"})]
            ),
        ],
    )

    result = graph.invoke(
        make_initial_state(max_steps=2),
        config={"configurable": {"thread_id": "v2_max_steps"}},
    )

    assert result["status"] == "max_steps_reached"
    assert result["final_result"]["status"] == "max_steps_reached"
    assert result["executor_state"]["status"] == "max_steps_reached"

    reflection_steps = [s for s in result["steps"] if s["type"] == "reflection"]
    assert len(reflection_steps) == 1
    assert reflection_steps[0]["error"]["type"] == "max_steps_reached"


def test_v2_premature_final_recovered_then_finished(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    graph = build_graph(monkeypatch, plan=plan, available_tools={"read_file": read_tool})

    patch_llm(
        monkeypatch,
        [
            # 第一轮：计划未完成就给出最终总结 → premature feedback
            make_final_response("提前总结。"),
            # 第二轮：执行计划工具
            make_tool_response(
                [make_fake_tool_call("read_file", {"path": "demo.txt"})]
            ),
            # 第三轮：计划完成 → 真正结束
            make_final_response("读取完成。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(max_steps=4),
        config={"configurable": {"thread_id": "v2_premature_ok"}},
    )

    assert result["final_result"]["status"] == "finished"
    assert result["premature_final_count"] == 1

    blocked_steps = [
        s for s in result["steps"]
        if s["type"] == "executor_blocked"
        and s["error"]["type"] == "premature_final_answer"
    ]
    assert len(blocked_steps) == 1
    assert blocked_steps[0]["content"] == "提前总结。"
    assert read_calls["count"] == 1


def test_v2_premature_final_twice_stalls(monkeypatch):
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    graph = build_graph(monkeypatch, plan=plan, available_tools={"read_file": read_tool})

    patch_llm(
        monkeypatch,
        [
            make_final_response("第一次提前总结。"),
            make_final_response("第二次提前总结。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(max_steps=4),
        config={"configurable": {"thread_id": "v2_premature_stall"}},
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "executor_stalled"
    assert result["final_result"]["status"] == "failed"
    assert read_calls["count"] == 0
    assert result["premature_final_count"] == 2


# ------------------------------------------------------------------ 11. approval + multi-call 协议


def test_v2_approval_then_remaining_tool_call_keeps_protocol(monkeypatch, tmp_path):
    """协议回归（B）：一次 assistant 返回 call_edit + call_read 两个 tool_calls。

    - call_edit 需要审批；批准后不得立即调用 LLM
    - 必须继续处理 call_read（每个 pending tool_call 都要有对应 tool message）
    - 两个 call 都有 tool message 后才允许下一次模型调用
    """
    monkeypatch.setattr(change_verifier_module, "WORKSPACE_ROOT", tmp_path)

    plan = make_plan(
        [
            make_step(0, "修改文件", "edit_file", risk_level="high"),
            make_step(1, "读取文件", "read_file"),
        ],
        target_paths=["demo_project/v2_multi.txt"],
        intents=["edit", "read"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    read_tool, read_calls = make_read_file_tool()
    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={"edit_file": edit_tool, "read_file": read_tool},
    )

    responses = [
        make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_multi.txt", "new_text": "multi"},
                    call_id="call_edit",
                ),
                make_fake_tool_call(
                    "read_file",
                    {"path": "demo_project/v2_multi.txt"},
                    call_id="call_read",
                ),
            ]
        ),
        make_final_response("修改并读取完成。"),
    ]
    captured_messages = []

    def fake_create(*args, **kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    config = {"configurable": {"thread_id": "v2_multi_approval"}}
    interrupted = graph.invoke(make_initial_state(), config=config)
    assert interrupted.get("__interrupt__")
    assert interrupted["pending_action"]["tool_call_id"] == "call_edit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=config)

    # LLM 只被调用两次：第一轮生成两个 tool_calls，最后一轮最终总结。
    # 批准 call_edit 后必须继续处理 call_read，不能提前调用 LLM。
    assert len(captured_messages) == 2

    # 两个工具都真实执行，两条 tool message 都在（协议完整）
    assert edit_calls["count"] == 1
    assert read_calls["count"] == 1
    assert resumed["final_result"]["status"] == "finished"
    assert resumed["executor_state"]["completed_steps"] == 2

    second = captured_messages[1]
    assistant_idx = next(
        i for i, m in enumerate(second) if m.get("tool_calls")
    )
    assert [tc["id"] for tc in second[assistant_idx]["tool_calls"]] == [
        "call_edit",
        "call_read",
    ]
    tool_messages = [
        m for m in second[assistant_idx + 1:] if m.get("role") == "tool"
    ]
    tool_call_ids = [m["tool_call_id"] for m in tool_messages]
    assert tool_call_ids == ["call_edit", "call_read"]


# --------------------------------------------- Bug3：Tool Call Batch Barrier


def test_v2_tool_batch_failure_then_success_deferred_reflection(monkeypatch):
    """协议回归（Bug 3 A/B/C/D）：同一 assistant batch 中 call_1 失败、call_2 成功。

    场景还原：model_round=5 一次返回 [run_command pytest(失败), run_command py_compile(成功)]。
    修复前：call_1 失败 → reflection 立刻把 [失败自省] system 消息插进 messages，
    此时 call_2 还没有 role=tool 响应，下一次 LLM 调用必然 400。

    协议不变式：assistant(tool_calls=[call_1, call_2]) → tool(call_1) → tool(call_2)
    → [之后才允许 reflection feedback] → 下一次 LLM。

    - A：下一次 LLM 收到的 messages 顺序必须是
          assistant → tool(call_1) → tool(call_2) → system(失败自省)
    - B：call_1 失败后、call_2 尚未处理时，LLM 调用次数不能增加
    - C：两个 tool response 完成后，下一次 LLM 调用正常、任务正常结束
    - D：Reflection 仍是独立 Graph Node（steps / graph_events 中存在）
    """
    plan = make_plan([make_step(0, "运行命令", "run_command")])

    run_calls = {"count": 0, "failed_commands": {"python -m pytest"}}

    def run_command(command: str):
        run_calls["count"] += 1
        if command in run_calls["failed_commands"]:
            return {
                "success": False,
                "result": f"失败：{command}",
                "error": {
                    "type": "command_failed",
                    "message": "命令执行失败",
                    "detail": command,
                },
            }
        return {"success": True, "result": f"输出：{command}", "error": None}

    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={"run_command": run_command},
    )

    responses = [
        make_tool_response(
            [
                make_fake_tool_call(
                    "run_command",
                    {"command": "python -m pytest"},
                    call_id="call_pytest",
                ),
                make_fake_tool_call(
                    "run_command",
                    {"command": "python -m py_compile models.py"},
                    call_id="call_pycompile",
                ),
            ]
        ),
        make_final_response("命令检查完成。"),
    ]
    captured_messages = []

    def fake_create(*args, **kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    result = graph.invoke(
        make_initial_state(),
        config={"configurable": {"thread_id": "v2_batch_deferred"}},
    )

    # B：总 LLM 调用次数必须只有 2 次（生成 batch + 最终总结）。
    # call_1 失败后 reflection 只能暂存 feedback，不能调用 LLM。
    assert len(captured_messages) == 2

    # 两个工具都真实执行（call_2 没有被跳过）
    assert run_calls["count"] == 2

    # A：第二次 LLM 请求中，所有 tool response 完成后才允许反馈消息
    second = captured_messages[1]
    assistant_idx = next(
        i for i, m in enumerate(second) if m.get("tool_calls")
    )
    assert second[assistant_idx]["tool_calls"][0]["id"] == "call_pytest"
    assert second[assistant_idx]["tool_calls"][1]["id"] == "call_pycompile"
    # 紧随 assistant 的必须是两条 role=tool，不允许任何非 tool 消息插在中间
    assert second[assistant_idx + 1]["role"] == "tool"
    assert second[assistant_idx + 1]["tool_call_id"] == "call_pytest"
    assert second[assistant_idx + 2]["role"] == "tool"
    assert second[assistant_idx + 2]["tool_call_id"] == "call_pycompile"
    # 反馈消息必须位于全部 tool responses 之后
    assert second[assistant_idx + 3]["role"] == "system"
    assert "[失败自省]" in second[assistant_idx + 3]["content"]

    # C：任务正常完成
    assert result["final_result"]["status"] == "finished"

    # D：Reflection 仍是真实 Graph Node
    assert "reflection" in step_types(result)
    events = [event["event"] for event in result["graph_events"]]
    assert "reflection_created" in events


def test_v2_approval_multi_call_with_reflection_keeps_protocol(monkeypatch, tmp_path):
    """协议回归（Bug 3 E）：审批 + multi-call + reflection 组合路径。

    LLM#1 一次返回 [call_edit(高风险, 需审批), call_read(将失败)]：
    - 批准后：tool(call_edit) 直接追加；[审批后恢复上下文] 暂存 deferred
    - call_read 失败：tool(call_read) 直接追加；[失败自省] 暂存 deferred
    - 只有两个 tool response 都完成，才注入两个 feedback，再调用 LLM#2

    修复前：approval 的恢复上下文 system 消息会直接插在
    tool(call_edit) 与 tool(call_read) 之间，同样违反协议。
    """
    monkeypatch.setattr(change_verifier_module, "WORKSPACE_ROOT", tmp_path)

    plan = make_plan(
        [
            make_step(0, "修改文件", "edit_file", risk_level="high"),
            make_step(1, "读取文件", "read_file"),
        ],
        target_paths=["demo_project/v2_multi_adv.txt"],
        intents=["edit", "read"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    read_tool, read_calls = make_read_file_tool()
    read_calls["failed_paths"].add("demo_project/v2_multi_adv.txt")
    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={"edit_file": edit_tool, "read_file": read_tool},
    )

    responses = [
        make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_multi_adv.txt", "new_text": "adv"},
                    call_id="call_edit",
                ),
                make_fake_tool_call(
                    "read_file",
                    {"path": "demo_project/v2_multi_adv.txt"},
                    call_id="call_read",
                ),
            ]
        ),
        make_final_response("修改完成，读取失败。"),
    ]
    captured_messages = []

    def fake_create(*args, **kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    config = {"configurable": {"thread_id": "v2_approval_multi_reflection"}}
    interrupted = graph.invoke(make_initial_state(), config=config)
    assert interrupted.get("__interrupt__")
    assert interrupted["pending_action"]["tool_call_id"] == "call_edit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=config)

    # LLM 只被调用两次：call_edit 批准后不能调 LLM，必须继续处理 call_read
    assert len(captured_messages) == 2
    assert edit_calls["count"] == 1
    assert read_calls["count"] == 1
    # edit 步骤完成；read 失败 → retrying → degraded final accepted
    assert resumed["final_result"]["status"] == "finished"
    assert resumed["executor_state"]["completed_steps"] == 1

    # 第二次请求的协议顺序：assistant → tool(call_edit) → tool(call_read)
    # → system(审批后恢复) → system(失败自省)
    second = captured_messages[1]
    assistant_idx = next(
        i for i, m in enumerate(second) if m.get("tool_calls")
    )
    assert second[assistant_idx + 1]["role"] == "tool"
    assert second[assistant_idx + 1]["tool_call_id"] == "call_edit"
    assert second[assistant_idx + 2]["role"] == "tool"
    assert second[assistant_idx + 2]["tool_call_id"] == "call_read"
    assert second[assistant_idx + 3]["role"] == "system"
    assert "[审批后恢复上下文]" in second[assistant_idx + 3]["content"]
    assert second[assistant_idx + 4]["role"] == "system"
    assert "[失败自省]" in second[assistant_idx + 4]["content"]

    # Reflection 仍然以真实 Graph Node 形式存在
    assert "reflection" in step_types(resumed)
    events = [event["event"] for event in resumed["graph_events"]]
    assert "reflection_created" in events


def test_v2_approval_resume_deferred_flush_order_keeps_protocol(monkeypatch, tmp_path):
    """协议回归（Bug 4）：deferred feedback 持久化顺序必须与请求时序一致。

    场景：approval resume 产生 deferred system 反馈 → 下一轮 LLM 返回
    [call_read, call_list] 两个 tool_calls 且都成功 → 再调用下一轮 LLM。

    不变式：本轮请求是 old_history → deferred_feedback → instruction → LLM，
    模型返回 assistant_response 后，持久化增量必须是
    [*deferred_feedback, assistant_response]（deferred 属于本轮请求前的历史）。

    修复前 model_node 返回 [assistant_response, *deferred_feedback]：
    checkpoint 变成 assistant(tool_calls) → system(审批恢复) → tool(read) → tool(list)，
    本轮请求合法（deferred 只是临时拼进 request），下一轮从 checkpoint
    读历史就 400——正是“Round 3 成功、Round 4 才 400”的原因。
    """
    monkeypatch.setattr(change_verifier_module, "WORKSPACE_ROOT", tmp_path)

    plan = make_plan(
        [
            make_step(0, "修改文件", "edit_file", risk_level="high"),
            make_step(1, "读取文件", "read_file"),
            make_step(2, "列出文件", "list_files"),
        ],
        target_paths=["demo_project/v2_proto.txt"],
        intents=["edit", "read"],
        risk_level="high",
    )
    edit_tool, edit_calls = make_edit_file_tool(tmp_path)
    read_tool, read_calls = make_read_file_tool()
    list_tool, list_calls = make_list_files_tool()
    graph = build_graph(
        monkeypatch,
        plan=plan,
        available_tools={
            "edit_file": edit_tool,
            "read_file": read_tool,
            "list_files": list_tool,
        },
    )

    responses = [
        # Round 1：edit_file → 审批 interrupt
        make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_proto.txt", "new_text": "proto"},
                    call_id="call_edit",
                )
            ]
        ),
        # Round 2：审批恢复后，一次返回两个工具调用（read + supporting list）
        make_tool_response(
            [
                make_fake_tool_call(
                    "read_file",
                    {"path": "demo_project/v2_proto.txt"},
                    call_id="call_read",
                ),
                make_fake_tool_call(
                    "list_files",
                    {"path": "demo_project/v2_proto.txt"},
                    call_id="call_list",
                ),
            ]
        ),
        # Round 3：最终总结
        make_final_response("修改并检查完成。"),
    ]
    captured_messages = []

    def fake_create(*args, **kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(v2_nodes.llm.client.chat.completions, "create", fake_create)

    config = {"configurable": {"thread_id": "v2_deferred_flush_order"}}
    interrupted = graph.invoke(make_initial_state(), config=config)
    assert interrupted.get("__interrupt__")
    assert interrupted["pending_action"]["tool_call_id"] == "call_edit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=config)

    # 三次 LLM：edit 审批 → 两个 tool_call → 最终总结
    assert len(captured_messages) == 3
    assert edit_calls["count"] == 1
    assert read_calls["count"] == 1
    assert list_calls["count"] == 1
    assert resumed["final_result"]["status"] == "finished"
    assert resumed["executor_state"]["completed_steps"] == 3

    def marker(message):
        role = message.get("role")
        if role == "assistant":
            ids = [tc["id"] for tc in (message.get("tool_calls") or [])]
            if ids == ["call_edit"]:
                return "assistant_edit"
            if ids == ["call_read", "call_list"]:
                return "assistant_batch"
            return "assistant:" + ",".join(ids)
        if role == "tool":
            return "tool:" + message.get("tool_call_id", "")
        if role == "system":
            content = message.get("content", "")
            if "[审批后恢复上下文]" in content:
                return "approval_resume"
            if content.startswith("[失败自省]"):
                return "reflection"
            return "instruction"
        return str(role)

    # 第三次 LLM 请求的完整时序：审批恢复反馈（Round 2 flush 的 deferred）
    # 必须位于 Round 2 assistant(tool_calls) 之前，不能插在 assistant 之后。
    third = captured_messages[2]
    assert [marker(m) for m in third] == [
        "instruction",        # system_prompt（ephemeral）
        "user",               # 原始用户消息（ephemeral）
        "assistant_edit",     # Round 1 assistant(tool_calls=[call_edit])
        "tool:call_edit",     # approval 批准的 tool response
        "approval_resume",    # Round 2 flush 的 deferred feedback（历史，在响应前）
        "assistant_batch",    # Round 2 assistant(tool_calls=[call_read, call_list])
        "tool:call_read",     # Round 2 的 tool response
        "tool:call_list",     # Round 2 的 tool response
        "instruction",        # executor instruction（ephemeral，不持久化）
    ]

    # Graph State（checkpoint）messages 的实际持久化顺序也必须一致：
    # 不包含 system_prompt / user / instruction，且 approval_resume 在
    # assistant_batch 之前——修复前这里是反序 [assistant_batch, approval_resume]。
    assert [marker(m) for m in resumed["messages"]] == [
        "assistant_edit",
        "tool:call_edit",
        "approval_resume",
        "assistant_batch",
        "tool:call_read",
        "tool:call_list",
    ]
