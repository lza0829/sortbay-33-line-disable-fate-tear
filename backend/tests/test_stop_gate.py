"""停机写闸夹具：覆盖「线停即门禁落」的四路写口、事件重放、并发半截与恢复后闸态。

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


def load(c, barcode, dest="110", line_id=1):
    return c.post("/api/扫描", json={"条码": barcode, "目的地码": dest, "分拣线编号": line_id})


def test_stop_rejects_new_loading_but_allows_collection(client):
    scan = load(client, "A001")
    assert scan.status_code == 200
    sid = scan.json()["id"]
    assert client.post(f"/api/扫描/{sid}/路由").status_code == 200

    stop_line(client)

    # 装载口：拒新件
    r = load(client, "A002")
    assert r.status_code == 409 and "停机检修" in r.json()["detail"]

    # 已在途件的封袋收口仍开：开袋→入袋→封口
    bag = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert bag.status_code == 200, bag.text
    bid = bag.json()["id"]
    assert client.post(f"/api/封袋/{bid}/入袋", json={"扫描编号": sid}).status_code == 200
    sealed = client.post(f"/api/封袋/{bid}/封口")
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["status"] == "已封袋"


def test_stop_blocks_new_routing_into_line_chutes(client):
    # 先备一笔待路由件，停机后裁决指向该线口门应被拒
    pending = load(client, "B001")
    assert pending.status_code == 200
    sid = pending.json()["id"]
    stop_line(client)
    r = client.post(f"/api/扫描/{sid}/路由")
    assert r.status_code == 409 and "禁止新落入" in r.json()["detail"]
    # 该件仍停在待路由，未被半截写
    row = [s for s in client.get("/api/扫描").json() if s["id"] == sid][0]
    assert row["status"] == "待路由"


def test_scan_at_stop_moment_cannot_slip_through(client):
    """停机成功后，任何新装载/新落入必须被挡；口门页须显示禁落入而非仍可裁决。"""
    stop_line(client)
    chutes = client.get("/api/口门").json()
    line1 = [c for c in chutes if c["line_id"] == 1]
    assert line1 and all(c["可新落入"] is False for c in line1)
    assert all(c["禁落原因"] == "线停机·禁落入" for c in line1)
    # 口门自身仍是「口门开」，但线闸压住
    assert all(c["status"] == "口门开" for c in line1)
    assert load(client, "C001").status_code == 409


def test_replay_gate_matches_after_stop_and_restart(client):
    stop_line(client)
    rep = client.get("/api/审计/线事件/重放", params={"line_id": 1}).json()
    assert rep["闸"] == "拒装禁落" and rep["重放一致"] is True
    start_line(client)
    rep = client.get("/api/审计/线事件/重放", params={"line_id": 1}).json()
    assert rep["闸"] == "开" and rep["重放一致"] is True
    # 恢复后闸态拧花检查：可装载、可落入、口门页恢复可新落入
    assert load(client, "D001").status_code == 200
    assert all(c["可新落入"] for c in client.get("/api/口门").json() if c["line_id"] == 1)


def test_duplicate_stop_is_rejected_not_silently_half(client):
    stop_line(client)
    r = client.post("/api/分拣线/1/启停", params={"启用": "false"})
    assert r.status_code == 409
    # 闸态不因重复点击抖动
    g = client.get("/api/分拣线/1/闸").json()
    assert g["闸"] == "拒装禁落"


def test_concurrent_stop_and_load_never_half_writes(client):
    """停机与装载并发：结果只能是「先装后停」或「先停后拒」，不得半截扫描。"""
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
    # 每条条码要么完整落库（200）要么被拒（409），不存在 5xx 或半行
    for i, code in results:
        assert code in (200, 409), f"R{i:03d} -> {code}"
        rows = [s for s in client.get("/api/扫描").json() if s["barcode"] == f"R{i:03d}"]
        if code == 200:
            assert len(rows) == 1 and rows[0]["status"] == "待路由"
        else:
            assert rows == []


def test_overview_exposes_entry_reject_and_gate_lock(client):
    load(client, "E001")
    stop_line(client)
    ov = client.get("/api/总览").json()
    line = [l for l in ov["分拣线"] if l["编号"] == 1][0]
    assert line["入口已拒装"] is True
    assert line["口门已禁落"] is True
    assert line["允许回流封袋收口"] is True
    assert line["重放一致"] is True
    assert ov["口径"] == "线停即门禁落"


def test_audit_carries_line_and_chute_refs(client):
    stop_line(client, reason="检修-02")
    kinds = {(e["kind"], e["action"]) for e in client.get("/api/审计").json()}
    assert ("分拣线", "停机检修") in kinds
    events = client.get("/api/审计/线事件").json()
    assert events[0]["action"] == "停机"
    assert events[0]["note"] == "检修-02"


def test_other_line_unaffected(client):
    """只覆盖单条线：停一号线不影响二号线（若存在）的装载与落入。"""
    created = client.post("/api/分拣线", json={"名称": "二号皮带", "启用": True})
    assert created.status_code == 200
    stop_line(client, 1)
    r = load(client, "F001", line_id=created.json()["id"])
    assert r.status_code == 200


def test_divert_still_open_on_stopped_line(client):
    """回流属收口：停机后对已有在途件仍可建回流批次。"""
    scan = load(client, "H001")
    sid = scan.json()["id"]
    assert client.post(f"/api/扫描/{sid}/路由").status_code == 200
    stop_line(client)
    r = client.post("/api/回流批次", json={"扫描编号": sid, "备注": "停机收口"})
    assert r.status_code == 200, r.text
    assert r.json()["扫描"]["status"] == "错分回流"


def test_stopped_line_does_not_block_other_lines_routing(client):
    """只停一号线时，指向二号线口门的裁决仍可落入。"""
    line2 = client.post("/api/分拣线", json={"名称": "二号皮带", "启用": True}).json()
    chute2 = client.post("/api/口门", json={"分拣线编号": line2["id"], "口门码": "Z01"}).json()
    assert client.post("/api/路由表", json={"目的地码": "777", "口门编号": chute2["id"]}).status_code == 200
    stop_line(client, 1)
    s = client.post("/api/扫描", json={"条码": "I001", "目的地码": "777", "分拣线编号": line2["id"]})
    assert s.status_code == 200
    r = client.post(f"/api/扫描/{s.json()['id']}/路由")
    assert r.status_code == 200, r.text
    assert r.json()["chute_id"] == chute2["id"]


def test_legacy_db_migration_backfills_replayable_event(tmp_path, monkeypatch):
    """老库无 line_events：迁移须补一条可重放的初始事件，闸态与 active 一致。"""
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
        assert c.post("/api/扫描", json={"条码": "L001", "目的地码": "110"}).status_code == 400
