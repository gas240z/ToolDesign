"""ShopPilot — FastAPI-сервер.

Запуск:   pip install -r requirements.txt
          uvicorn app:app --reload          →  http://127.0.0.1:8000
Ключ:     строка OPENAI_API_KEY=... в файле .env (читается при каждом запросе).
"""
import json
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

import agent

BASE = Path(__file__).parent
app = FastAPI(title="ShopPilot", description="ИИ-консультант магазина: ReAct-агент + structured output")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class AgentRequest(BaseModel):
    question: str = Field(min_length=2, max_length=600)
    mode: Literal["react", "tools"] = "react"
    history: list[Turn] = []


class TicketRequest(BaseModel):
    text: str = Field(min_length=5, max_length=1500)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE / "index.html")


@app.get("/api/status")
def status():
    return {"ai_ready": bool(agent.api_key()), "model": agent.env("OPENAI_MODEL", "gpt-4o-mini")}


@app.get("/api/catalog")
def catalog():
    return {
        "products": [{"id": k, **{f: v[f] for f in ("name", "price", "stock", "rating", "icon")}} for k, v in agent.CATALOG.items()],
        "tools": [{"name": s.name, "title": s.title, "icon": s.icon, "desc": s.desc} for s in agent.SPECS],
        "promos": [{"code": c, "percent": p, "min": m, "active": a} for c, (p, m, a) in agent.PROMOS.items()],
        "free_delivery_from": agent.FREE_DELIVERY_FROM,
    }


def sse(events):
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@app.post("/api/agent/stream")
def agent_stream(req: AgentRequest):
    """Server-Sent Events: каждый шаг ReAct-цикла приходит в браузер сразу, в конце — structured-отчёт."""
    history = [t.model_dump() for t in req.history]
    return StreamingResponse(sse(agent.run_stream(req.question, req.mode, history)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/agent/run")
def agent_run(req: AgentRequest):
    """Тот же агент без стриминга: возвращает все шаги и итоговый отчёт одним JSON."""
    steps, final, error = [], None, None
    for ev in agent.run_stream(req.question, req.mode, [t.model_dump() for t in req.history]):
        if ev["type"] == "step":
            steps.append(ev)
        elif ev["type"] == "final":
            final = ev
        elif ev["type"] == "error":
            error = ev["message"]
    if error:
        raise HTTPException(502, error)
    return {"steps": steps, **(final or {})}


@app.post("/api/ticket", response_model=agent.TicketAnalysis)
def ticket(req: TicketRequest):
    """Structured output: свободный текст клиента → типизированный разбор обращения."""
    t0 = time.perf_counter()
    try:
        return agent.analyze_ticket(req.text)
    except ToolException as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, agent.friendly_error(e))
