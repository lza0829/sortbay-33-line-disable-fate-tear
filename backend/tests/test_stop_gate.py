"""线停命运夹具（架构 B：线停缺口台账）。

钉死的不变量：
  1. 停机可先亮，但停机后拒新入站/拒新落入必须同事务落 line_stop_gaps；
  2. 在途件收口（回流/封袋）恒放行，被误冻死即缺口、整笔回滚；
  3. 未清缺口 => 已停宣称冻结、总览不得假装清场、禁止带缺口恢复开机；
  4. 半截停机（事务内任何写口失败）整笔回滚：无停机事件、无残留缺口、线仍启用；
  5. 重放 = 线事件流 ∪ 缺口流 ∪ 拒写审计，三方同命运才给重放一致；
  6. 线页与口门页同闸同源，不允许各报各的。

运行：cd backend && ../.venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

POLICY = "线停缺口台账"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("SORTBAY_DB", str(db_path))
    # config 在 import 时读环境变量，故先清模块缓存。
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        sys.modules.pop(mod)
    from app.seed import seed_if_empty

    seed_if_empty()
    from app.main import app

    with TestClient(app) as c:
        c.db_path = db_path
        yield c


def stop_line(c, line_id=1, reason="停机检修"):
    r = c.post(f"/api/分拣线/{line_id}/启停", params={"启用": "false", "备注": reason})
    assert r.status_code == 200, r.text
    return r.json()


def start_line(c, line_id=1):
    r = c.post(f"/api/分拣线/{line_id}/启停", params={"启用": "true"})
    assert r.status_code == 200, r.text
    return r.json()


def load(c, barcode, dest="110", line_id=None):
    body = {"条码": barcode, "目的地码": dest}
    if line_id is not None:
        body["分拣线编号"] = line_id
    return c.post("/api/扫描", json=body)


def open_gaps(c, line_id=1):
    return c.get(f"/api/分拣线/{line_id}/缺口", params={"状态": "未清"}).json()


def resolve_first_gap(c, line_id=1, note="现场清账"):
    gaps = open_gaps(c, line_id)
    assert gaps, "预期存在未清缺口"
    r = c.post(f"/api/线停缺口/{gaps[0]['id']}/核销", json={"备注": note})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 夹具一：线停亮灯后仍新入站 —— 必须拒写且落可核缺口
# ---------------------------------------------------------------------------

def test_stop_rejects_new_loading_and_records_gap(client):
    scan = load(client, "A001")
    assert scan.status_code == 200
    sid = scan.json()["id"]
    assert client.post(f"/api/扫描/{sid}/路由").status_code == 200

    stop_line(client)

    r = load(client, "A002")
    assert r.status_code == 409 and "停机检修" in r.json()["detail"]
    # 拒写同命运：缺口可核、审计可查、扫描册无半截行
    gaps = open_gaps(client)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["kind"] == "停机拒入站" and g["barcode"] == "A002" and g["status"] == "未清"
    audit_kinds = {(e["kind"], e["action"]) for e in client.get("/api/审计").json()}
    assert ("线停缺口", "停机拒入站") in audit_kinds
    assert all(s["barcode"] != "A002" for s in client.get("/api/扫描").json())


def test_stop_without_line_id_still_rejects_with_409(client):
    """不指定线的装载：停机后必须按口径 409 拒写（而非 400 无可用线），且落缺口。"""
    stop_line(client)
    r = load(client, "A003")  # 不传分拣线编号
    assert r.status_code == 409, r.text
    assert open_gaps(client) and open_gaps(client)[0]["barcode"] == "A003"


def test_stop_blocks_new_routing_into_line_chutes_and_records_gap(client):
    pending = load(client, "B001")
    assert pending.status_code == 200
    sid = pending.json()["id"]
    stop_line(client)
    r = client.post(f"/api/扫描/{sid}/路由")
    assert r.status_code == 409 and "禁止新落入" in r.json()["detail"]
    # 该件仍停在待路由，未被半截写；拒落入落缺口
    row = [s for s in client.get("/api/扫描").json() if s["id"] == sid][0]
    assert row["status"] == "待路由"
    gaps = open_gaps(client)
    assert len(gaps) == 1 and gaps[0]["kind"] == "停机拒落入"
    assert gaps[0]["scan_id"] == sid and gaps[0]["barcode"] == "B001"


def test_scan_at_stop_moment_cannot_slip_through(client):
    """停机成功后口门页须显示禁落入；线页与口门页同源不拧花。"""
    stop_line(client)
    chutes = client.get("/api/口门").json()
    line1 = [c for c in chutes if c["line_id"] == 1]
    assert line1 and all(c["可新落入"] is False for c in line1)
    assert all(c["禁落原因"] == "线停机·禁落入" for c in line1)
    assert all(c["status"] == "口门开" for c in line1)
    assert load(client, "C001").status_code == 409
    # 线页闸灯与口门页闸灯同命运
    gate = client.get("/api/分拣线/1/闸").json()
    assert gate["闸"] == "拒装禁落"
    assert all(c["线闸"] == gate["闸"] for c in line1)


# ---------------------------------------------------------------------------
# 夹具二：在途件被误挡封袋 —— 收口恒放行，误冻死则整笔回滚
# ---------------------------------------------------------------------------

def test_inflight_collection_never_frozen_after_stop(client):
    """在途件的回流批次与开袋→入袋→封口，停机后必须全程走得动，且不得产生误冻死缺口。"""
    # 停机前备好两笔已路由在途件
    sid1 = load(client, "H001").json()["id"]
    sid2 = load(client, "H002").json()["id"]
    assert client.post(f"/api/扫描/{sid1}/路由").status_code == 200
    assert client.post(f"/api/扫描/{sid2}/路由").status_code == 200
    stop_line(client)

    # 回流属收口：停机后仍可建回流批次
    r = client.post("/api/回流批次", json={"扫描编号": sid1, "备注": "停机收口"})
    assert r.status_code == 200, r.text
    assert r.json()["扫描"]["status"] == "错分回流"

    # 封袋链对另一笔在途件全程放行
    bag = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert bag.status_code == 200, bag.text
    bid = bag.json()["id"]
    assert client.post(f"/api/封袋/{bid}/入袋", json={"扫描编号": sid2}).status_code == 200
    sealed = client.post(f"/api/封袋/{bid}/封口")
    assert sealed.status_code == 200 and sealed.json()["status"] == "已封袋"
    # 收口动作不得制造任何缺口
    assert open_gaps(client) == []


def test_frozen_inflight_gap_rolls_back_whole_write(client, monkeypatch):
    """模拟收口口被误冻：一旦在途件命运被冻死，记在途误冻死缺口并整笔回滚，扫描态不变。"""
    from app.services import common
    scan = load(client, "K001")
    sid = scan.json()["id"]
    assert client.post(f"/api/扫描/{sid}/路由").status_code == 200
    stop_line(client)

    real = common.require_collect_allowed

    def frozen(conn, chute_id):
        chute = real(conn, chute_id)
        common.record_gap(
            conn, chute["line_id"], common.GAP_INFLIGHT_FROZEN,
            f"清场臂误冻在途件·口门{chute['code']}·扫描{sid}",
            barcode="K001", chute_id=chute["id"], scan_id=sid,
        )
        from fastapi import HTTPException
        raise HTTPException(500, "清场臂误冻")

    monkeypatch.setattr(common, "require_collect_allowed", frozen)
    # bags/diverts 模块以 from-import 持有引用，须同步补丁
    from app.routers import bags as bags_router, diverts as diverts_router
    monkeypatch.setattr(bags_router, "require_collect_allowed", frozen)
    monkeypatch.setattr(diverts_router, "require_collect_allowed", frozen)

    r = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert r.status_code == 500
    # 整笔回滚：无半条袋、扫描册命运不变；但误冻证据由带外台账存活并提升为可核缺口
    assert client.get("/api/封袋").json() == []
    row = [s for s in client.get("/api/扫描").json() if s["id"] == sid][0]
    assert row["status"] == "已路由"
    gaps = open_gaps(client)
    assert len(gaps) == 1 and gaps[0]["kind"] == "在途误冻死"
    assert gaps[0]["scan_id"] == sid and gaps[0]["barcode"] == "K001"
    audit_kinds = {(e["kind"], e["action"]) for e in client.get("/api/审计").json()}
    assert ("线停缺口", "在途误冻死") in audit_kinds
    # 缺口未清 => 冻结宣称、禁止恢复开机
    assert client.post("/api/分拣线/1/启停", params={"启用": "true"}).status_code == 409
    resolve_first_gap(client)
    start_line(client)


# ---------------------------------------------------------------------------
# 夹具三：半截线停态 —— 事务内失败必须整体回滚，通道不得仍开却宣称已停
# ---------------------------------------------------------------------------

def test_half_stop_transaction_rolls_back_completely(client, monkeypatch):
    """在停机事务中途注入失败：线仍启用、无停机事件、无残留缺口，装载通道仍可进。"""
    from fastapi import HTTPException
    from app.services import common
    real = common.record_line_event
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1 and a[2] == "停机":
            raise HTTPException(500, "注入·写事件盘故障")
        return real(*a, **kw)

    monkeypatch.setattr(common, "record_line_event", flaky)
    from app.routers import lines as lines_router
    monkeypatch.setattr(lines_router, "record_line_event", flaky)

    r = client.post("/api/分拣线/1/启停", params={"启用": "false", "备注": "半截"})
    assert r.status_code == 500
    # 回滚后：线仍启用、无停机事件、闸仍开、无缺口、新装载可进
    line = [l for l in client.get("/api/分拣线").json() if l["id"] == 1][0]
    assert line["active"] == 1
    assert client.get("/api/分拣线/1/闸").json()["闸"] == "开"
    assert all(e["action"] != "停机" for e in
               client.get("/api/审计/线事件", params={"line_id": 1}).json())
    assert open_gaps(client) == []
    assert load(client, "R001").status_code == 200


def test_duplicate_stop_is_rejected_not_silently_half(client):
    stop_line(client)
    r = client.post("/api/分拣线/1/启停", params={"启用": "false"})
    assert r.status_code == 409
    g = client.get("/api/分拣线/1/闸").json()
    assert g["闸"] == "拒装禁落"
    # 重复点击不制造重复缺口
    assert open_gaps(client) == []


# ---------------------------------------------------------------------------
# 夹具四：线页与口门页拧花 —— 冻结宣称与验账恢复
# ---------------------------------------------------------------------------

def test_unresolved_gap_freezes_stop_claim_and_blocks_restart(client):
    stop_line(client)
    assert load(client, "G001").status_code == 409
    gaps = open_gaps(client)
    assert len(gaps) == 1

    # 已停宣称冻结：闸响应与总览都必须如实报未清场
    gate = client.get("/api/分拣线/1/闸").json()
    assert gate["缺口冻结"] is True and gate["未清缺口数"] == 1
    ov = client.get("/api/总览").json()
    line = [l for l in ov["分拣线"] if l["编号"] == 1][0]
    assert line["缺口冻结"] is True
    assert line["清场状态"] == "未清场·缺口冻结"
    assert ov["全局清场"] is False and ov["未清缺口总数"] == 1

    # 账未清不得恢复开机
    r = client.post("/api/分拣线/1/启停", params={"启用": "true"})
    assert r.status_code == 409 and "线停缺口" in r.json()["detail"]
    # 仍然拒装，没有带伤翻篇；第二次拒写再落一笔缺口，台账累加不吞证据
    assert load(client, "G002").status_code == 409
    assert len(open_gaps(client)) == 2

    # 逐笔核销后冻结解除，方可恢复
    resolve_first_gap(client)
    resolve_first_gap(client)
    assert open_gaps(client) == []
    start_line(client)
    g = client.get("/api/分拣线/1/闸").json()
    assert g["闸"] == "开" and g["缺口冻结"] is False and g["重放一致"] is True
    assert load(client, "G003").status_code == 200


def test_resolve_unknown_or_double_gap_rejected(client):
    stop_line(client)
    assert load(client, "G100").status_code == 409
    gid = open_gaps(client)[0]["id"]
    assert client.post(f"/api/线停缺口/99999/核销", json={}).status_code == 404
    assert client.post(f"/api/线停缺口/{gid}/核销", json={}).status_code == 200
    r = client.post(f"/api/线停缺口/{gid}/核销", json={})
    assert r.status_code == 409


def test_replay_triple_ledger_matches_after_gap_lifecycle(client):
    """重放 = 线事件流 ∪ 缺口流 ∪ 拒写审计；全周期一致。"""
    stop_line(client)
    assert load(client, "D001").status_code == 409
    rep = client.get("/api/审计/线事件/重放", params={"line_id": 1}).json()
    assert rep["口径"] == POLICY
    assert rep["闸"] == "拒装禁落" and rep["缺口冻结"] is True
    assert rep["缺口审计齐"] is True and rep["重放一致"] is True
    assert {g["kind"] for g in rep["缺口流水"]} == {"停机拒入站"}
    # 缺口必须挂在本次停机事件下
    assert all(g["event_id"] == rep["事件编号"] for g in rep["缺口流水"])

    resolve_first_gap(client)
    start_line(client)
    rep = client.get("/api/审计/线事件/重放", params={"line_id": 1}).json()
    assert rep["闸"] == "开" and rep["缺口冻结"] is False and rep["重放一致"] is True
    assert load(client, "D002").status_code == 200


def test_overview_exposes_entry_reject_gate_lock_and_gap(client):
    load(client, "E001")
    stop_line(client)
    assert load(client, "E002").status_code == 409
    ov = client.get("/api/总览").json()
    line = [l for l in ov["分拣线"] if l["编号"] == 1][0]
    assert line["入口已拒装"] is True
    assert line["口门已禁落"] is True
    assert line["允许回流封袋收口"] is True
    assert line["重放一致"] is True
    assert line["未清缺口数"] == 1 and line["缺口冻结"] is True
    assert ov["口径"] == POLICY and ov["全局清场"] is False
    # 总览不得携带任何假表面字段
    assert not any(k.startswith("surface") for k in ov)


def test_audit_carries_line_and_gap_refs(client):
    stop_line(client, reason="检修-02")
    assert load(client, "M001").status_code == 409
    kinds = {(e["kind"], e["action"]) for e in client.get("/api/审计").json()}
    assert ("分拣线", "停机检修") in kinds
    assert ("线停缺口", "停机拒入站") in kinds
    events = client.get("/api/审计/线事件").json()
    assert events[0]["action"] == "停机" and events[0]["note"] == "检修-02"
    # 审计流里不得出现伪造的表面成功宣称
    assert all(e["kind"] != "表面" for e in client.get("/api/审计").json())


# ---------------------------------------------------------------------------
# 并发：停机与装载只能「先装后停」或「先停后拒+缺口」，不得半截
# ---------------------------------------------------------------------------

def test_concurrent_stop_and_load_never_half_writes(client):
    errors = []
    results = []

    def worker(i):
        try:
            r = load(client, f"R{i:03d}")
            results.append((i, r.status_code))
        except Exception as e:  # noqa: BLE001 - 测试收集
            errors.append(e)

    stop_signal = threading.Event()

    def stopper():
        stop_signal.wait()
        stop_line(client)

    t = threading.Thread(target=stopper)
    t.start()
    stop_signal.set()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    t.join()

    assert not errors, errors
    for i, code in results:
        assert code in (200, 409), f"R{i:03d} -> {code}"
        rows = [s for s in client.get("/api/扫描").json() if s["barcode"] == f"R{i:03d}"]
        if code == 200:
            assert len(rows) == 1 and rows[0]["status"] == "待路由"
        else:
            assert rows == []
    # 被拒的每一笔都必须有同命运缺口，数量与 409 一一对应
    rejected = [i for i, code in results if code == 409]
    gaps = open_gaps(client)
    assert len(gaps) == len(rejected)
    assert {g["barcode"] for g in gaps} == {f"R{i:03d}" for i in rejected}


# ---------------------------------------------------------------------------
# 隔离性：只停一条线，不殃及其余线；班次对账同命运
# ---------------------------------------------------------------------------

def test_other_line_unaffected_and_gaps_are_scoped(client):
    created = client.post("/api/分拣线", json={"名称": "二号皮带", "启用": True})
    assert created.status_code == 200
    line2 = created.json()["id"]
    stop_line(client, 1)
    r = load(client, "F001", line_id=line2)
    assert r.status_code == 200
    # 缺口只挂一号线
    assert open_gaps(client, 1) == []  # 二号线未被尝试拒装，一号线本用例也没尝试
    assert client.get(f"/api/分拣线/{line2}/闸").json()["闸"] == "开"


def test_stopped_line_does_not_block_other_lines_routing(client):
    line2 = client.post("/api/分拣线", json={"名称": "二号皮带", "启用": True}).json()
    chute2 = client.post("/api/口门", json={"分拣线编号": line2["id"], "口门码": "Z01"}).json()
    assert client.post("/api/路由表", json={"目的地码": "777", "口门编号": chute2["id"]}).status_code == 200
    stop_line(client, 1)
    s = client.post("/api/扫描", json={"条码": "I001", "目的地码": "777", "分拣线编号": line2["id"]})
    assert s.status_code == 200
    r = client.post(f"/api/扫描/{s.json()['id']}/路由")
    assert r.status_code == 200, r.text
    assert r.json()["chute_id"] == chute2["id"]


def test_shift_reconcile_carries_gap_ledger(client):
    stop_line(client)
    assert load(client, "J001").status_code == 409
    rec = client.get("/api/班次/对账").json()
    assert rec["未清线停缺口"] == 1
    assert rec["全局清场"] is False
    line = [l for l in rec["分拣线"] if l["编号"] == 1][0]
    assert line["缺口冻结"] is True and line["未清缺口"] == 1
    # 核销后对账即清场
    resolve_first_gap(client)
    rec2 = client.get("/api/班次/对账").json()
    assert rec2["未清线停缺口"] == 0 and rec2["全局清场"] is True


# ---------------------------------------------------------------------------
# 老库迁移：无缺口表/无事件流也能补建并重放
# ---------------------------------------------------------------------------

def test_legacy_db_migration_backfills_replayable_event(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE lines (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                            active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE shifts (id INTEGER PRIMARY KEY AUTOINCREMENT, business_day TEXT NOT NULL,
                             name TEXT NOT NULL, sealed INTEGER NOT NULL DEFAULT 0, sealed_at TEXT,
                             UNIQUE(business_day, name));
        CREATE TABLE chutes (id INTEGER PRIMARY KEY AUTOINCREMENT, line_id INTEGER NOT NULL,
                             code TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                             status TEXT NOT NULL DEFAULT '口门开',
                             soft_cap INTEGER NOT NULL DEFAULT 200, UNIQUE(line_id, code));
        INSERT INTO lines(name, active) VALUES ('老一号线', 0);
        """
    )
    raw.commit()
    raw.close()

    monkeypatch.setenv("SORTBAY_DB", str(db_path))
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        sys.modules.pop(mod)
    from app.seed import seed_if_empty

    seed_if_empty()
    from app.main import app

    with TestClient(app) as c:
        rep = c.get("/api/审计/线事件/重放", params={"line_id": 1}).json()
        assert rep["闸"] == "拒装禁落" and rep["重放一致"] is True
        assert rep["缺口冻结"] is False
        # 缺口表已补建可写
        assert c.get("/api/分拣线/1/缺口").status_code == 200
        assert c.post("/api/扫描", json={"条码": "L001", "目的地码": "110"}).status_code == 400
