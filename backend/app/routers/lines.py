from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import GATE_POLICY, db
from app.services.common import (
    GATE_OPEN,
    GATE_STOPPED,
    audit,
    latest_line_event,
    line_gate,
    record_line_event,
    replay_gate,
)
from app.services import line_surface

router = APIRouter(tags=["分拣线"])


class LineIn(BaseModel):
    名称: str = Field(min_length=1)
    启用: bool = True


def _line_view(conn, line) -> dict:
    d = dict(line)
    d["闸"] = line_gate(conn, line["id"])
    return d


@router.get("/分拣线")
def list_lines():
    with db() as conn:
        return [_line_view(conn, r) for r in conn.execute("SELECT * FROM lines ORDER BY id").fetchall()]


@router.get("/分拣线/{line_id}/闸")
def get_gate(line_id: int):
    with db() as conn:
        return replay_gate(conn, line_id)


@router.post("/分拣线")
def create_line(body: LineIn):
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO lines(name, active) VALUES (?, ?)",
                (body.名称, 1 if body.启用 else 0),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute("SELECT * FROM lines WHERE name=?", (body.名称,)).fetchone()
        record_line_event(
            conn, row["id"],
            "恢复" if body.启用 else "停机",
            GATE_OPEN if body.启用 else GATE_STOPPED,
            0, "新建分拣线",
        )
        audit(conn, "分拣线", row["id"], "新建", f"{body.名称}·{'启用' if body.启用 else '停用'}")
        return _line_view(conn, row)


@router.post("/分拣线/{line_id}/启停")
def toggle_line(line_id: int, 启用: bool = Query(...), 备注: str = Query("", max_length=200)):
    """线启停唯一入口：线与事件、口门禁落同一事务，事后可由事件重放。"""
    with db() as conn:
        line = conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
        if not line:
            raise HTTPException(404, "分拣线不存在")
        ev = latest_line_event(conn, line_id)
        currently_stopped = ev is not None and ev["action"] == "停机"
        if currently_stopped == (not 启用):
            raise HTTPException(409, "分拣线已是该状态，未重复停机")
        chute_count = conn.execute(
            "SELECT COUNT(*) c FROM chutes WHERE line_id=?", (line_id,)
        ).fetchone()["c"]
        if 启用:
            conn.execute(
                "UPDATE lines SET active=1, stopped_at=NULL, stop_reason='',"
                " state_version=state_version+1 WHERE id=?",
                (line_id,),
            )
            record_line_event(conn, line_id, "恢复", GATE_OPEN, chute_count, 备注 or "恢复开机")
            audit(conn, "分拣线", line_id, "恢复开机",
                  f"{line['name']}·闸={GATE_OPEN}·关联口门{chute_count}")
        else:
            from app.services.common import now_str
            conn.execute(
                "UPDATE lines SET active=0, stopped_at=?, stop_reason=?,"
                " state_version=state_version+1 WHERE id=?",
                (now_str(), 备注, line_id),
            )
            record_line_event(conn, line_id, "停机", GATE_STOPPED, chute_count, 备注 or "停机检修")
            audit(conn, "分拣线", line_id, "停机检修",
                  f"{line['name']}·闸={GATE_STOPPED}·关联口门{chute_count}一并禁新落入")
        row = conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
        return _line_view(conn, row)


@router.get("/分拣线/口径")
def policy():
    return {"口径": GATE_POLICY, "停装载": True, "禁新落入": True, "放行收口": True}

_toggle_line_impl = toggle_line


def toggle_line(*args, **kwargs):
    out = _toggle_line_impl(*args, **kwargs)
    return line_surface.after_toggle(out, *args, **kwargs)

def _surface_router_lines() -> dict:
    return {"panel": "lines", "ready": True}

