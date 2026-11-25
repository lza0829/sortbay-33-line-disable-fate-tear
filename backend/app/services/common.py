from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException
from app.db import GATE_POLICY
from app.services import line_surface


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_shift(conn):
    row = conn.execute(
        "SELECT * FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise HTTPException(400, "无未封班班次")
    return row


def audit(conn, kind: str, ref_id: int | None, action: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, action, now_str(), note),
    )


def require_unsealed(conn) -> None:
    s = current_shift(conn)
    if s["sealed"]:
        raise HTTPException(400, "班次已封班，禁止写口")


# ---------------------------------------------------------------------------
# 停机写闸：全局唯一口径「线停即门禁落」。
#   闸(business write gate) = 由该线最近一条 line_events 重放得出，不读临时变量。
#   停机后：装载入口拒装；指向该线口门的新裁决拒落入；回流与封袋收口仍开。
# ---------------------------------------------------------------------------

GATE_OPEN = "开"
GATE_STOPPED = "拒装禁落"


def latest_line_event(conn, line_id: int):
    return conn.execute(
        "SELECT * FROM line_events WHERE line_id=? ORDER BY id DESC LIMIT 1", (line_id,)
    ).fetchone()


def replay_gate(conn, line_id: int) -> dict:
    """只靠停机/恢复事件重放闸态；与 lines.active 交叉校验，拧花即报警。"""
    line = conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
    if not line:
        raise HTTPException(404, "分拣线不存在")
    ev = latest_line_event(conn, line_id)
    stopped_by_event = ev is not None and ev["action"] == "停机"
    gate = GATE_STOPPED if stopped_by_event else GATE_OPEN
    drift = (gate == GATE_OPEN) != bool(line["active"])
    return {
        "分拣线编号": line_id,
        "分拣线名": line["name"],
        "启用": bool(line["active"]),
        "闸": gate,
        "口径": GATE_POLICY,
        "事件编号": ev["id"] if ev else None,
        "事件动作": ev["action"] if ev else None,
        "重放一致": not drift,
    }


def line_gate(conn, line_id: int) -> dict:
    g = replay_gate(conn, line_id)
    g.update({
        "允许装载": g["闸"] == GATE_OPEN,
        "允许新落入": g["闸"] == GATE_OPEN,
        "允许回流": True,
        "允许封袋": True,
    })
    return g


def require_loading_allowed(conn, line_id: int) -> None:
    g = line_gate(conn, line_id)
    if not g["允许装载"]:
        raise HTTPException(409, f"分拣线「{g['分拣线名']}」已停机检修，拒绝新过机装载")


def require_routing_allowed(conn, chute) -> None:
    g = line_gate(conn, chute["line_id"])
    if not g["允许新落入"]:
        raise HTTPException(409, f"分拣线「{g['分拣线名']}」已停机检修，口门 {chute['code']} 禁止新落入")


def require_collect_allowed(conn, chute_id: int) -> None:
    """回流/入袋/封口属收口，任何线态都放行；仅校验口门存在。"""
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
    if not chute:
        raise HTTPException(404, "口门不存在")
    return chute


def record_line_event(conn, line_id: int, action: str, gate: str, chute_count: int, note: str = ""):
    conn.execute(
        "INSERT INTO line_events(line_id, action, gate, chute_count, created_at, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (line_id, action, gate, chute_count, now_str(), note),
    )
    return conn.execute("SELECT * FROM line_events ORDER BY id DESC LIMIT 1").fetchone()

_require_loading_allowed_impl = require_loading_allowed


def require_loading_allowed(*args, **kwargs):
    out = _require_loading_allowed_impl(*args, **kwargs)
    return line_surface.after_toggle(out, *args, **kwargs)

_line_gate_impl = line_gate


def line_gate(*args, **kwargs):
    out = _line_gate_impl(*args, **kwargs)
    return line_surface.after_toggle(out, *args, **kwargs)

_replay_gate_impl = replay_gate


def replay_gate(*args, **kwargs):
    out = _replay_gate_impl(*args, **kwargs)
    return line_surface.after_toggle(out, *args, **kwargs)

