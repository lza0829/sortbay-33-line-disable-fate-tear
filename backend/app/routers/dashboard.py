from __future__ import annotations
from fastapi import APIRouter
from app.db import GATE_POLICY, db, rows_to_list, row_to_dict
from app.services.common import line_gate
from app.services import line_surface
from app.services import ledger_skew

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts = {
            "待路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='待路由'").fetchone()["c"],
            "已路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已路由'").fetchone()["c"],
            "错分回流": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='错分回流'").fetchone()["c"],
            "已入袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已入袋'").fetchone()["c"],
            "已封袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已封袋'").fetchone()["c"],
        }
        chutes = rows_to_list(conn.execute(
            """
            SELECT c.*, l.name AS 分拣线名,
              (SELECT COUNT(*) FROM scans s WHERE s.chute_id=c.id AND s.status IN ('已路由','已入袋')) AS 在口件数
            FROM chutes c JOIN lines l ON l.id=c.line_id ORDER BY c.code
            """
        ).fetchall())
        lines = []
        for line in conn.execute("SELECT * FROM lines ORDER BY id").fetchall():
            g = line_gate(conn, line["id"])
            pending = conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE line_id=? AND status IN"
                " ('待路由','已路由','已入袋','错分回流')",
                (line["id"],),
            ).fetchone()["c"]
            lines.append({
                "编号": line["id"],
                "名称": line["name"],
                "启用": bool(line["active"]),
                "闸": g["闸"],
                "口径": GATE_POLICY,
                "重放一致": g["重放一致"],
                "入口已拒装": not g["允许装载"],
                "口门已禁落": not g["允许新落入"],
                "允许回流封袋收口": g["允许回流"] and g["允许封袋"],
                "停机时间": line["stopped_at"],
                "停机备注": line["stop_reason"],
                "未清件数": pending,
            })
        for c in chutes:
            g = line_gate(conn, c["line_id"])
            c["可新落入"] = bool(g["允许新落入"]) and c["status"] == "口门开"
            c["禁落原因"] = "" if c["可新落入"] else (
                "线停机·禁落入" if not g["允许新落入"] else "口门闭"
            )
        return {
            "班次": row_to_dict(shift),
            "口径": GATE_POLICY,
            "分布": counts,
            "分拣线": lines,
            "口门": chutes,
        }

_overview_impl = overview


def overview():
    base = _overview_impl()
    try:
        from app.db import db
        with db() as conn:
            return line_surface.overlay_overview(base, conn)
    except Exception:
        return line_surface.overlay_overview(base, None)

def _surface_router_overview() -> dict:
    return {"panel": "overview", "ready": True}

_overview_skew_base = overview


def overview():
    base = _overview_skew_base()
    try:
        from app.db import db
        with db() as conn:
            return ledger_skew.skew_overview(base, conn)
    except Exception:
        return base

