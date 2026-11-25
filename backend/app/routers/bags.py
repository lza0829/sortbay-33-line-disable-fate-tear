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

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int
    备注: str = ""


class BagAddIn(BaseModel):
    扫描编号: int


@router.get("/封袋")
def list_bags():
    with db() as conn:
        return rows_to_list(conn.execute(
            """
            SELECT b.*, c.code AS 口门码,
              (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数
            FROM bags b JOIN chutes c ON c.id=b.chute_id
            ORDER BY b.id DESC LIMIT 100
            """
        ).fetchall())


@router.post("/封袋/开袋")
def open_bag(body: BagOpenIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        # 开袋属收口，停机线也放行；口门须存在。
        chute = require_collect_allowed(conn, body.口门编号)
        code = f"袋-{chute['code']}-{shift['id']}-{now_str().replace(':','').replace(' ', '')[-10:]}"
        conn.execute(
            "INSERT INTO bags(shift_id, chute_id, code, status, note) VALUES (?, ?, ?, '集包中', ?)",
            (shift["id"], body.口门编号, code, body.备注),
        )
        row = conn.execute("SELECT * FROM bags WHERE code=?", (code,)).fetchone()
        audit(conn, "封袋", row["id"], "开袋", code)
        return row_to_dict(row)


@router.post("/封袋/{bag_id}/入袋")
def add_to_bag(bag_id: int, body: BagAddIn):
    with db() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋已封，禁止再入")
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] != "已路由":
            raise HTTPException(400, "仅已路由件可入袋")
        if scan["chute_id"] != bag["chute_id"]:
            raise HTTPException(400, "扫描口门与袋口门不一致")
        conn.execute(
            "UPDATE scans SET status='已入袋', bag_id=? WHERE id=?",
            (bag_id, body.扫描编号),
        )
        audit(conn, "封袋", bag_id, "入袋", str(body.扫描编号))
        return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone())


@router.post("/封袋/{bag_id}/封口")
def seal_bag(bag_id: int):
    with db() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋状态不可封口")
        n = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
        ).fetchone()["c"]
        if n == 0:
            raise HTTPException(400, "空袋不可封口")
        conn.execute(
            "UPDATE bags SET status='已封袋', sealed_at=? WHERE id=?",
            (now_str(), bag_id),
        )
        conn.execute(
            "UPDATE scans SET status='已封袋' WHERE bag_id=?",
            (bag_id,),
        )
        audit(conn, "封袋", bag_id, "封口", f"件数{n}")
        return row_to_dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())
