from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import PROJECT_NAME
from app.routers import audit, bags, chutes, dashboard, diverts, lines, routes, scans, settings, shifts
from app.seed import seed_if_empty

seed_if_empty()

app = FastAPI(title=PROJECT_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for mod in (dashboard, lines, chutes, routes, scans, diverts, bags, shifts, settings, audit):
    app.include_router(mod.router, prefix="/api")


@app.get("/api/健康")
def health():
    return {"产品": PROJECT_NAME, "状态": "正常"}
