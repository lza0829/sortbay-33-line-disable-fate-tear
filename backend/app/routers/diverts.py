from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list, row_to_dict
from app.services.common import (
    audit,
    current_shift,
    now_str,
    require_collect_allowed,
    require_unsealed,
)

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int
    备注: str = ""


@router.get("/回流批次")
def list_batches():
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM divert_batches ORDER BY id DESC LIMIT 100"
        ).fetchall())


@router.post("/回流批次")
def create_divert(body: DivertIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] not in ("已路由", "已入袋"):
            raise HTTPException(400, "仅已路由或已入袋可标错分回流")
        # 回流属收口：停机线关联口门也放行，仅校验口门存在。
        if scan["chute_id"]:
            require_collect_allowed(conn, scan["chute_id"])
        code = f"回流-{shift['id']}-{now_str().replace(' ', '').replace(':', '')[-8:]}"
        conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift["id"], code, now_str(), body.备注),
        )
        bid = conn.execute("SELECT id FROM divert_batches WHERE code=?", (code,)).fetchone()["id"]
        conn.execute(
            "UPDATE scans SET status='错分回流', divert_batch_id=?, bag_id=NULL WHERE id=?",
            (bid, body.扫描编号),
        )
        if scan["chute_id"]:
            conn.execute(
                "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
                (body.扫描编号, scan["chute_id"], "错分回流", now_str(), code),
            )
        audit(conn, "回流", bid, "建回流批次", str(body.扫描编号))
        return {
            "批次": row_to_dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
            "扫描": row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()),
        }
