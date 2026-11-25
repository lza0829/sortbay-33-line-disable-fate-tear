from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import db
from app.services.common import audit, line_gate, require_collect_allowed

router = APIRouter(tags=["口门"])


class ChuteIn(BaseModel):
    分拣线编号: int
    口门码: str = Field(min_length=1)
    标题: str = ""
    软容量: int = 120


def _chute_view(conn, row) -> dict:
    d = dict(row)
    g = line_gate(conn, row["line_id"])
    d["线启停"] = "启用" if g["启用"] else "停机"
    d["线闸"] = g["闸"]
    # 停机后不论口门自状态如何，一律不可新落入——口门页不可显示成仍可裁决。
    d["可新落入"] = bool(g["允许新落入"]) and row["status"] == "口门开"
    d["禁落原因"] = "" if d["可新落入"] else (
        "线停机·禁落入" if not g["允许新落入"] else "口门闭"
    )
    d["允许收口"] = True
    return d


@router.get("/口门")
def list_chutes():
    with db() as conn:
        rows = conn.execute(
            "SELECT c.*, l.name AS 分拣线名, l.active AS 线启用 FROM chutes c"
            " JOIN lines l ON l.id=c.line_id ORDER BY c.code"
        ).fetchall()
        return [_chute_view(conn, r) for r in rows]


@router.post("/口门")
def create_chute(body: ChuteIn):
    with db() as conn:
        line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        if not line:
            raise HTTPException(404, "分拣线不存在")
        try:
            conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?, ?, ?, '口门开', ?)",
                (body.分拣线编号, body.口门码, body.标题, body.软容量),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute(
            "SELECT * FROM chutes WHERE line_id=? AND code=?",
            (body.分拣线编号, body.口门码),
        ).fetchone()
        g = line_gate(conn, body.分拣线编号)
        audit(conn, "口门", row["id"], "新建", f"{body.口门码}·线闸={g['闸']}")
        return _chute_view(conn, row)


@router.post("/口门/{chute_id}/开闭")
def set_chute(chute_id: int, 状态: str = Query(...)):
    if 状态 not in ("口门开", "口门闭"):
        raise HTTPException(400, "状态仅允许口门开或口门闭")
    with db() as conn:
        chute = require_collect_allowed(conn, chute_id)
        conn.execute("UPDATE chutes SET status=? WHERE id=?", (状态, chute_id))
        g = line_gate(conn, chute["line_id"])
        # 只改口门自状态；停机线即便「口门开」也不会因此放行新落入。
        audit(conn, "口门", chute_id, "开闭", f"{状态}·线闸={g['闸']}")
        row = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
        return _chute_view(conn, row)

def _surface_router_chutes() -> dict:
    return {"panel": "chutes", "ready": True}

