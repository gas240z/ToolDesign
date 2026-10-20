"""ShopPilot — ядро: каталог, инструменты, ReAct / Tool-calling агенты и structured output.

Ключ OpenAI читается из .env рядом с файлом (OPENAI_API_KEY=...) или из переменных окружения —
при каждом запросе, поэтому перезапуск сервера после смены ключа не нужен.
"""
from __future__ import annotations

import ast
import difflib
import json
import operator
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from langchain_classic.agents import AgentExecutor, create_react_agent, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.tools import StructuredTool, Tool, ToolException
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

# ════════════════════════════ Настройки ════════════════════════════
BASE = Path(__file__).parent


def env(name: str, default: str = "") -> str:
    """Значение из .env рядом с agent.py, иначе из переменных окружения."""
    p = BASE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                return v.strip().strip("\"'")
    return os.getenv(name, default)


def api_key() -> str:
    return env("OPENAI_API_KEY")


def make_llm() -> ChatOpenAI:
    key = api_key()
    if not key:
        raise ToolException("Ключ OpenAI не найден. Добавь строку OPENAI_API_KEY=... в файл .env рядом с app.py.")
    return ChatOpenAI(model=env("OPENAI_MODEL", "gpt-4o-mini"), temperature=0, api_key=key, timeout=45, max_retries=2)


# ════════════════════════════ Каталог (учебные данные) ════════════════════════════
CATALOG: dict[str, dict] = {
    "phone": dict(name="Смартфон Nova X", price=800, stock=14, rating=4.7, icon="phone",
                  aliases=["смартфон", "телефон", "phone", "telefon", "nova x", "smartphone"]),
    "laptop": dict(name="Ноутбук Aero 14", price=1500, stock=6, rating=4.8, icon="laptop",
                   aliases=["ноутбук", "laptop", "noutbuk", "aero", "notebook"]),
    "headphones": dict(name="Наушники Pulse", price=120, stock=40, rating=4.5, icon="headphones",
                       aliases=["наушники", "headphones", "qulaqlıq", "qulaqliq", "pulse"]),
    "tablet": dict(name="Планшет Slate 11", price=450, stock=0, rating=4.4, icon="tablet",
                   aliases=["планшет", "tablet", "planşet", "slate"]),
    "watch": dict(name="Умные часы Orbit", price=210, stock=22, rating=4.3, icon="watch",
                  aliases=["часы", "умные часы", "watch", "smartwatch", "saat", "orbit"]),
    "keyboard": dict(name="Клавиатура Keyra", price=95, stock=31, rating=4.6, icon="keyboard",
                     aliases=["клавиатура", "keyboard", "klaviatura", "keyra"]),
    "monitor": dict(name='Монитор Vista 27"', price=380, stock=9, rating=4.6, icon="monitor",
                    aliases=["монитор", "monitor", "ekran", "vista"]),
    "speaker": dict(name="Колонка Boom Mini", price=65, stock=3, rating=4.2, icon="speaker",
                    aliases=["колонка", "speaker", "dinamik", "boom"]),
}
PROMOS = {  # код -> (процент, минимальная сумма заказа, актуален)
    "WELCOME10": (10, 0, True),
    "SUMMER20": (20, 500, True),
    "VIP25": (25, 0, False),
}
CITIES = {"баку": 1, "baku": 1, "сумгаит": 1, "sumqayıt": 1, "sumgait": 1,
          "гянджа": 2, "ganja": 2, "gəncə": 2, "шеки": 3, "shaki": 3, "şəki": 3, "ленкорань": 3, "lankaran": 3}
FREE_DELIVERY_FROM = 300
DELIVERY_FEE = 5


def _find(query: str) -> tuple[str, dict]:
    q = query.strip().lower()
    if not q:
        raise ToolException("Название товара пустое. Укажи, например, «ноутбук».")
    for pid, p in CATALOG.items():
        names = [pid, p["name"].lower(), *p["aliases"]]
        if q in names or any(q in n or n in q for n in names if len(n) > 3):
            return pid, p
    pool = {a: pid for pid, p in CATALOG.items() for a in [pid, p["name"].lower(), *p["aliases"]]}
    close = difflib.get_close_matches(q, list(pool), n=1, cutoff=0.7)
    if close:
        pid = pool[close[0]]
        return pid, CATALOG[pid]
    raise ToolException(f"Товар «{query}» не найден. Вызови search_products, чтобы увидеть, что есть в каталоге.")


# ════════════════════════════ Инструменты ════════════════════════════
class SearchArgs(BaseModel):
    query: str = Field(description="Слово для поиска или 'все' для всего каталога", max_length=80)
    max_price: float | None = Field(None, description="Верхняя граница цены в манатах (AZN)", gt=0)


def search_products(query: str, max_price: float | None = None) -> str:
    q = query.strip().lower()
    hits = []
    for p in CATALOG.values():
        text = " ".join([p["name"].lower(), *p["aliases"]])
        words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
        if q in ("", "все", "всё", "all", "*", "каталог") or q in text or any(w in text for w in words):
            if max_price is None or p["price"] <= max_price:
                hits.append(p)
    if not hits:
        raise ToolException(f"По запросу «{query}»{f' до {max_price} AZN' if max_price else ''} ничего нет. Попробуй запрос «все».")
    return "; ".join(f"{p['name']} — {p['price']} AZN (в наличии: {p['stock']})" for p in sorted(hits, key=lambda x: x["price"]))


class ProductArgs(BaseModel):
    product: str = Field(description="Название товара: смартфон, ноутбук, наушники, планшет, часы, клавиатура, монитор, колонка")


def get_price(product: str) -> str:
    _, p = _find(product)
    return f"{p['name']}: {p['price']} AZN, рейтинг {p['rating']}"


class StockArgs(BaseModel):
    product: str = Field(description="Название товара")
    quantity: int = Field(1, description="Сколько штук нужно", ge=1, le=100)


def check_stock(product: str, quantity: int = 1) -> str:
    _, p = _find(product)
    if p["stock"] == 0:
        raise ToolException(f"{p['name']} закончился на складе. Предложи альтернативу через search_products.")
    if p["stock"] < quantity:
        raise ToolException(f"{p['name']}: нужно {quantity}, но в наличии только {p['stock']}.")
    return f"{p['name']}: {quantity} шт. есть (на складе {p['stock']})"


class DiscountArgs(BaseModel):
    price: float = Field(description="Цена до скидки, AZN", gt=0)
    percent: float = Field(description="Процент скидки от 0 до 90", ge=0, le=90)


def apply_discount(price: float, percent: float) -> str:
    final = round(price - price * percent / 100, 2)
    return f"Цена {price} AZN со скидкой {percent}% = {final} AZN (экономия {round(price - final, 2)} AZN)"


class PromoArgs(BaseModel):
    code: str = Field(description="Промокод, например WELCOME10", max_length=30)
    subtotal: float = Field(description="Сумма заказа до скидки, AZN", gt=0)


def apply_promo(code: str, subtotal: float) -> str:
    c = code.strip().upper()
    if c not in PROMOS:
        raise ToolException(f"Промокода «{code}» не существует. Действуют: WELCOME10, SUMMER20.")
    pct, min_sum, active = PROMOS[c]
    if not active:
        raise ToolException(f"Промокод {c} истёк и больше не действует.")
    if subtotal < min_sum:
        raise ToolException(f"Промокод {c} работает от {min_sum} AZN, сейчас сумма {subtotal} AZN.")
    final = round(subtotal * (1 - pct / 100), 2)
    return f"Промокод {c}: −{pct}%. Сумма {subtotal} → {final} AZN"


class DeliveryArgs(BaseModel):
    city: str = Field(description="Город доставки: Баку, Сумгаит, Гянджа, Шеки, Ленкорань")
    order_total: float = Field(0, description="Сумма заказа в AZN (для бесплатной доставки)", ge=0)


def check_delivery(city: str, order_total: float = 0) -> str:
    days = CITIES.get(city.strip().lower())
    if days is None:
        raise ToolException(f"В город «{city}» не доставляем. Доступно: Баку, Сумгаит, Гянджа, Шеки, Ленкорань.")
    fee = 0 if order_total >= FREE_DELIVERY_FROM else DELIVERY_FEE
    note = "бесплатно" if fee == 0 else f"{fee} AZN (бесплатно от {FREE_DELIVERY_FROM} AZN)"
    return f"Доставка в {city.title()}: {days} дн., стоимость {note}"


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}


def _ev(n: ast.AST) -> float:
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 50:
            raise ToolException("Степень больше 50 запрещена.")
        return _OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand))
    raise ToolException("Разрешены только числа и знаки + - * / % **")


class CalcArgs(BaseModel):
    expression: str = Field(description="Арифметическое выражение, например 1500*0.85+5", min_length=1, max_length=120)


def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression.replace(",", "."), mode="eval")
        return f"{expression} = {round(_ev(tree.body), 2)}"
    except SyntaxError:
        raise ToolException(f"Не понимаю выражение «{expression}». Пример: 1500*0.85+5")
    except ZeroDivisionError:
        raise ToolException("Деление на ноль.")


@dataclass
class Spec:
    name: str
    desc: str
    args: type[BaseModel]
    fn: Callable[..., str]
    example: str
    icon: str
    title: str


SPECS: list[Spec] = [
    Spec("search_products", "Поиск товаров в каталоге по слову (можно с лимитом цены). Для списка всего каталога query='все'.",
         SearchArgs, search_products, '{"query": "наушники", "max_price": 200}', "search", "Поиск по каталогу"),
    Spec("get_price", "Узнать цену и рейтинг одного товара.", ProductArgs, get_price, '{"product": "ноутбук"}', "tag", "Цена товара"),
    Spec("check_stock", "Проверить, есть ли нужное количество товара на складе.", StockArgs, check_stock,
         '{"product": "телефон", "quantity": 2}', "box", "Наличие на складе"),
    Spec("apply_discount", "Применить процентную скидку к цене и получить итог.", DiscountArgs, apply_discount,
         '{"price": 1500, "percent": 15}', "percent", "Скидка в %"),
    Spec("apply_promo", "Применить промокод к сумме заказа (проверяет срок действия и минимальную сумму).", PromoArgs, apply_promo,
         '{"code": "WELCOME10", "subtotal": 800}', "ticket", "Промокод"),
    Spec("check_delivery", "Срок и стоимость доставки в город.", DeliveryArgs, check_delivery,
         '{"city": "Баку", "order_total": 800}', "truck", "Доставка"),
    Spec("calculate", "Безопасный калькулятор для итоговых сумм (+ - * / % **).", CalcArgs, calculate,
         '{"expression": "800*2+120"}', "calc", "Калькулятор"),
]
SPEC_BY_NAME = {s.name: s for s in SPECS}


def _validation_msg(e: ValidationError) -> str:
    return "Неверные данные: " + "; ".join(f"поле «{'.'.join(map(str, x['loc']))}» — {x['msg']}" for x in e.errors())


def _err(e: Exception) -> str:
    return f"ОШИБКА: {e}"


def tool_calling_tools() -> list[StructuredTool]:
    """Настоящие StructuredTool — для режима function calling."""
    return [StructuredTool.from_function(s.fn, name=s.name, description=s.desc, args_schema=s.args,
                                         handle_tool_error=_err, handle_validation_error=lambda e: _err(_validation_msg(e)))
            for s in SPECS]


def react_tools() -> list[Tool]:
    """ReAct-агент общается текстом, поэтому каждый инструмент принимает ОДНУ строку (JSON)."""
    def wrap(s: Spec) -> Tool:
        first = next(iter(s.args.model_fields))

        def run(raw: str) -> str:
            raw = raw.strip()
            m = re.search(r"\{.*\}", raw, re.S)  # модель может добавить лишний текст вокруг JSON
            raw = m.group(0) if m else raw.strip("`").removeprefix("json").strip()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    data = {first: data}
            except json.JSONDecodeError:
                data = {first: raw.strip("\"'")}
            try:
                model = s.args(**data)
                return s.fn(**model.model_dump())
            except ValidationError as e:
                return _err(_validation_msg(e) + f". Пример Action Input: {s.example}")
            except ToolException as e:
                return _err(e)

        return Tool(name=s.name, func=run, description=f"{s.desc} Action Input — JSON, например {s.example}")

    return [wrap(s) for s in SPECS]


# ════════════════════════════ Агенты ════════════════════════════
SYSTEM = (
    "Ты — ShopPilot, консультант интернет-магазина электроники. Валюта — манаты (AZN). "
    "Для цен, наличия, скидок, промокодов, доставки и арифметики ВСЕГДА используй инструменты, никогда не придумывай числа. "
    "Если инструмент вернул ОШИБКА — прочитай подсказку, исправь аргументы или предложи клиенту альтернативу. "
    "Отвечай на языке клиента (русский, азербайджанский или английский), коротко и дружелюбно."
)

REACT_TEMPLATE = SYSTEM + """

У тебя есть инструменты:
{tools}

Используй СТРОГО такой формат:

Question: вопрос клиента
Thought: что нужно сделать дальше
Action: имя инструмента, одно из [{tool_names}]
Action Input: ОДИН JSON-объект с аргументами (пример — в описании инструмента), без пояснений
Observation: результат инструмента
... (Thought/Action/Action Input/Observation можно повторять)
Thought: теперь я знаю ответ
Final Answer: итоговый ответ клиенту с ценами и сроками

Начинай!

Question: {input}
Thought:{agent_scratchpad}"""


def build_executor(mode: Literal["react", "tools"]) -> AgentExecutor:
    llm = make_llm()
    if mode == "react":
        tools = react_tools()
        agent = create_react_agent(llm, tools, PromptTemplate.from_template(REACT_TEMPLATE))
    else:
        tools = tool_calling_tools()
        prompt = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", "{input}"),
                                                   MessagesPlaceholder("agent_scratchpad")])
        agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, max_iterations=8, handle_parsing_errors=True,
                         return_intermediate_steps=True)


# ════════════════════════════ Structured output ════════════════════════════
class LineItem(BaseModel):
    product: str = Field(description="Название товара")
    quantity: int = Field(description="Количество")
    unit_price: float = Field(description="Цена за штуку до скидки, AZN")
    discount_percent: float = Field(description="Процент скидки на позицию, 0 если нет")
    line_total: float = Field(description="Итог по позиции со скидкой, AZN")


class ShopReport(BaseModel):
    """Итоговый структурированный отчёт агента — только на основе результатов инструментов."""
    answer: str = Field(description="Короткий ответ клиенту на его языке")
    status: Literal["ok", "partial", "impossible"] = Field(
        description="ok — запрос выполнен, partial — выполнен частично/с заменой, impossible — выполнить нельзя")
    items: list[LineItem] = Field(description="Товары из запроса; пустой список, если речь не о товарах")
    subtotal: float | None = Field(description="Сумма товаров до скидок, AZN, или null")
    savings: float | None = Field(description="Общая экономия, AZN, или null")
    delivery_city: str | None = Field(description="Город доставки или null")
    delivery_days: int | None = Field(description="Срок доставки в днях или null")
    delivery_cost: float | None = Field(description="Стоимость доставки, AZN, или null")
    promo_code: str | None = Field(description="Применённый промокод или null")
    total: float | None = Field(description="Итог к оплате с доставкой, AZN, или null")
    warnings: list[str] = Field(description="Проблемы: нет на складе, истёкший промокод и т.д.")
    follow_ups: list[str] = Field(description="2–3 коротких вопроса, которые клиент может задать дальше")


class TicketAnalysis(BaseModel):
    """Разбор обращения клиента."""
    category: Literal["цена", "доставка", "жалоба", "возврат", "другое"] = Field(description="Тема обращения")
    is_urgent: bool = Field(description="True, если клиент зол, ждёт слишком долго или угрожает отказом")
    sentiment: Literal["негативный", "нейтральный", "позитивный"] = Field(description="Тон клиента")
    priority: Literal[1, 2, 3, 4, 5] = Field(description="Приоритет: 1 — низкий, 5 — критичный")
    language: Literal["ru", "az", "en", "other"] = Field(description="Язык обращения")
    products: list[str] = Field(description="Упомянутые товары, пустой список если нет")
    summary: str = Field(description="Суть обращения одним предложением на русском")
    suggested_reply: str = Field(description="Вежливый черновик ответа на языке клиента, 2–3 предложения")


def structured(schema: type[BaseModel]):
    """LLM с типизированным выходом; если json_schema не прошёл — запасной function_calling."""
    llm = make_llm()
    primary = llm.with_structured_output(schema)
    fallback = llm.with_structured_output(schema, method="function_calling")
    return primary.with_fallbacks([fallback])


REPORT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "Заполни отчёт СТРОГО по данным из шагов инструментов. Не придумывай числа: если данных нет — null. "
               "Если инструмент вернул ОШИБКА — добавь это в warnings и поставь status partial или impossible. "
               "Скидка по промокоду учитывается в discount_percent позиций (и в savings). "
               "answer — на языке клиента."),
    ("human", "Вопрос клиента:\n{question}\n\nШаги инструментов:\n{trace}\n\nФинальный ответ агента:\n{final}"),
])


def make_report(question: str, steps: list[dict], final: str) -> ShopReport:
    trace = "\n".join(f"- {s['tool']}({json.dumps(s['args'], ensure_ascii=False)}) → {s['observation']}" for s in steps) or "(инструменты не вызывались)"
    return (REPORT_PROMPT | structured(ShopReport)).invoke({"question": question, "trace": trace, "final": final})


TICKET_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "Ты аналитик службы поддержки интернет-магазина. Классифицируй обращение клиента."),
    ("human", "{text}"),
])


def analyze_ticket(text: str) -> TicketAnalysis:
    return (TICKET_PROMPT | structured(TicketAnalysis)).invoke({"text": text})


# ════════════════════════════ Запуск агента со стримингом шагов ════════════════════════════
def _thought(action: Any, mode: str) -> str:
    if mode == "react":
        return (action.log or "").split("Action:")[0].split("Thought:")[-1].strip()
    for m in getattr(action, "message_log", None) or []:
        if isinstance(m.content, str) and m.content.strip():
            return m.content.strip()
    return ""


def _args(action: Any) -> dict:
    ti = action.tool_input
    if isinstance(ti, dict):
        return ti
    try:
        d = json.loads(str(ti).strip().strip("`").removeprefix("json"))
        return d if isinstance(d, dict) else {"input": d}
    except json.JSONDecodeError:
        return {"input": str(ti)}


def run_stream(question: str, mode: Literal["react", "tools"], history: list[dict]) -> Iterator[dict]:
    """Генератор событий: step (thought/action/observation) → structuring → final | error."""
    t0 = time.perf_counter()
    ms = lambda: round((time.perf_counter() - t0) * 1000)  # noqa: E731
    try:
        executor = build_executor(mode)
        ctx = "\n".join(f"{'Клиент' if h['role'] == 'user' else 'ShopPilot'}: {h['content']}" for h in history[-6:])
        text = f"Предыдущий диалог:\n{ctx}\n\nНовый вопрос клиента: {question}" if ctx else question
        steps: list[dict] = []
        pending: dict[str, list[dict]] = {}
        final = ""
        n = 0
        for chunk in executor.stream({"input": text}):
            for a in chunk.get("actions", []):
                if a.tool == "_Exception":
                    yield {"type": "step", "kind": "note", "text": "Агент ответил не по формату — просим повторить", "ms": ms()}
                    continue
                th = _thought(a, mode)
                if th:
                    yield {"type": "step", "kind": "thought", "text": th, "ms": ms()}
                n += 1
                rec = {"id": n, "tool": a.tool, "args": _args(a)}
                pending.setdefault(a.tool, []).append(rec)
                yield {"type": "step", "kind": "action", "id": n, "tool": a.tool, "args": rec["args"], "ms": ms()}
            for s in chunk.get("steps", []):
                obs = str(s.observation)
                queue = pending.get(s.action.tool) or []
                rec = queue.pop(0) if queue else {"id": 0, "tool": s.action.tool, "args": _args(s.action)}
                steps.append({**rec, "observation": obs})
                yield {"type": "step", "kind": "observation", "id": rec["id"], "tool": rec["tool"], "text": obs,
                       "ok": not obs.startswith("ОШИБКА") and not obs.startswith("Invalid"), "ms": ms()}
            if "output" in chunk:
                final = str(chunk["output"])
        if final.startswith("Agent stopped"):
            final = "Не удалось завершить задачу за отведённое число шагов. Попробуй упростить вопрос."
        yield {"type": "structuring", "ms": ms()}
        report = make_report(question, steps, final)
        yield {"type": "final", "answer": report.answer or final, "report": report.model_dump(), "steps": len(steps), "ms": ms()}
    except ToolException as e:
        yield {"type": "error", "message": str(e)}
    except Exception as e:  # noqa: BLE001 — любая ошибка должна дойти до интерфейса
        yield {"type": "error", "message": friendly_error(e)}


def friendly_error(e: Exception) -> str:
    name, msg = type(e).__name__, str(e)
    if "401" in msg or "invalid_api_key" in msg or name == "AuthenticationError":
        return "OpenAI отклонил ключ. Проверь OPENAI_API_KEY в файле .env."
    if "429" in msg or name == "RateLimitError":
        return "Лимит запросов OpenAI исчерпан или закончились средства на аккаунте."
    if "model" in msg.lower() and ("not exist" in msg.lower() or "404" in msg):
        return "Модель недоступна для этого ключа. Укажи другую: OPENAI_MODEL=gpt-4o-mini в .env."
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError"):
        return "Нет соединения с OpenAI. Проверь интернет или VPN."
    return f"{name}: {msg[:300]}"
