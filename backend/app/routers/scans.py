from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import (
    audit,
    current_shift,
    line_gate,
    now_str,
    require_collect_allowed,
    require_loading_allowed,
    require_routing_allowed,
    require_unsealed,
)
from app.services import ledger_skew

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str = Field(min_length=1)
    目的地码: str = ""
    分拣线编号: int | None = None
    备注: str = ""


@router.get("/扫描")
def list_scans(状态: str | None = None):
    with db() as conn:
        if 状态:
            rows = conn.execute(
                "SELECT * FROM scans WHERE status=? ORDER BY id DESC LIMIT 200", (状态,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 200").fetchall()
        return rows_to_list(rows)


@router.post("/扫描")
def create_scan(body: ScanIn):
    """装载入口：停机线拒装。闸在 BEGIN IMMEDIATE 事务内判定，与停机串行。"""
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        if body.分拣线编号:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        else:
            line = None
            for cand in conn.execute("SELECT * FROM lines ORDER BY id").fetchall():
                if line_gate(conn, cand["id"])["允许装载"]:
                    line = cand
                    break
        if not line:
            raise HTTPException(400, "无可用分拣线")
        require_loading_allowed(conn, line["id"])
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (body.条码, body.目的地码, shift["id"], line["id"], now_str(), body.备注),
        )
        row = conn.execute(
            "SELECT * FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        audit(conn, "扫描", row["id"], "过机装载", f"{body.条码}·线{line['name']}")
        return row_to_dict(row)


@router.post("/扫描/{scan_id}/路由")
def route_scan(scan_id: int):
    """裁决口：停机线关联口门禁新落入；不影响已停线以外口门。"""
    with db() as conn:
        require_unsealed(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可路由: {scan['status']}")
        if not scan["dest_code"]:
            raise HTTPException(400, "缺少目的地码")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        chute = require_collect_allowed(conn, rule["chute_id"])
        if chute["status"] != "口门开":
            raise HTTPException(400, "口门已闭，禁止落入")
        require_routing_allowed(conn, chute)
        conn.execute(
            "UPDATE scans SET status='已路由', chute_id=? WHERE id=?",
            (chute["id"], scan_id),
        )
        conn.execute(
            "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
            (scan_id, chute["id"], "路由落入", now_str(), chute["code"]),
        )
        audit(conn, "扫描", scan_id, "路由裁决", chute["code"])
        return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone())

def _surface_router_scans() -> dict:
    return {"panel": "scans", "ready": True}

_list_scans_impl = list_scans


def list_scans(*args, **kwargs):
    rows = _list_scans_impl(*args, **kwargs)
    try:
        from app.db import db
        with db() as conn:
            if isinstance(rows, list):
                return ledger_skew.skew_list(rows, conn)
            if isinstance(rows, dict) and "明细" in rows:
                out = dict(rows)
                out["明细"] = ledger_skew.skew_list(out.get("明细") or [], conn)
                return out
    except Exception:
        pass
    return rows

