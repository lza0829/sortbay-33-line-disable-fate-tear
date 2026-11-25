from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "分拣过机台"
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("SORTBAY_DB", str(ROOT / "data" / "sortbay.db")))
# 带外缺口台账：独立库文件，与主库写锁物理隔离，
# 使拒写意图在被拒请求事务回滚后仍可存活并随后补提为正式缺口。
OUTBOX_PATH = Path(os.environ.get(
    "SORTBAY_OUTBOX_DB",
    str(DB_PATH.parent / f"{DB_PATH.stem}_outbox{DB_PATH.suffix}"),
))
