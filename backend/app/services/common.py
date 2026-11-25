from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException
from app.db import GATE_POLICY, connect


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
# 唯一正确性口径（架构 B：线停缺口台账）：
#   闸态只由 line_events 重放得出；停机后拒写/误冻死必须同事务落 line_stop_gaps；
#   存在未清缺口即「缺口冻结」，已停宣称冻结、总览不得假装清场、禁止带缺口恢复开机。
#   回流/封袋属收口，任何线态都放行——在途件不得被误冻死。
# ---------------------------------------------------------------------------

GATE_OPEN = "开"
GATE_STOPPED = "拒装禁落"

# 缺口类型：停机后拒新入站 / 停机后拒新落入 / 在途件被通道闸误冻死
GAP_LOAD_REJECT = "停机拒入站"
GAP_ROUTE_REJECT = "停机拒落入"
GAP_INFLIGHT_FROZEN = "在途误冻死"


def latest_line_event(conn, line_id: int):
    return conn.execute(
        "SELECT * FROM line_events WHERE line_id=? ORDER BY id DESC LIMIT 1", (line_id,)
    ).fetchone()


def latest_stop_event(conn, line_id: int):
    return conn.execute(
        "SELECT * FROM line_events WHERE line_id=? AND action='停机' ORDER BY id DESC LIMIT 1",
        (line_id,),
    ).fetchone()


def record_line_event(conn, line_id: int, action: str, gate: str, chute_count: int, note: str = ""):
    cur = conn.execute(
        "INSERT INTO line_events(line_id, action, gate, chute_count, created_at, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (line_id, action, gate, chute_count, now_str(), note),
    )
    return conn.execute("SELECT * FROM line_events WHERE id=?", (cur.lastrowid,)).fetchone()


def _outbox_conn():
    from app.db import connect_outbox
    return connect_outbox()


def _outbox_audit(oc, action: str, note: str, ref_id=None) -> None:
    """缺口审计写在 outbox 库，与拒写证据同事务同命运，主事务回滚也不丢。"""
    oc.execute(
        "INSERT INTO outbox_audit(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        ("线停缺口", ref_id, action, now_str(), note),
    )


def record_gap(conn, line_id: int, kind: str, note: str,
               barcode: str = "", chute_id: int | None = None, scan_id: int | None = None):
    """拒写同命运：证据先在独立 outbox 库权威落盘（含审计），永不随被拒请求的主事务回滚；
    再在当前主事务内镜像一行 line_stop_gaps，供主库事件重放 JOIN。
    - 请求提交：镜像与主事务一起落库；
    - 请求回滚（409/500）：镜像回滚，outbox 权威行仍在，下次读账时 sync 补镜像。"""
    stop_ev = latest_stop_event(conn, line_id)
    event_id = stop_ev["id"] if stop_ev else None
    oc = _outbox_conn()
    try:
        cur = oc.execute(
            """
            INSERT INTO gap_outbox(line_id, event_id, kind, barcode, chute_id, scan_id,
                                   status, created_at, note)
            VALUES (?, ?, ?, ?, ?, ?, '未清', ?, ?)
            """,
            (line_id, event_id, kind, barcode, chute_id, scan_id, now_str(), note),
        )
        outbox_id = cur.lastrowid
        _outbox_audit(oc, kind, note, ref_id=outbox_id)
        oc.commit()
    finally:
        oc.close()
    # 主库镜像仅尝试性写入：请求提交则持久，请求回滚则由 sync_gaps 在读账时幂等补提。
    conn.execute(
        """
        INSERT INTO line_stop_gaps
          (line_id, event_id, kind, barcode, chute_id, scan_id, status, created_at, note, outbox_id)
        VALUES (?, ?, ?, ?, ?, ?, '未清', ?, ?, ?)
        """,
        (line_id, event_id, kind, barcode, chute_id, scan_id, now_str(), note, outbox_id),
    )


def sync_gaps(conn, line_id: int | None = None) -> None:
    """以 outbox 权威台账补全主库镜像（幂等）。
    只依据主库是否已存在该 outbox_id 的镜像来决定补提，不依赖任何跨库标记——
    读账事务正常提交后镜像即持久；曾随写请求回滚而丢失的镜像在这里被补回，证据不丢。"""
    oc = _outbox_conn()
    try:
        if line_id is None:
            rows = oc.execute("SELECT * FROM gap_outbox ORDER BY id").fetchall()
        else:
            rows = oc.execute(
                "SELECT * FROM gap_outbox WHERE line_id=? ORDER BY id", (line_id,)
            ).fetchall()
        pending = [dict(r) for r in rows]
    finally:
        oc.close()
    for ob in pending:
        exists = conn.execute(
            "SELECT 1 FROM line_stop_gaps WHERE outbox_id=?", (ob["id"],)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO line_stop_gaps
              (line_id, event_id, kind, barcode, chute_id, scan_id, status, created_at, note, outbox_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ob["line_id"], ob["event_id"], ob["kind"], ob["barcode"], ob["chute_id"],
             ob["scan_id"], ob["status"], ob["created_at"], ob["note"], ob["id"]),
        )


def count_open_gaps(conn, line_id: int) -> int:
    """权威口径：直接数 outbox 未清缺口，不依赖主事务是否提交。"""
    oc = _outbox_conn()
    try:
        return oc.execute(
            "SELECT COUNT(*) c FROM gap_outbox WHERE line_id=? AND status='未清'", (line_id,)
        ).fetchone()["c"]
    finally:
        oc.close()


def count_all_open_gaps() -> int:
    """全局未清缺口（outbox 权威），供总览/班次对账判断是否清场。"""
    oc = _outbox_conn()
    try:
        return oc.execute(
            "SELECT COUNT(*) c FROM gap_outbox WHERE status='未清'"
        ).fetchone()["c"]
    finally:
        oc.close()


def list_gaps(conn, line_id: int, status: str | None = None):
    """以 outbox 权威台账为准返回缺口（字段对齐 line_stop_gaps，含 id=outbox 主键）。"""
    sync_gaps(conn, line_id)
    oc = _outbox_conn()
    try:
        if status:
            rows = oc.execute(
                "SELECT o.id, o.line_id, o.event_id, o.kind, o.barcode, o.chute_id, o.scan_id,"
                " o.status, o.created_at, o.resolved_at, '' AS resolve_note, o.note, o.id AS outbox_id"
                " FROM gap_outbox o WHERE o.line_id=? AND o.status=? ORDER BY o.id",
                (line_id, status),
            ).fetchall()
        else:
            rows = oc.execute(
                "SELECT o.id, o.line_id, o.event_id, o.kind, o.barcode, o.chute_id, o.scan_id,"
                " o.status, o.created_at, o.resolved_at, '' AS resolve_note, o.note, o.id AS outbox_id"
                " FROM gap_outbox o WHERE o.line_id=? ORDER BY o.id",
                (line_id,),
            ).fetchall()
        return rows
    finally:
        oc.close()


def resolve_gap(conn, gap_id: int, note: str = ""):
    """核销：outbox 权威状态先置已清（独立库），主库镜像同事务同步。"""
    oc = _outbox_conn()
    try:
        ob = oc.execute("SELECT * FROM gap_outbox WHERE id=?", (gap_id,)).fetchone()
        if not ob:
            raise HTTPException(404, "线停缺口不存在")
        if ob["status"] != "未清":
            raise HTTPException(409, "缺口已清，禁止重复核销")
        ts = now_str()
        oc.execute(
            "UPDATE gap_outbox SET status='已清', resolved_at=? WHERE id=?", (ts, gap_id)
        )
        _outbox_audit(oc, "核销", note or "现场清账", ref_id=gap_id)
        oc.commit()
        line_id = ob["line_id"]
    finally:
        oc.close()
    sync_gaps(conn, line_id)
    conn.execute(
        "UPDATE line_stop_gaps SET status='已清', resolved_at=?, resolve_note=? WHERE outbox_id=?",
        (ts, note, gap_id),
    )
    audit(conn, "线停缺口", gap_id, "核销", note or "现场清账")
    oc2 = _outbox_conn()
    try:
        return oc2.execute("SELECT * FROM gap_outbox WHERE id=?", (gap_id,)).fetchone()
    finally:
        oc2.close()


def gap_audit_events():
    """outbox 权威缺口审计流水（供审计端点与主库审计合并）。"""
    oc = _outbox_conn()
    try:
        return oc.execute(
            "SELECT id, kind, ref_id, action, created_at, note FROM outbox_audit ORDER BY id DESC LIMIT 200"
        ).fetchall()
    finally:
        oc.close()


def replay_gate(conn, line_id: int) -> dict:
    """重放口径：线事件流重放闸态 × lines.active × outbox 缺口归属事件，三方一致才可宣称一致。"""
    sync_gaps(conn, line_id)
    line = conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
    if not line:
        raise HTTPException(404, "分拣线不存在")
    ev = latest_line_event(conn, line_id)
    stopped_by_event = ev is not None and ev["action"] == "停机"
    gate = GATE_STOPPED if stopped_by_event else GATE_OPEN

    open_gaps = count_open_gaps(conn, line_id)
    # 孤儿缺口以权威 outbox 为准：挂不到本线停机事件上即证据撕裂。
    # line_events 在主库、gap_outbox 在独立库，先在主库取合法停机事件 id 再到 outbox 比对。
    stop_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM line_events WHERE line_id=? AND action='停机'", (line_id,)
        ).fetchall()
    ]
    oc = _outbox_conn()
    try:
        if stop_ids:
            placeholders = ",".join("?" * len(stop_ids))
            orphan_gaps = oc.execute(
                f"SELECT COUNT(*) c FROM gap_outbox WHERE line_id=? AND status='未清'"
                f" AND (event_id IS NULL OR event_id NOT IN ({placeholders}))",
                [line_id, *stop_ids],
            ).fetchone()["c"]
        else:
            orphan_gaps = oc.execute(
                "SELECT COUNT(*) c FROM gap_outbox WHERE line_id=? AND status='未清'",
                (line_id,),
            ).fetchone()["c"]
    finally:
        oc.close()
    # 闸态与应用态拧花，或缺口挂不到任何停机事件上，都视为重放不一致。
    drift = (gate == GATE_OPEN) != bool(line["active"])
    consistent = not drift and orphan_gaps == 0
    return {
        "分拣线编号": line_id,
        "分拣线名": line["name"],
        "启用": bool(line["active"]),
        "闸": gate,
        "口径": GATE_POLICY,
        "事件编号": ev["id"] if ev else None,
        "事件动作": ev["action"] if ev else None,
        "未清缺口数": open_gaps,
        "缺口冻结": open_gaps > 0,
        "重放一致": consistent,
    }


def line_gate(conn, line_id: int) -> dict:
    g = replay_gate(conn, line_id)
    g.update({
        "允许装载": g["闸"] == GATE_OPEN,
        "允许新落入": g["闸"] == GATE_OPEN,
        # 收口（回流/封袋）恒放行：在途件命运不被线停误冻。
        "允许回流": True,
        "允许封袋": True,
    })
    return g


def require_loading_allowed(conn, line_id: int, barcode: str = "") -> None:
    g = line_gate(conn, line_id)
    if not g["允许装载"]:
        record_gap(
            conn, line_id, GAP_LOAD_REJECT,
            f"停机后新入站拒写·{g['分拣线名']}·条码{barcode or '未提供'}",
            barcode=barcode,
        )
        raise HTTPException(409, f"分拣线「{g['分拣线名']}」已停机检修，拒绝新过机装载（已记线停缺口）")


def require_routing_allowed(conn, chute, barcode: str = "", scan_id: int | None = None) -> None:
    g = line_gate(conn, chute["line_id"])
    if not g["允许新落入"]:
        record_gap(
            conn, chute["line_id"], GAP_ROUTE_REJECT,
            f"停机后禁止新落入·口门{chute['code']}·条码{barcode or '未提供'}",
            barcode=barcode, chute_id=chute["id"], scan_id=scan_id,
        )
        raise HTTPException(409, f"分拣线「{g['分拣线名']}」已停机检修，口门 {chute['code']} 禁止新落入（已记线停缺口）")


def require_collect_allowed(conn, chute_id: int):
    """回流/入袋/封口属收口，任何线态都放行；仅校验口门存在。"""
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
    if not chute:
        raise HTTPException(404, "口门不存在")
    return chute
