"""Builds a synthetic ~/.claude tree. No test ever touches the real one."""

import json
import os
from typing import Any, Dict, List, Optional

from orchestra.locate import SessionPaths

T0 = "2026-09-09T05:00:00.000Z"


def ts(offset_s: int) -> str:
    """A timestamp offset_s seconds after T0."""
    minutes, seconds = divmod(offset_s, 60)
    hours, minutes = divmod(minutes, 60)
    return "2026-09-09T{:02d}:{:02d}:{:02d}.000Z".format(5 + hours, minutes, seconds)


def launch(tool_use_id: str, description: str, prompt: str, at: int,
           turn: str = "turn-1", model: str = "haiku") -> Dict[str, Any]:
    return {"uuid": turn, "timestamp": ts(at), "type": "assistant",
            "cwd": r"E:\proj",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Agent",
                 "input": {"description": description, "prompt": prompt,
                           "model": model}}]}}


def background_result(tool_use_id: str, agent_id: str, at: int) -> Dict[str, Any]:
    text = ("Async agent launched successfully.\n"
            "agentId: {} (internal ID)\n".format(agent_id))
    return {"uuid": "r-" + agent_id, "timestamp": ts(at), "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": [{"type": "text", "text": text}]}]}}


def notification(agent_id: str, tool_use_id: str, result: str, at: int,
                 status: str = "completed") -> Dict[str, Any]:
    body = ("<task-notification>\n<task-id>{}</task-id>\n"
            "<tool-use-id>{}</tool-use-id>\n<status>{}</status>\n"
            "<summary>done</summary>\n<result>{}</result>\n"
            "</task-notification>").format(agent_id, tool_use_id, status, result)
    return {"uuid": "n-" + agent_id + str(at), "timestamp": ts(at), "type": "user",
            "message": {"role": "user", "content": body}}


def agent_entry(blocks: List[Dict[str, Any]], at: int,
                usage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": blocks,
                               "model": "claude-haiku-4-5-20251001"}
    if usage:
        message["usage"] = usage
    return {"isSidechain": True, "timestamp": ts(at), "type": "assistant",
            "message": message}


def write_jsonl(path: str, entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def build_session(root: str, session_id: str = "s1") -> SessionPaths:
    """A three-agent run: planner writes a plan, two implementers read it.

    a1  plan      0s -> 60s   completed, wrote PLAN.md
    a2  impl one  70s -> 140s completed, read PLAN.md, quotes a1's result
    a3  impl two  70s -> ...  still running
    """
    project = os.path.join(root, "projects", "E--proj")
    session_jsonl = os.path.join(project, session_id + ".jsonl")
    subagents = os.path.join(project, session_id, "subagents")

    a1_result = ("Wrote the plan to PLAN.md. The parser must normalize windows "
                 "paths before comparing them or every artifact edge fails.")

    parent = [
        launch("toolu_1", "Plan the work",
               "You are planning.\n\n## Deliverable\n\nA written plan at PLAN.md.\n", 0),
        background_result("toolu_1", "a1", 1),
        notification("a1", "toolu_1", a1_result, 60),
        launch("toolu_2", "Implement part one",
               "Follow the plan.\n\n" + a1_result + "\n\n## Deliverable\n\nA commit.\n",
               70, turn="turn-2"),
        background_result("toolu_2", "a2", 71),
        launch("toolu_3", "Implement part two",
               "Follow the plan.\n\n## Deliverable\n\nAnother commit.\n",
               70, turn="turn-2"),
        background_result("toolu_3", "a3", 71),
        notification("a2", "toolu_2", "Implemented part one.", 140),
    ]
    write_jsonl(session_jsonl, parent)

    def meta(path: str, description: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "description": description,
                       "toolUseId": path_to_tool[path], "spawnDepth": 1,
                       "requestShape": "background", "model": "haiku"}, fh)

    path_to_tool = {}
    for agent_id, tool_use_id, description in (("a1", "toolu_1", "Plan the work"),
                                               ("a2", "toolu_2", "Implement part one"),
                                               ("a3", "toolu_3", "Implement part two")):
        os.makedirs(subagents, exist_ok=True)
        meta_path = os.path.join(subagents, "agent-{}.meta.json".format(agent_id))
        path_to_tool[meta_path] = tool_use_id
        meta(meta_path, description)

    write_jsonl(os.path.join(subagents, "agent-a1.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "w1", "name": "Write",
                      "input": {"file_path": r"E:\proj\PLAN.md", "content": "plan"}}], 30,
                    usage={"input_tokens": 100, "output_tokens": 50}),
        agent_entry([{"type": "text", "text": a1_result}], 59),
    ])
    write_jsonl(os.path.join(subagents, "agent-a2.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "r1", "name": "Read",
                      "input": {"file_path": r"E:\proj\PLAN.md"}}], 80,
                    usage={"input_tokens": 200, "output_tokens": 20}),
        agent_entry([{"type": "text", "text": "Implemented part one."}], 139),
    ])
    # a3's Read must be answered by a tool_result. An unanswered tool_use sets
    # AgentDigest.ended_mid_tool, and a mid-tool death outranks orphaned in the
    # status precedence — so without this the "orphaned when the session dies"
    # test would get `failed` instead.
    write_jsonl(os.path.join(subagents, "agent-a3.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "r2", "name": "Read",
                      "input": {"file_path": r"E:\proj\PLAN.md"}}], 80,
                    usage={"input_tokens": 210}),
        {"isSidechain": True, "type": "user", "timestamp": ts(81),
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "r2", "content": "plan"}]}},
    ])

    return SessionPaths(session_id=session_id, session_jsonl=session_jsonl,
                        subagents_dir=subagents, project_dir=project)


def build_nested_session(root: str, session_id: str = "n1") -> SessionPaths:
    """A depth-2 run: the orchestrator spawns a1, and a1 spawns a1b itself.

    spawnDepth > 1 does not appear in any real transcript yet (spec section 20),
    so this synthetic fixture is the only coverage nesting has. Keep it.
    """
    project = os.path.join(root, "projects", "E--proj")
    session_jsonl = os.path.join(project, session_id + ".jsonl")
    subagents = os.path.join(project, session_id, "subagents")

    write_jsonl(session_jsonl, [
        launch("toolu_1", "Lead the work", "You are the lead.\n", 0),
        background_result("toolu_1", "a1", 1),
        notification("a1", "toolu_1", "Lead work finished.", 120),
    ])

    os.makedirs(subagents, exist_ok=True)
    for agent_id, tool_use_id, depth, description in (
            ("a1", "toolu_1", 1, "Lead the work"),
            ("a1b", "toolu_2", 2, "Sub-task of the lead")):
        with open(os.path.join(subagents, "agent-{}.meta.json".format(agent_id)),
                  "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "description": description,
                       "toolUseId": tool_use_id, "spawnDepth": depth,
                       "requestShape": "background", "model": "haiku"}, fh)

    # The nested launch lives in a1's OWN transcript, marked isSidechain with
    # a1's agentId — that is what makes a1 the parent rather than the session.
    nested_launch = {
        "isSidechain": True, "agentId": "a1", "uuid": "turn-inner",
        "timestamp": ts(20), "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Agent",
             "input": {"description": "Sub-task of the lead",
                       "prompt": "Do the inner part.\n", "model": "haiku"}}]}}
    nested_result = {
        "isSidechain": True, "agentId": "a1", "uuid": "r-inner",
        "timestamp": ts(21), "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": [
                {"type": "text", "text": "Async agent launched successfully.\n"
                                         "agentId: a1b (internal ID)\n"}]}]}}

    write_jsonl(os.path.join(subagents, "agent-a1.jsonl"), [
        nested_launch, nested_result,
        agent_entry([{"type": "text", "text": "Lead work finished."}], 119),
    ])
    write_jsonl(os.path.join(subagents, "agent-a1b.jsonl"), [
        agent_entry([{"type": "text", "text": "Inner part done."}], 60),
    ])

    return SessionPaths(session_id=session_id, session_jsonl=session_jsonl,
                        subagents_dir=subagents, project_dir=project)
