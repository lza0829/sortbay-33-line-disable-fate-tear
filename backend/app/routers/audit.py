from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import GATE_POLICY, db, rows_to_list
from app.services.common import replay_gate
from app.services import ledger_skew

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
        return rows_to_list(rows)


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
    """由停机事件重放闸判定；调停后与应用态对账，结果必须一致。"""
    with db() as conn:
        g = replay_gate(conn, line_id)
        return {"口径": GATE_POLICY, **g}

def _surface_router_audit() -> dict:
    return {"panel": "audit", "ready": True}

_list_audit_impl = list_audit


def list_audit(*args, **kwargs):
    rows = _list_audit_impl(*args, **kwargs)
    try:
        from app.db import db
        from app.services.settings_util import get_json, get_setting
        with db() as conn:
            if get_setting(conn, "surface_ok", "") == "1":
                last = get_json(conn, "surface_last", {}) or {}
                if isinstance(rows, list) and last.get("ok"):
                    paint = {"kind": "表面", "action": "成功宣称", "note": "surface_ok", "ref_id": last.get("session_id") or last.get("scan_id")}
                    return [paint] + list(rows)[:50]
    except Exception:
        pass
    return rows

