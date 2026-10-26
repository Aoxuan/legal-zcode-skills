# -*- coding: utf-8 -*-
"""判断使用人是否为当前待办审批人，并拟审批意见。不调用同意接口。"""


def proposed_comment(contract_no):
    return "合同编号：%s" % (contract_no or "").strip()


def comment_has_number(comment, contract_no):
    number = (contract_no or "").strip()
    return bool(number) and number in (comment or "")


def task_id_of(task):
    if not isinstance(task, dict):
        return ""
    return str(task.get("id") or task.get("task_id") or "")


def pending_tasks(task_list, user_id):
    wanted = (user_id or "").strip()
    if not wanted:
        return []
    found = []
    for task in task_list or []:
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").upper() != "PENDING":
            continue
        ids = {str(task.get("user_id") or ""), str(task.get("open_id") or "")}
        ids.discard("")
        if wanted in ids:
            found.append(task)
    return found


def preview(user_id, contract_no, task_list, already_released=False, dry_run=False):
    """返回要并入录入结果的放行字段。不发同意请求。"""
    if dry_run:
        return {"release": "skipped", "release_reason": "干跑不放行"}
    if already_released:
        return {"release": "done"}
    if not (contract_no or "").strip():
        return {
            "release": "skipped",
            "release_reason": "合同编号尚未生成，台账已写入，未放行",
        }
    if not (user_id or "").strip():
        return {
            "release": "skipped",
            "release_reason": "尚未绑定使用人，台账已写入，未放行",
        }
    tasks = pending_tasks(task_list, user_id)
    if not tasks:
        return {
            "release": "skipped",
            "release_reason": "当前使用人不是这张单的待办审批人，台账已写入，未放行",
        }
    if len(tasks) > 1:
        return {
            "release": "skipped",
            "release_reason": "当前使用人在这张单上有多条待办，已停住，未放行",
        }
    task = tasks[0]
    return {
        "release": "pending_confirm",
        "task_id": task_id_of(task),
        "node_name": task.get("node_name") or "",
        "proposed_comment": proposed_comment(contract_no),
    }
