"""Tool Lab — LangChain Custom Tools & Error Handling (12 инструментов, 2 из них с ИИ на Groq).
Запуск:  pip install -r requirements.txt && uvicorn app:app --reload   →  http://127.0.0.1:8000
Ключ Groq: строка GROQ_API_KEY=... в файле .env рядом с app.py (читается при каждом запросе, перезапуск не нужен).
"""
import ast, contextvars, json, operator, os, random, re, time
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, Field

# ═════════ Обычные функции ═════════
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg}

def _eval(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        a, b = _eval(n.left), _eval(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 100: raise ToolException("Степень больше 100 запрещена.")
        return OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and type(n.op) in OPS: return OPS[type(n.op)](_eval(n.operand))
    raise ToolException("Разрешены только числа и знаки + - * / % **")

class CalcArgs(BaseModel):
    expression: str = Field(title="Пример", min_length=1, max_length=120, description="Например: 2*(3+4)")
def calculator(expression: str) -> str:
    try: tree = ast.parse(expression, mode="eval")
    except SyntaxError: raise ToolException(f"Не понимаю «{expression}». Пример: 2*(3+4)")
    try: return f"{expression} = {_eval(tree.body)}"
    except ZeroDivisionError: raise ToolException("Делить на ноль нельзя. Измени выражение.")

UNITS = {"km": ("длина", 1000), "m": ("длина", 1), "mile": ("длина", 1609.34), "ft": ("длина", 0.3048),
         "kg": ("масса", 1), "g": ("масса", 0.001), "lb": ("масса", 0.4536)}
class UnitArgs(BaseModel):
    value: float = Field(title="Число")
    from_unit: str = Field(title="Из чего", description="km, m, mile, ft, kg, g, lb")
    to_unit: str = Field(title="Во что", description="km, m, mile, ft, kg, g, lb")
def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    a, b = UNITS.get(from_unit.lower()), UNITS.get(to_unit.lower())
    if not a or not b: raise ToolException(f"Неизвестная единица. Доступны: {', '.join(UNITS)}.")
    if a[0] != b[0]: raise ToolException(f"Нельзя перевести {a[0]} в {b[0]}. Выбери единицы одного типа.")
    return f"{value} {from_unit} = {round(value * a[1] / b[1], 4)} {to_unit}"

RATES = {"USD": 1, "AZN": 1.7, "EUR": 0.92, "TRY": 34.0, "RUB": 92.0}  # учебные курсы
CUR = Literal["USD", "AZN", "EUR", "TRY", "RUB"]
class CurArgs(BaseModel):
    amount: float = Field(title="Сумма", gt=0)
    from_cur: CUR = Field(title="Из валюты")
    to_cur: CUR = Field(title="В валюту")
def currency(amount: float, from_cur: str, to_cur: str) -> str:
    if from_cur == to_cur: raise ToolException("Валюты одинаковые — менять нечего. Выбери разные.")
    return f"{amount} {from_cur} ≈ {round(amount / RATES[from_cur] * RATES[to_cur], 2)} {to_cur} (учебные курсы)"

class BmiArgs(BaseModel):
    weight_kg: float = Field(title="Вес, кг", gt=0, le=500)
    height_cm: float = Field(title="Рост, см", gt=50, le=260)
def bmi(weight_kg: float, height_cm: float) -> str:
    v = weight_kg / (height_cm / 100) ** 2
    cat = "ниже нормы" if v < 18.5 else "норма" if v < 25 else "выше нормы" if v < 30 else "ожирение"
    return f"ИМТ = {v:.1f} ({cat})"

class DateArgs(BaseModel):
    start: str = Field(title="С даты", description="Формат ГГГГ-ММ-ДД, например 2026-01-31")
    end: str = Field(title="По дату", description="Формат ГГГГ-ММ-ДД")
def date_diff(start: str, end: str) -> str:
    try: a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError: raise ToolException("Дата должна быть в формате ГГГГ-ММ-ДД, например 2026-10-17.")
    d = (b - a).days
    return f"Между датами {abs(d)} дн. (≈ {abs(d)//7} нед.){' — вторая дата раньше первой' if d < 0 else ''}"

class TextArgs(BaseModel):
    text: str = Field(title="Текст", min_length=1)
def text_stats(text: str) -> str:
    words = re.findall(r"\w+", text)
    if not words: raise ToolException("В тексте нет ни одного слова.")
    return (f"Слов: {len(words)}, символов: {len(text)}, предложений: {len(re.findall(r'[.!?]+', text)) or 1}, "
            f"самое длинное слово: «{max(words, key=len)}»")

class PwdArgs(BaseModel):
    password: str = Field(title="Пароль", min_length=1)
def password_check(password: str) -> str:
    kinds = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"\W"))
    score = (len(password) >= 8) + (len(password) >= 12) + kinds
    lvl = "слабый" if score <= 2 else "средний" if score <= 4 else "сильный"
    return f"Пароль {lvl} ({score} из 6). Длина {len(password)}, типов символов: {kinds} из 4."

CITIES = {"baku": 24, "london": 12, "tokyo": 19, "cairo": 31}
class WeatherArgs(BaseModel):
    city: str = Field(title="Город", min_length=2, description="По-английски: Baku, London, Tokyo, Cairo")
    units: Literal["c", "f"] = Field("c", title="Единицы", description="c — Цельсий, f — Фаренгейт")
def get_weather(city: str, units: str = "c") -> str:
    t = CITIES.get(city.strip().lower())
    if t is None: raise ToolException(f"Города «{city}» нет в базе. Есть: {', '.join(CITIES)}.")
    return f"{city.title()}: {t}°C" if units == "c" else f"{city.title()}: {round(t * 9 / 5 + 32)}°F"

# ═════════ Демо ошибок ═════════
class FlakyArgs(BaseModel):
    fail_rate: float = Field(0.6, title="Шанс сбоя (0–1)", ge=0, le=1)
    max_retries: int = Field(3, title="Сколько попыток", ge=1, le=5)
def flaky_api(fail_rate: float = 0.6, max_retries: int = 3) -> str:
    last = None
    for n in range(1, max_retries + 1):
        if random.random() >= fail_rate: return f"Сервер ответил с попытки {n} из {max_retries}"
        last = "503 Service Unavailable"
        time.sleep(0.15 * 2 ** (n - 1))
    raise ToolException(f"Все {max_retries} попытки провалились ({last}).")

class JsonArgs(BaseModel):
    text: str = Field(title="JSON-строка")
def parse_json(text: str) -> dict:
    return json.loads(text)

# ═════════ ИИ-агент на Groq: сам вызывает tools из этого файла ═════════
def env(name: str, default: str = "") -> str:
    """Читает значение из .env рядом с app.py, иначе из переменных окружения."""
    p = Path(__file__).with_name(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip(): return v.strip().strip("\"'")
    return os.getenv(name, default)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def groq_call(messages: list, tools: list) -> dict:
    """Один запрос к Groq (function calling) с повторами. Возвращает message модели."""
    key = env("GROQ_API_KEY")
    if not key: raise ToolException("Ключ Groq не найден. Добавь строку GROQ_API_KEY=... в файл .env (ключ бесплатно на console.groq.com).")
    last = "неизвестная ошибка"
    for n in range(1, 4):
        try:
            r = httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, timeout=30, json={
                "model": env("GROQ_MODEL", "openai/gpt-oss-120b"), "temperature": 0.2, "messages": messages, "tools": tools, "tool_choice": "auto"})
        except httpx.HTTPError as e:
            last = f"сеть: {type(e).__name__}"
        else:
            if r.status_code == 200: return r.json()["choices"][0]["message"]
            if r.status_code in (401, 403): raise ToolException("Groq отклонил ключ. Проверь, что он скопирован целиком.")
            if r.status_code == 404: raise ToolException("Groq не нашёл модель (404): она снята с поддержки. Впиши в .env актуальную, например GROQ_MODEL=openai/gpt-oss-120b")
            last = f"Groq ответил {r.status_code}"
            if r.status_code not in (429, 500, 502, 503) and "tool_use_failed" not in r.text: break
        time.sleep(0.5 * 2 ** (n - 1))
    raise ToolException(f"ИИ недоступен после 3 попыток ({last}). Попробуй позже.")

class AgentArgs(BaseModel):
    task: str = Field(title="Задание для ИИ", min_length=5, max_length=500, description="Своими словами, например: сколько будет 15% от 2480?")

SYSTEM = ("Ты помощник. Для расчётов, конвертаций, погоды, дат и проверок ВСЕГДА вызывай доступные функции, не считай сам. "
          "Если функция вернула ошибку, прочитай подсказку, исправь аргументы и вызови снова. "
          "Если задачу невозможно выполнить, честно объясни почему. В конце дай короткий ответ на русском.")
ALLOWED = {
    "ai_agent": ["calculator", "unit_converter", "currency", "bmi", "date_diff", "text_stats", "password_check", "get_weather"],
    "ai_selfheal": ["get_weather", "currency", "unit_converter", "date_diff", "flaky_api"],
}

def run_agent(name: str, task: str, tr) -> tuple:
    names = ALLOWED[name]
    specs = [{"type": "function", "function": {"name": n, "description": TOOLS[n].description,
              "parameters": TOOLS[n].args_schema.model_json_schema()}} for n in names]
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    steps = []
    for _ in range(6):
        m = groq_call(msgs, specs)
        calls = m.get("tool_calls")
        if not calls: return (m.get("content") or "").strip(), steps
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c["function"]["name"]
            try: args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError: args = {}
            if fn not in names:
                status, out = "handled", f"Функции «{fn}» нет. Доступны: {', '.join(names)}."
            else:
                try:
                    res = TOOLS[fn].invoke({"type": "tool_call", "id": c["id"], "name": fn, "args": args}, config={"callbacks": [tr]})
                    status, out = ("handled" if res.status == "error" else "success"), res.content
                except Exception as e:  # необработанное исключение тоже не роняет агента
                    status, out = "raised", f"{type(e).__name__}: {e}"
            steps.append({"tool": fn, "args": args, "status": status, "output": out})
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": str(out)})
    raise ToolException("Агент не уложился в 6 шагов. Упрости задание.")

# ═════════ Регистрация ═════════
def _vmsg(e) -> str:
    return "Неверные данные: " + "; ".join(f"поле «{'.'.join(map(str, x['loc']))}» — {x['msg']}" for x in e.errors())

def mk(fn, name, schema, desc, handler=True):
    kw = dict(handle_tool_error=handler, handle_validation_error=_vmsg) if handler else {}
    return StructuredTool.from_function(fn, name=name, description=desc, args_schema=schema, **kw)

TOOLS = {
 "calculator": mk(calculator, "calculator", CalcArgs, "Калькулятор"),
 "unit_converter": mk(unit_converter, "unit_converter", UnitArgs, "Километры, мили, кг, фунты"),
 "currency": mk(currency, "currency", CurArgs, "Обмен валют"),
 "bmi": mk(bmi, "bmi", BmiArgs, "Индекс массы тела"),
 "date_diff": mk(date_diff, "date_diff", DateArgs, "Сколько дней между датами"),
 "text_stats": mk(text_stats, "text_stats", TextArgs, "Слова, символы, предложения"),
 "password_check": mk(password_check, "password_check", PwdArgs, "Надёжность пароля"),
 "get_weather": mk(get_weather, "get_weather", WeatherArgs, "Погода по городу",
                   handler=lambda e: f"{e.args[0]} Повтори запрос с городом из списка."),
 "flaky_api": mk(flaky_api, "flaky_api", FlakyArgs, "Нестабильный сервер",
                 handler=lambda e: f"ЗАПАСНОЙ ОТВЕТ: {e.args[0]} Беру данные из кэша."),
 "parse_json": mk(parse_json, "parse_json", JsonArgs, "Разбор JSON без защиты", handler=False),
}

E = lambda label, kind, **a: {"label": label, "kind": kind, "args": a}
LONG_TEXT = "Python — язык программирования, который любят за простоту. На нём пишут сайты, анализируют данные и создают ИИ. Его синтаксис похож на обычный английский текст."
META = {
 "calculator": ("basic", "🧮", "Калькулятор", "Деление на ноль не роняет программу: инструмент объясняет, что не так.",
   [E("Правильно", "ok", expression="2*(3+4)**2"), E("Деление на 0", "err", expression="10/0"), E("Пустое поле", "err", expression="")]),
 "unit_converter": ("basic", "📏", "Единицы измерения", "Понимает, что километры в килограммы не переводятся.",
   [E("Правильно", "ok", value=5, from_unit="km", to_unit="mile"), E("Км → кг", "err", value=5, from_unit="km", to_unit="kg"), E("Неизвестная единица", "err", value=5, from_unit="km", to_unit="parsec")]),
 "currency": ("basic", "💱", "Обмен валют", "Проверяет выбор из списка и одинаковые валюты.",
   [E("Правильно", "ok", amount=100, from_cur="USD", to_cur="AZN"), E("Одна и та же валюта", "err", amount=100, from_cur="USD", to_cur="USD"), E("Сумма 0", "err", amount=0, from_cur="USD", to_cur="EUR")]),
 "bmi": ("basic", "⚖️", "Индекс массы тела", "Отсекает невозможные значения роста и веса.",
   [E("Правильно", "ok", weight_kg=70, height_cm=175), E("Рост 5 см", "err", weight_kg=70, height_cm=5)]),
 "date_diff": ("basic", "📅", "Разница дат", "Подсказывает правильный формат даты.",
   [E("Правильно", "ok", start="2026-01-01", end="2026-10-17"), E("Неверный формат", "err", start="17.10.2026", end="2026-12-31")]),
 "text_stats": ("basic", "📝", "Статистика текста", "Считает слова и не принимает пустой текст.",
   [E("Правильно", "ok", text=LONG_TEXT), E("Только знаки", "err", text="...!!!")]),
 "password_check": ("basic", "🔐", "Проверка пароля", "Оценивает надёжность, пустой пароль не принимает.",
   [E("Слабый", "ok", password="qwerty"), E("Сильный", "ok", password="T7#kLm9!vQz2"), E("Пустой", "err", password="")]),
 "get_weather": ("basic", "🌤️", "Погода", "Если города нет, ИИ-агент получит список доступных.",
   [E("Правильно", "ok", city="Baku", units="c"), E("Неизвестный город", "err", city="Atlantis", units="c")]),
 "flaky_api": ("demo", "📡", "Нестабильный сервер", "Повторяет запрос с паузами; если не вышло, отдаёт запасной ответ.",
   [E("Часто падает", "ok", fail_rate=0.6, max_retries=3), E("Всегда падает", "err", fail_rate=1, max_retries=3)]),
 "parse_json": ("demo", "💥", "JSON без защиты", "Для контраста: здесь ошибка не обработана и программа «падает».",
   [E("Правильный JSON", "ok", text='{"name": "Ali", "age": 20}'), E("Сломанный JSON", "err", text="{name: Ali")]),
 "ai_agent": ("ai", "🤖", "ИИ-помощник", "Напиши задание обычными словами. ИИ сам выберет нужные функции из кода (калькулятор, валюты, погода и др.), вызовет их и ответит.",
   [E("Расчёт", "ok", task="Сколько будет 15% от 2480?"), E("Две функции сразу", "ok", task="Переведи 100 долларов в манаты и скажи, какая погода в Баку."),
    E("Пароль и ИМТ", "ok", task="Проверь пароль qwerty123 и посчитай ИМТ при весе 80 кг и росте 180 см.")]),
 "ai_selfheal": ("ai", "🔧", "ИИ исправляет ошибки", "Задания, на которых функции дают ошибку. ИИ читает подсказку об ошибке и пробует снова или объясняет, почему нельзя.",
   [E("Погода (город по-русски)", "err", task="Какая погода в Баку по Фаренгейту?"), E("Невозможный перевод", "err", task="Переведи 5 километров в килограммы."),
    E("Сервер падает", "err", task="Вызови нестабильный сервер с шансом сбоя 100% и 2 попытками и скажи, что получилось.")]),
}
LONG_FIELDS = {"text", "task"}
AGENTS = {"ai_agent", "ai_selfheal"}

class Trace(BaseCallbackHandler):
    def __init__(self): self.t0, self.events = time.perf_counter(), []
    def _add(self, k, d): self.events.append({"ms": round((time.perf_counter() - self.t0) * 1000), "kind": k, "detail": d})
    def on_tool_start(self, s, i, **kw): self._add("start", i)
    def on_tool_end(self, o, **kw): self._add("end", str(getattr(o, "content", o)))
    def on_tool_error(self, e, **kw): self._add("error", f"{type(e).__name__}: {e}")

app = FastAPI(title="Tool Lab")
class Call(BaseModel):
    tool: str
    args: dict = {}

@app.get("/")
def index(): return FileResponse(Path(__file__).with_name("index.html"))

@app.get("/api/status")
def status(): return {"ai_ready": bool(env("GROQ_API_KEY"))}

@app.get("/api/tools")
def tools():
    return [{"name": n, "group": m[0], "icon": m[1], "title": m[2], "explain": m[3], "examples": m[4],
             "schema": (AgentArgs if n in AGENTS else TOOLS[n].args_schema).model_json_schema(),
             "long": [f for f in (AgentArgs if n in AGENTS else TOOLS[n].args_schema).model_fields if f in LONG_FIELDS]}
            for n, m in META.items()]

@app.post("/api/invoke")
def invoke(call: Call):
    tr = Trace(); steps = None
    try:
        if call.tool in AGENTS:
            task = AgentArgs(**call.args).task
            out, steps = run_agent(call.tool, task, tr)
            failed = any(x["status"] != "success" for x in steps)
            status = "handled" if failed else "success"
        else:
            tool = TOOLS.get(call.tool)
            if not tool: raise HTTPException(404, f"Нет инструмента «{call.tool}»")
            msg = tool.invoke({"type": "tool_call", "id": "demo", "name": tool.name, "args": call.args}, config={"callbacks": [tr]})
            status, out = ("handled" if msg.status == "error" else "success"), msg.content
    except HTTPException: raise
    except ToolException as e:
        status, out = "handled", str(e)
    except Exception as e:
        status, out = "raised", f"{type(e).__name__}: {e}"
    return {"status": status, "output": out, "steps": steps, "ms": round((time.perf_counter() - tr.t0) * 1000), "trace": tr.events}