"""FastAPI app for the STEM kit: routes and WebSocket.

Run from the stem_kit folder:
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import csv
import io
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import ai, db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


# ------------------------------------------------------------------ models
# These shapes match the API the ESP32-S3 will use.


class ReadingIn(BaseModel):
    kit_id: str = Field(min_length=1, max_length=32)
    group: str = Field(default="", max_length=32)
    experiment: str = Field(min_length=1, max_length=32)
    sensor: str = Field(min_length=1, max_length=32)
    value: float
    unit: str = Field(default="", max_length=16)
    ts: Optional[float] = None  # seconds since 1970. If missing, the laptop time is used.


class RunIn(BaseModel):
    kit_id: str = Field(min_length=1, max_length=32)
    experiment: str = Field(min_length=1, max_length=32)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    notes: str = Field(default="", max_length=20000)


class SessionStartIn(BaseModel):
    name: str = Field(default="", max_length=80)


class KitIn(BaseModel):
    kit_id: str = Field(min_length=1, max_length=32)
    group: str = Field(min_length=1, max_length=32)


# --------------------------------------------------------------- WebSocket


class Hub:
    """Keeps the open browser connections and sends messages to all of them."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send_all(self, message: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()


async def publish_alert(alert: dict) -> None:
    """Called by the AI when it has saved a new alert."""
    await hub.send_all({"type": "alert", "data": alert})


# The local AI. It checks every new reading and creates alerts.
engine = ai.Engine(publish_alert)


# --------------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Smart STEM Experiment Kit", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def home():
    page = STATIC_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return {"message": "The backend is running. The web pages are in the static folder. Try /api/kits"}


# ---------------------------------------------------------------- readings


@app.post("/api/readings")
async def post_reading(reading: ReadingIn):
    saved = db.insert_reading(reading.model_dump())
    await hub.send_all({"type": "reading", "data": saved})
    engine.observe(saved)  # Never raises. The reading is already saved.
    return {"ok": True, "id": saved["id"]}


@app.get("/api/readings")
async def get_readings(kit_id: Optional[str] = None, limit: int = Query(200, ge=1, le=5000)):
    return db.get_readings(kit_id, limit)


# -------------------------------------------------------------------- kits


@app.get("/api/kits")
async def get_kits():
    return db.get_kits()


@app.post("/api/kits")
async def add_kit(kit: KitIn):
    # Needed for "Add group" in the teacher view.
    return db.add_kit(kit.kit_id, kit.group)


# -------------------------------------------------------------------- runs


@app.post("/api/runs")
async def post_run(run: RunIn):
    return db.create_run(run.model_dump())


@app.get("/api/runs")
async def get_runs(group: Optional[str] = None):
    return db.get_runs(group)


# ------------------------------------------------------------------ alerts


@app.get("/api/alerts")
async def get_alerts(resolved: Optional[bool] = None):
    return db.get_alerts(resolved)


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int):
    alert = db.resolve_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


# --------------------------------------------------------------- sessions


@app.post("/api/sessions/start")
async def start_session(body: SessionStartIn):
    return db.start_session(body.name)


@app.post("/api/sessions/end")
async def end_session():
    session = db.end_session()
    if session is None:
        raise HTTPException(status_code=400, detail="No session is running")
    return session


@app.get("/api/sessions")
async def get_sessions():
    # Needed so the teacher view can pick a session to export.
    return db.get_sessions()


# ------------------------------------------------------------------ export


@app.get("/api/export.csv")
async def export_csv(session_id: Optional[int] = None):
    rows = db.export_rows(session_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["ts", "time", "kit_id", "group", "experiment", "sensor", "value", "unit", "session_id"])
    for r in rows:
        writer.writerow(
            [
                r["ts"],
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                r["kit_id"],
                r["group"],
                r["experiment"],
                r["sensor"],
                r["value"],
                r["unit"],
                r["session_id"] if r["session_id"] is not None else "",
            ]
        )
    name = f"stem_kit_session_{session_id}.csv" if session_id is not None else "stem_kit_all.csv"
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------- WebSocket


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Pushes every new reading and every new alert to the browser.

    Messages look like {"type": "reading", "data": {...}}
    and {"type": "alert", "data": {...}}.
    """
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # Browsers send nothing. This keeps the socket open.
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


# Web pages. Mounted last so it never hides an API route.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
