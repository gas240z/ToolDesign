# ShopPilot — ИИ-консультант магазина

FastAPI + LangChain-агент (ReAct и Tool calling) + structured output на OpenAI.

## Запуск
```
pip install -r requirements.txt
uvicorn app:app --reload
```
Открой http://127.0.0.1:8000. Ключ — в файле `.env` рядом с `app.py`:
```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini   # необязательно
```

## Что внутри
| Файл | Роль |
|---|---|
| `agent.py` | каталог, 7 инструментов, ReAct-агент (`create_react_agent`), tool-calling агент (`create_tool_calling_agent`), `AgentExecutor`, схемы `ShopReport` и `TicketAnalysis`, `with_structured_output` |
| `app.py` | FastAPI: `/api/agent/stream` (SSE по шагам), `/api/agent/run`, `/api/ticket`, `/api/catalog`, `/api/status` |
| `index.html`, `static/` | интерфейс: чат, живая лента «Thought → Action → Observation», чек, разбор обращений |
| `legacy/` | прежний проект Tool Lab (Groq) — для истории |

## Как работает
1. **Агент** получает вопрос и в цикле вызывает инструменты (цена, склад, скидка, промокод, доставка, калькулятор). Ошибки инструментов («нет на складе», «промокод истёк») возвращаются как `ОШИБКА: …` — агент читает их и перестраивает план.
2. **ReAct-режим** — модель пишет Thought/Action/Action Input текстом; **Tool calling** — нативные вызовы функций OpenAI.
3. **Structured output** — после цикла `llm.with_structured_output(ShopReport)` превращает результат в типизированный чек (позиции, скидка, доставка, итог, предупреждения).
4. **Обращения** — `with_structured_output(TicketAnalysis)`: тема, срочность, тон, приоритет, язык, черновик ответа.
