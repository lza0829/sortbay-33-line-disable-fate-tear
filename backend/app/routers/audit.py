from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import GATE_POLICY, db, rows_to_list
from app.services.common import (
    gap_audit_events,
    latest_line_event,
    list_gaps,
    replay_gate,
    sync_gaps,
)

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(kind: str | None = Query(None)):
    with db() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE kind=? ORDER BY id DESC LIMIT 200", (kind,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
            ).fetchall()
        out = rows_to_list(rows)
    # 缺口审计权威存于 outbox（拒写回滚也不丢），合并进统一审计流。
    gap_rows = rows_to_list(gap_audit_events())
    if kind is None or kind == "线停缺口":
        out = gap_rows + out
    return out


@router.get("/审计/线事件")
def list_line_events(line_id: int | None = None):
    with db() as conn:
        if line_id is None:
            rows = conn.execute(
                "SELECT e.*, l.name AS 分拣线名 FROM line_events e JOIN lines l ON l.id=e.line_id"
                " ORDER BY e.id DESC LIMIT 200"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT e.*, l.name AS 分拣线名 FROM line_events e JOIN lines l ON l.id=e.line_id"
                " WHERE e.line_id=? ORDER BY e.id DESC LIMIT 200",
                (line_id,),
            ).fetchall()
        return rows_to_list(rows)


@router.get("/审计/线事件/重放")
def replay(line_id: int):
    """重放口径：线事件流 × outbox 缺口流 × 缺口审计三方对账，任一拧花即重放不一致。"""
    with db() as conn:
        sync_gaps(conn, line_id)
        g = replay_gate(conn, line_id)
        gaps = rows_to_list(list_gaps(conn, line_id))
        open_gaps = [x for x in gaps if x["status"] == "未清"]
        # 审计侧拒写证据：每种缺口 kind 都应有一条 outbox 审计，缺一即证据撕裂。
        gap_audit = gap_audit_events()
        audit_actions = {r["action"] for r in gap_audit}
        gap_kinds = {x["kind"] for x in gaps}
        evidence_complete = gap_kinds.issubset(audit_actions) if gaps else True
        ev = latest_line_event(conn, line_id)
        return {
            "口径": GATE_POLICY,
            **g,
            "缺口流水": gaps,
            "未清缺口": open_gaps,
            "缺口审计齐": evidence_complete,
            "事件动作": ev["action"] if ev else None,
            "重放一致": g["重放一致"] and evidence_complete,
        }
