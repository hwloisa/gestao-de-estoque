"""Controle de estoque - Creche Carolina Guilhermina Schnoor Bohianza.

Backend Flask + SQLite. Execute com:

    python app.py

e acesse http://127.0.0.1:5000
"""

import hmac
import math
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DATABASE_PATH = os.path.join(BASE_DIR, "estoque.db")


def resolve_database_path():
    """Guarda o banco fora do OneDrive.

    O SQLite não deve ficar em pasta sincronizada: o OneDrive pode substituir
    o arquivo por uma versão antiga e os dados importados desaparecem.
    """
    override = os.environ.get("ESTOQUE_DB_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        data_dir = os.path.join(local_app_data, "GestaoDeEstoque")
        try:
            os.makedirs(data_dir, exist_ok=True)
            return os.path.join(data_dir, "estoque.db")
        except OSError:
            pass

    return LEGACY_DATABASE_PATH


DATABASE_PATH = resolve_database_path()


def migrate_legacy_database():
    """Copia o banco antigo (dentro do projeto/OneDrive) para a pasta local."""
    if os.path.abspath(DATABASE_PATH) == os.path.abspath(LEGACY_DATABASE_PATH):
        return
    if os.path.exists(DATABASE_PATH) or not os.path.exists(LEGACY_DATABASE_PATH):
        return
    try:
        shutil.copy2(LEGACY_DATABASE_PATH, DATABASE_PATH)
    except OSError:
        pass

APP_NAME = "Creche Carolina Guilhermina Schnoor Bohianza"

SECTORS = {"pedagogico": "Pedagógico", "limpeza": "Limpeza"}

# O cadastro de itens usa sempre estas duas categorias fixas.
FIXED_CATEGORIES = {"pedagogico": "Pedagógica", "limpeza": "Limpeza"}

UNITS = [
    {"label": "Unidade", "short": "un"},
    {"label": "Caixa", "short": "cx"},
    {"label": "Saco", "short": "sc"},
    {"label": "Fardo", "short": "fd"},
    {"label": "Resma", "short": "rm"},
    {"label": "Litro", "short": "L"},
    {"label": "Par", "short": "par"},
    {"label": "Maço", "short": "mc"},
    {"label": "Pacote", "short": "pct"},
    {"label": "Frasco", "short": "fr"},
]
UNIT_SHORT = {unit["label"]: unit["short"] for unit in UNITS}

MONTHS = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sector TEXT NOT NULL CHECK (sector IN ('pedagogico', 'limpeza')),
    UNIQUE (name, sector)
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories (id) ON DELETE RESTRICT,
    unit TEXT NOT NULL,
    monthly_forecast REAL NOT NULL DEFAULT 0,
    quantity REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('entrada', 'saida')),
    quantity REAL NOT NULL,
    note TEXT,
    previous_quantity REAL NOT NULL DEFAULT 0,
    new_quantity REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_movements_created ON movements (created_at);
CREATE INDEX IF NOT EXISTS idx_items_category ON items (category_id);
"""

app = Flask(__name__)


def load_secret_key():
    """Usa SECRET_KEY do ambiente ou mantém uma chave local persistente."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    key_path = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(key_path, "r", encoding="utf-8") as key_file:
            stored = key_file.read().strip()
            if stored:
                return stored
    except FileNotFoundError:
        pass

    generated = secrets.token_hex(32)
    with open(key_path, "w", encoding="utf-8") as key_file:
        key_file.write(generated)
    return generated


app.secret_key = load_secret_key()
app.json.ensure_ascii = False
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

PUBLIC_ENDPOINTS = {"login", "static", "api_login", "api_setup"}

# Hash usado para igualar o tempo de resposta quando o usuário não existe.
DUMMY_PASSWORD_HASH = generate_password_hash("senha-inexistente-para-tempo-constante")

MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 120
LOGIN_WINDOW_SECONDS = 300
login_attempts = {}
login_attempts_lock = threading.Lock()


class ApiError(Exception):
    """Erro de negócio devolvido em JSON para o frontend."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(DATABASE_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


# ---------------------------------------------------------------------------
# Autenticação e segurança
# ---------------------------------------------------------------------------


def normalize_username(value):
    return (value or "").strip()


def login_attempt_key(username):
    return f"{request.remote_addr or 'local'}|{username.lower()}"


def login_lock_seconds(key):
    now = time.time()
    with login_attempts_lock:
        entry = login_attempts.get(key)
        if not entry:
            return 0
        if entry["locked_until"] > now:
            return int(entry["locked_until"] - now) + 1
        if now - entry["first"] > LOGIN_WINDOW_SECONDS:
            login_attempts.pop(key, None)
        return 0


def register_login_failure(key):
    now = time.time()
    with login_attempts_lock:
        entry = login_attempts.get(key)
        if not entry or now - entry["first"] > LOGIN_WINDOW_SECONDS:
            entry = {"count": 0, "first": now, "locked_until": 0}
        entry["count"] += 1
        if entry["count"] >= MAX_LOGIN_ATTEMPTS:
            entry["locked_until"] = now + LOGIN_LOCK_SECONDS
        login_attempts[key] = entry
        if len(login_attempts) > 2000:
            for name in [name for name, item in login_attempts.items() if item["locked_until"] < now]:
                login_attempts.pop(name, None)


def clear_login_failures(key):
    with login_attempts_lock:
        login_attempts.pop(key, None)


def start_user_session(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)


@app.before_request
def authenticate_request():
    g.user = None
    user_id = session.get("user_id")

    if user_id is not None:
        g.user = get_db().execute(
            "SELECT id, username, display_name, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if g.user is None:
            session.clear()

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if g.user is None:
        if request.path.startswith("/api/"):
            return (
                jsonify(error="Faça login para continuar.", code="unauthorized"),
                401,
            )
        return redirect(url_for("login"))

    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)

    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.path.startswith("/api/")
    ):
        expected = session.get("csrf_token", "")
        received = request.headers.get("X-CSRF-Token", "")
        if not expected or not received or not hmac.compare_digest(expected, received):
            return (
                jsonify(
                    error="Sessão inválida. Recarregue a página e tente novamente.",
                    code="csrf",
                ),
                403,
            )

    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(ApiError)
def handle_api_error(error):
    return jsonify(error=error.message), error.status


@app.errorhandler(404)
def handle_not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="Recurso não encontrado."), 404
    return render_template("index.html", app_name=APP_NAME), 404


# ---------------------------------------------------------------------------
# Inicialização / dados de exemplo
# ---------------------------------------------------------------------------


def timestamp_ago(now, days=0, hours=0, minutes=0):
    return (now - timedelta(days=days, hours=hours, minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def seed_database(connection):
    now = datetime.now()

    categories = list(FIXED_CATEGORIES.items())
    category_ids = {}
    for sector, name in categories:
        cursor = connection.execute(
            "INSERT INTO categories (name, sector) VALUES (?, ?)", (name, sector)
        )
        category_ids[sector] = cursor.lastrowid

    # (nome, setor, unidade, previsão mensal, saldo inicial)
    items = [
        ("Giz de cera", "pedagogico", "Caixa", 12, 10),
        ("Massinha de modelar", "pedagogico", "Caixa", 8, 4),
        ("Papel A4", "pedagogico", "Resma", 10, 9),
        ("Cola branca", "pedagogico", "Unidade", 10, 16),
        ("Lenço umedecido", "pedagogico", "Pacote", 30, 25),
        ("Fralda descartável M", "pedagogico", "Pacote", 40, 40),
        ("Detergente", "limpeza", "Unidade", 12, 8),
        ("Água sanitária", "limpeza", "Litro", 8, 10),
        ("Papel toalha", "limpeza", "Pacote", 20, 22),
        ("Sabonete líquido", "limpeza", "Frasco", 10, 6),
        ("Álcool 70%", "limpeza", "Frasco", 8, 3),
        ("Luvas descartáveis", "limpeza", "Caixa", 6, 14),
    ]

    item_ids = {}
    stock = {}
    created_at = timestamp_ago(now, days=30, hours=2)
    for name, sector, unit, forecast, initial in items:
        cursor = connection.execute(
            """
            INSERT INTO items (name, category_id, unit, monthly_forecast, quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, category_ids[sector], unit, forecast, initial, created_at),
        )
        item_id = cursor.lastrowid
        item_ids[name] = item_id
        stock[name] = initial
        connection.execute(
            """
            INSERT INTO movements
                (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
            VALUES (?, 'entrada', ?, 'Saldo inicial', 0, ?, ?)
            """,
            (item_id, initial, initial, created_at),
        )

    # (item, tipo, quantidade, dias atrás, horas extras, observação)
    movements = [
        ("Fralda descartável M", "entrada", 10, 6, 3, "Doação recebida"),
        ("Papel toalha", "saida", 2, 6, 5, "Uso na limpeza"),
        ("Giz de cera", "saida", 1, 5, 2, None),
        ("Detergente", "saida", 2, 5, 4, "Reposição da cozinha"),
        ("Lenço umedecido", "saida", 2, 4, 1, None),
        ("Papel A4", "saida", 2, 4, 5, "Atividades pedagógicas"),
        ("Massinha de modelar", "saida", 4, 3, 2, "Oficina de modelagem"),
        ("Sabonete líquido", "saida", 2, 3, 4, None),
        ("Água sanitária", "saida", 1, 3, 6, None),
        ("Giz de cera", "saida", 1, 2, 1, None),
        ("Papel A4", "saida", 1, 2, 3, None),
        ("Luvas descartáveis", "saida", 2, 2, 5, None),
        ("Fralda descartável M", "saida", 8, 2, 7, "Consumo da semana"),
        ("Lenço umedecido", "saida", 1, 1, 1, None),
        ("Detergente", "saida", 1, 1, 3, None),
        ("Papel toalha", "saida", 2, 1, 4, None),
        ("Cola branca", "saida", 2, 1, 5, "Atividades pedagógicas"),
        ("Fralda descartável M", "saida", 7, 1, 6, None),
        ("Álcool 70%", "saida", 3, 1, 7, "Higienização dos brinquedos"),
        ("Sabonete líquido", "saida", 1, 1, 8, None),
    ]

    for name, movement_type, quantity, days, hours, note in movements:
        item_id = item_ids[name]
        previous = stock[name]
        new_quantity = previous + quantity if movement_type == "entrada" else max(previous - quantity, 0)
        connection.execute(
            """
            INSERT INTO movements
                (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                movement_type,
                quantity,
                note,
                previous,
                new_quantity,
                timestamp_ago(now, days=days, hours=hours),
            ),
        )
        connection.execute(
            "UPDATE items SET quantity = ? WHERE id = ?", (new_quantity, item_id)
        )
        stock[name] = new_quantity


def normalize_fixed_categories(connection):
    """Mantém exatamente duas categorias: Pedagógica e Limpeza.

    Bancos antigos tinham várias categorias por setor. Os itens são movidos
    para a categoria fixa do seu setor e as categorias extras são removidas.
    """
    canonical = {}

    for sector, name in FIXED_CATEGORIES.items():
        row = connection.execute(
            "SELECT id FROM categories WHERE sector = ? AND name = ?",
            (sector, name),
        ).fetchone()

        if row is None:
            # Reaproveita a primeira categoria do setor para não perder os itens.
            row = connection.execute(
                "SELECT id FROM categories WHERE sector = ? ORDER BY id LIMIT 1",
                (sector,),
            ).fetchone()

        if row is None:
            cursor = connection.execute(
                "INSERT INTO categories (name, sector) VALUES (?, ?)", (name, sector)
            )
            canonical[sector] = cursor.lastrowid
        else:
            canonical[sector] = row["id"]
            connection.execute(
                "UPDATE categories SET name = ? WHERE id = ?", (name, row["id"])
            )

    for sector, category_id in canonical.items():
        connection.execute(
            """
            UPDATE items SET category_id = ?
            WHERE category_id IN (
                SELECT id FROM categories WHERE sector = ? AND id <> ?
            )
            """,
            (category_id, sector, category_id),
        )

    placeholders = ",".join("?" for _ in canonical)
    connection.execute(
        f"DELETE FROM categories WHERE id NOT IN ({placeholders})",
        tuple(canonical.values()),
    )


def category_for_sector(connection, sector):
    if sector not in SECTORS:
        return None
    return connection.execute(
        "SELECT * FROM categories WHERE sector = ? ORDER BY id LIMIT 1", (sector,)
    ).fetchone()


def init_db():
    first_time = not os.path.exists(DATABASE_PATH)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    if first_time and os.environ.get("SEED_DEMO_DATA") == "1":
        seed_database(connection)
    normalize_fixed_categories(connection)
    connection.commit()
    connection.close()


# ---------------------------------------------------------------------------
# Serialização e consultas
# ---------------------------------------------------------------------------


def serialize_item(row):
    quantity = round(row["quantity"], 3)
    forecast = round(row["monthly_forecast"], 3)
    is_low = forecast > 0 and quantity < forecast
    return {
        "id": row["id"],
        "name": row["name"],
        "category_id": row["category_id"],
        "category_name": row["category_name"],
        "sector": row["sector"],
        "sector_label": SECTORS.get(row["sector"], row["sector"]),
        "unit": row["unit"],
        "unit_short": UNIT_SHORT.get(row["unit"], row["unit"]),
        "monthly_forecast": forecast,
        "quantity": quantity,
        "is_low": is_low,
        "is_out": quantity <= 0,
        "percent": round(min(quantity / forecast, 1) * 100) if forecast > 0 else 100,
        "deficit": round(max(forecast - quantity, 0), 3),
        "created_at": row["created_at"],
    }


ITEMS_QUERY = """
    SELECT i.*, c.name AS category_name, c.sector
    FROM items i
    JOIN categories c ON c.id = i.category_id
    ORDER BY i.name COLLATE NOCASE
"""


def fetch_items(connection):
    return [serialize_item(row) for row in connection.execute(ITEMS_QUERY).fetchall()]


def fetch_item(connection, item_id):
    row = connection.execute(
        """
        SELECT i.*, c.name AS category_name, c.sector
        FROM items i
        JOIN categories c ON c.id = i.category_id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    return serialize_item(row) if row else None


def find_duplicate_item(connection, name, sector, exclude_id=None):
    """Procura outro item com o mesmo nome no mesmo setor."""
    query = """
        SELECT i.id, i.name
        FROM items i
        JOIN categories c ON c.id = i.category_id
        WHERE c.sector = ? AND i.name = ? COLLATE NOCASE
    """
    params = [sector, name]
    if exclude_id is not None:
        query += " AND i.id <> ?"
        params.append(exclude_id)
    return connection.execute(query, params).fetchone()


def serialize_movement(row):
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "item_name": row["item_name"],
        "unit": row["unit"],
        "unit_short": UNIT_SHORT.get(row["unit"], row["unit"]),
        "category_name": row["category_name"],
        "sector": row["sector"],
        "sector_label": SECTORS.get(row["sector"], row["sector"]),
        "type": row["type"],
        "quantity": round(row["quantity"], 3),
        "note": row["note"],
        "previous_quantity": round(row["previous_quantity"], 3),
        "new_quantity": round(row["new_quantity"], 3),
        "created_at": row["created_at"],
    }


MOVEMENTS_QUERY = """
    SELECT m.*, i.name AS item_name, i.unit, c.name AS category_name, c.sector
    FROM movements m
    JOIN items i ON i.id = m.item_id
    JOIN categories c ON c.id = i.category_id
"""


def build_dashboard(connection):
    items = fetch_items(connection)
    total_stock = round(sum(item["quantity"] for item in items), 3)
    out_of_stock = [item for item in items if item["is_out"]]
    low_stock = [item for item in items if item["is_low"]]
    alerts = sorted(
        low_stock,
        key=lambda item: (
            item["quantity"] / item["monthly_forecast"] if item["monthly_forecast"] else 0,
            item["name"].lower(),
        ),
    )

    categories_count = connection.execute(
        "SELECT COUNT(*) AS total FROM categories"
    ).fetchone()["total"]

    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month = {
        "label": f"{MONTHS[today.month - 1]} de {today.year}",
        "entradas": {"count": 0, "quantity": 0},
        "saidas": {"count": 0, "quantity": 0},
        "balance": 0,
    }
    for row in connection.execute(
        """
        SELECT type, COUNT(*) AS total, COALESCE(SUM(quantity), 0) AS quantity
        FROM movements
        WHERE date(created_at) >= ?
        GROUP BY type
        """,
        (month_start,),
    ).fetchall():
        key = "entradas" if row["type"] == "entrada" else "saidas"
        month[key] = {
            "count": row["total"],
            "quantity": round(row["quantity"], 3),
        }
    month["balance"] = round(
        month["entradas"]["quantity"] - month["saidas"]["quantity"], 3
    )

    chart_start = today - timedelta(days=6)
    daily = {}
    for row in connection.execute(
        """
        SELECT date(created_at) AS day, type, COALESCE(SUM(quantity), 0) AS quantity
        FROM movements
        WHERE date(created_at) >= ?
        GROUP BY day, type
        """,
        (chart_start.isoformat(),),
    ).fetchall():
        daily[(row["day"], row["type"])] = round(row["quantity"], 3)

    chart = []
    for offset in range(7):
        day = chart_start + timedelta(days=offset)
        chart.append(
            {
                "date": day.isoformat(),
                "label": WEEKDAYS[day.weekday()],
                "is_today": day == today,
                "entradas": daily.get((day.isoformat(), "entrada"), 0),
                "saidas": daily.get((day.isoformat(), "saida"), 0),
            }
        )

    recent = [
        serialize_movement(row)
        for row in connection.execute(
            MOVEMENTS_QUERY + " ORDER BY m.created_at DESC, m.id DESC LIMIT 8"
        ).fetchall()
    ]

    sectors = {}
    for key, label in SECTORS.items():
        subset = [item for item in items if item["sector"] == key]
        sectors[key] = {
            "label": label,
            "items": len(subset),
            "stock": round(sum(item["quantity"] for item in subset), 3),
            "alerts": len([item for item in subset if item["is_low"]]),
            "out": len([item for item in subset if item["is_out"]]),
        }

    return {
        "totals": {
            "items": len(items),
            "categories": categories_count,
            "stock": total_stock,
            "out_of_stock": len(out_of_stock),
            "low_stock": len(low_stock),
        },
        "month": month,
        "chart": chart,
        "alerts": alerts,
        "recent": recent,
        "sectors": sectors,
    }


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def parse_number(value, label, minimum=0):
    if value is None or value == "":
        value = 0
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError(f"Informe um número válido para {label}.")
    if not math.isfinite(number):
        raise ApiError(f"Informe um número válido para {label}.")
    if number < minimum:
        raise ApiError(f"O campo “{label}” deve ser maior ou igual a {minimum}.")
    return round(number, 3)


def parse_item_payload(data):
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Informe o nome do item.")
    if len(name) > 120:
        raise ApiError("O nome do item deve ter no máximo 120 caracteres.")

    try:
        category_id = int(data.get("category_id"))
    except (TypeError, ValueError):
        raise ApiError("Selecione uma categoria válida.")

    category = get_db().execute(
        "SELECT * FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if category is None:
        raise ApiError("Categoria não encontrada.", 404)

    unit = (data.get("unit") or "").strip()
    if not unit:
        raise ApiError("Selecione a unidade de medida.")
    if len(unit) > 40:
        raise ApiError("Unidade de medida inválida.")
    unit = normalize_unit_label(unit) or unit

    forecast = parse_number(data.get("monthly_forecast", 0), "previsão mensal")
    if forecast > 100000:
        raise ApiError("A previsão mensal informada é muito grande.")

    return name, category, unit, forecast


# ---------------------------------------------------------------------------
# Relatórios em Excel
# ---------------------------------------------------------------------------

REPORT_LINE_1 = "PREFEITURA MUNICIPAL DE LIMEIRA - SECRETARIA DE EDUCAÇÃO"
REPORT_SCHOOL_LINE = 'ESCOLA: CI "Carolina Guilhermina Schnoor Bohianza"'
REPORT_HEADERS = [
    "N",
    "MATERIAIS",
    "COMO SOLICITAR (UNIDADE)",
    "PREVISAO MENSAL",
    "ESTOQUE",
]
REPORT_SECTOR_TITLES = {
    "pedagogico": "MATERIAIS PEDAGÓGICOS",
    "limpeza": "MATERIAIS DE LIMPEZA",
}
REPORT_TEMPLATE_ITEMS = {
    "pedagogico": [
        ("Giz de cera", "Caixa", 12, 10),
        ("Papel A4", "Resma", 10, 9),
    ],
    "limpeza": [
        ("Detergente", "Unidade", 12, 8),
        ("Água sanitária", "Litro", 8, 10),
    ],
}

UNIT_ALIASES = {
    "unidade": "Unidade",
    "unidades": "Unidade",
    "unidada": "Unidade",
    "un": "Unidade",
    "und": "Unidade",
    "unid": "Unidade",
    "caixa": "Caixa",
    "caixas": "Caixa",
    "cx": "Caixa",
    "saco": "Saco",
    "sacos": "Saco",
    "sc": "Saco",
    "fardo": "Fardo",
    "fardos": "Fardo",
    "fd": "Fardo",
    "resma": "Resma",
    "resmas": "Resma",
    "rm": "Resma",
    "litro": "Litro",
    "litros": "Litro",
    "l": "Litro",
    "par": "Par",
    "pares": "Par",
    "maco": "Maço",
    "macos": "Maço",
    "mc": "Maço",
    "pacote": "Pacote",
    "pacotes": "Pacote",
    "pct": "Pacote",
    "frasco": "Frasco",
    "frascos": "Frasco",
    "fr": "Frasco",
}

REPORT_COLUMN_ALIASES = {
    "material": {
        "material",
        "materiais",
        "nome do material",
        "nome",
        "nome do item",
        "descricao",
        "descricao do material",
    },
    "sector": {"setor", "categoria", "setor categoria", "setor ou categoria"},
    "number": {"n", "no", "numero", "numero do item", "item", "codigo", "cod", "ordem"},
    "unit": {
        "unidade",
        "unidade de medida",
        "como solicitar",
        "como solicitar unidade",
        "un",
    },
    "forecast": {"previsao mensal", "previsao", "meta mensal", "meta"},
    "quantity": {
        "quantidade",
        "quantidade em estoque",
        "quantidade no estoque",
        "estoque",
        "saldo",
        "quantidade atual",
    },
}

# Usado apenas quando não existe nenhuma coluna com o nome do material.
# Na planilha oficial a coluna "ITEM" é o número, não o nome.
REPORT_WEAK_MATERIAL_ALIASES = {"item"}


def strip_accents(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char))


def normalize_header(value):
    text = strip_accents(value).strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_unit_label(value):
    """Converte o texto da unidade para um dos rótulos oficiais.

    Aceita variações como "caixa c/ 24", "unidade de 10 em 10",
    "pacote c/ 100" e "resma (pequena)", usando a unidade base.
    """
    text = str(value or "").strip()
    if not text:
        return None
    normalized = normalize_header(text)
    if normalized in UNIT_ALIASES:
        return UNIT_ALIASES[normalized]
    first_token = normalized.split(" ")[0] if normalized else ""
    return UNIT_ALIASES.get(first_token)


def valid_units_text():
    return ", ".join(unit["label"] for unit in UNITS)


REPORT_ALIASES_NORMALIZED = {
    key: {normalize_header(alias) for alias in aliases}
    for key, aliases in REPORT_COLUMN_ALIASES.items()
}
REPORT_WEAK_MATERIAL_ALIASES_NORMALIZED = {
    normalize_header(alias) for alias in REPORT_WEAK_MATERIAL_ALIASES
}


def find_report_header(rows):
    """Procura a linha de cabeçalho nas primeiras linhas da planilha.

    A coluna do material tem prioridade sobre a coluna "ITEM", que na
    planilha oficial contém apenas a numeração.
    """
    for index, row in enumerate(rows[:10]):
        normalized = [normalize_header(value) for value in row]
        mapping = {}

        material_aliases = REPORT_ALIASES_NORMALIZED["material"]
        for position, text in enumerate(normalized):
            if text in material_aliases:
                mapping["material"] = position
                break
        if "material" not in mapping:
            for position, text in enumerate(normalized):
                if text in REPORT_WEAK_MATERIAL_ALIASES_NORMALIZED:
                    mapping["material"] = position
                    break

        for key in ("sector", "number", "unit", "forecast", "quantity"):
            for position, text in enumerate(normalized):
                if text in REPORT_ALIASES_NORMALIZED[key]:
                    if key == "number" and position == mapping.get("material"):
                        continue
                    mapping[key] = position
                    break

        if "material" in mapping and "quantity" in mapping:
            return index, mapping
    return None


def parse_import_sector(value):
    text = strip_accents(value).strip().lower()
    if not text:
        return None
    if "pedagog" in text:
        return "pedagogico"
    if "limpeza" in text:
        return "limpeza"
    return None


def detect_import_sector(rows, header_index, sheet_title=None):
    """Descobre o setor pelo nome da aba, pelo título ou pelas linhas acima do cabeçalho."""
    sector = parse_import_sector(sheet_title)
    if sector:
        return sector
    for row in rows[:header_index]:
        for value in row or ():
            sector = parse_import_sector(value)
            if sector:
                return sector
    return None


def parse_import_number(value):
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip().replace(" ", "")
        if "," in text and "." in text:
            # Formato brasileiro: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        number = float(text)
    if not math.isfinite(number):
        raise ValueError("número inválido")
    return round(number, 3)


EXCEL_TEXT_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def write_excel_text(cell, value):
    """Grava texto sem permitir que o Excel o interprete como fórmula.

    O ``quotePrefix`` é o mesmo recurso usado pelo Excel quando o usuário
    digita um apóstrofo antes do conteúdo: a célula continua sendo texto e o
    apóstrofo não aparece na planilha.
    """
    text = "" if value is None else str(value)
    cell.value = text
    cell.data_type = "s"
    if text.startswith(EXCEL_TEXT_PREFIXES):
        cell.quotePrefix = True


def fill_official_sheet(sheet, sector, items, month_label):
    """Monta uma aba no formato oficial enviado à prefeitura."""
    title = REPORT_SECTOR_TITLES[sector]
    last_row = 4 + len(items)

    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:E1")
    sheet.merge_cells("A2:D2")
    sheet.merge_cells("A3:E3")

    write_excel_text(sheet["A1"], REPORT_LINE_1)
    write_excel_text(sheet["A2"], title)
    write_excel_text(sheet["E2"], f"MÊS: {month_label}")
    write_excel_text(sheet["A3"], REPORT_SCHOOL_LINE)

    for column, header in enumerate(REPORT_HEADERS, start=1):
        write_excel_text(sheet.cell(row=4, column=column), header)

    for position, item in enumerate(items, start=1):
        row = 4 + position
        unit = normalize_unit_label(item.get("unit")) or item.get("unit") or ""
        sheet.cell(row=row, column=1, value=position)
        write_excel_text(sheet.cell(row=row, column=2), item.get("name") or "")
        write_excel_text(sheet.cell(row=row, column=3), unit)
        sheet.cell(row=row, column=4, value=item.get("monthly_forecast") or 0)
        sheet.cell(row=row, column=5, value=item.get("quantity") or 0)

    thin = Side(style="thin", color="B7C4CE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for row in sheet.iter_rows(min_row=1, max_row=last_row, min_col=1, max_col=5):
        for cell in row:
            cell.border = border

    sheet["A1"].font = Font(bold=True, size=12)
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    sheet["A2"].font = Font(bold=True, size=11)
    sheet["A2"].alignment = Alignment(horizontal="left", vertical="center")
    sheet["E2"].font = Font(bold=True, size=11)
    sheet["E2"].alignment = Alignment(horizontal="right", vertical="center")
    sheet["A3"].font = Font(bold=True, size=10)
    sheet["A3"].alignment = Alignment(horizontal="left", vertical="center")

    header_fill = PatternFill("solid", fgColor="D9E1F2")
    for cell in sheet[4]:
        cell.font = Font(bold=True, size=10)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    if items:
        for row in sheet.iter_rows(min_row=5, max_row=last_row, min_col=1, max_col=5):
            for cell in row:
                cell.font = Font(size=10)
                cell.alignment = Alignment(horizontal="center", vertical="center")
            row[1].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

        for row in sheet.iter_rows(min_row=5, max_row=last_row, min_col=4, max_col=5):
            for cell in row:
                cell.number_format = "0.###"

    sheet.row_dimensions[1].height = 24
    sheet.row_dimensions[2].height = 20
    sheet.row_dimensions[3].height = 20
    sheet.row_dimensions[4].height = 28

    widths = {"A": 6, "B": 46, "C": 26, "D": 18, "E": 12}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    sheet.freeze_panes = "A5"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = f"A1:E{last_row}"
    sheet.print_title_rows = "1:4"


def add_report_instructions(workbook):
    guide = workbook.create_sheet("Instruções")
    guide.column_dimensions["A"].width = 110
    instructions = [
        "Como preencher a planilha de estoque",
        "",
        "• N: numeração automática. A importação usa o nome do material, nunca este número.",
        "• MATERIAIS: nome do item (obrigatório).",
        f"• COMO SOLICITAR (UNIDADE): {valid_units_text()}.",
        "• PREVISAO MENSAL: quantidade estimada para o mês (pode ficar em branco).",
        "• ESTOQUE: saldo atual do item (obrigatório).",
        "",
        "Cada aba representa um setor: Pedagógico ou Limpeza.",
        "Itens com o mesmo nome no mesmo setor são atualizados; itens novos são cadastrados.",
    ]
    for index, line in enumerate(instructions, start=1):
        guide.cell(row=index, column=1, value=line)
    guide["A1"].font = Font(bold=True, size=14, color="0F766E")


def build_report_workbook(groups, *, template=False):
    """Gera o arquivo oficial. groups = [(setor, itens), ...]."""
    workbook = Workbook()
    month_label = MONTHS[datetime.now().month - 1].upper()

    for index, (sector, items) in enumerate(groups):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = SECTORS[sector]

        rows = items
        if template:
            rows = [
                {
                    "name": name,
                    "unit": unit,
                    "monthly_forecast": forecast,
                    "quantity": quantity,
                }
                for name, unit, forecast, quantity in REPORT_TEMPLATE_ITEMS.get(sector, [])
            ]

        fill_official_sheet(sheet, sector, rows, month_label)

    if template:
        add_report_instructions(workbook)

    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    buffer.seek(0)
    return buffer


def report_filename(prefix, sector=None):
    date_part = datetime.now().strftime("%Y-%m-%d")
    suffix = f"-{sector}" if sector in SECTORS else ""
    return f"{prefix}-{date_part}{suffix}.xlsx"


# ---------------------------------------------------------------------------
# Rotas de páginas
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return render_template(
        "index.html",
        app_name=APP_NAME,
        current_user=g.user,
        csrf_token=session.get("csrf_token", ""),
    )


@app.get("/login")
def login():
    if g.user is not None:
        return redirect(url_for("index"))

    has_users = (
        get_db().execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"] > 0
    )
    message = None
    if request.args.get("saiu") == "1":
        message = "Sessão encerrada com segurança."
    elif request.args.get("expirou") == "1":
        message = "Sua sessão expirou. Entre novamente para continuar."

    return render_template(
        "login.html",
        app_name=APP_NAME,
        mode="login" if has_users else "setup",
        message=message,
    )


# ---------------------------------------------------------------------------
# Rotas da API
# ---------------------------------------------------------------------------


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = normalize_username(data.get("username"))
    password = data.get("password") or ""

    if not username or not password:
        raise ApiError("Informe o usuário e a senha.")

    attempt_key = login_attempt_key(username)
    remaining = login_lock_seconds(attempt_key)
    if remaining:
        raise ApiError(
            f"Muitas tentativas de acesso. Aguarde {remaining} segundos e tente novamente.",
            429,
        )

    connection = get_db()
    user = connection.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    if user is not None:
        valid = check_password_hash(user["password_hash"], password)
    else:
        check_password_hash(DUMMY_PASSWORD_HASH, password)
        valid = False

    if not valid:
        register_login_failure(attempt_key)
        raise ApiError("Usuário ou senha inválidos.", 401)

    clear_login_failures(attempt_key)
    start_user_session(user)
    return jsonify(
        ok=True,
        user={"username": user["username"], "display_name": user["display_name"]},
    )


@app.post("/api/setup")
def api_setup():
    connection = get_db()
    if connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"] > 0:
        raise ApiError("O sistema já possui uma conta de acesso.", 409)

    data = request.get_json(silent=True) or {}
    display_name = (data.get("display_name") or "").strip()
    username = normalize_username(data.get("username"))
    password = data.get("password") or ""
    password_confirm = data.get("password_confirm")

    if not 2 <= len(display_name) <= 80:
        raise ApiError("Informe o nome do responsável (de 2 a 80 caracteres).")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", username):
        raise ApiError(
            "O usuário deve ter de 3 a 32 caracteres, usando letras, números, ponto, hífen ou sublinhado."
        )
    if len(password) < 8:
        raise ApiError("A senha deve ter pelo menos 8 caracteres.")
    if len(password) > 128:
        raise ApiError("A senha deve ter no máximo 128 caracteres.")
    if password_confirm is not None and password != password_confirm:
        raise ApiError("As senhas não conferem.")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO users (username, display_name, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (username, display_name, generate_password_hash(password), now),
            )
    except sqlite3.IntegrityError:
        raise ApiError("Este nome de usuário já está em uso.", 409)

    user = connection.execute(
        "SELECT id, username, display_name, created_at FROM users WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    start_user_session(user)
    return jsonify(ok=True, user=dict(user)), 201


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/bootstrap")
def api_bootstrap():
    connection = get_db()
    return jsonify(
        {
            "items": fetch_items(connection),
            "categories": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT c.*, (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id)
                        AS item_count
                    FROM categories c
                    ORDER BY c.sector, c.name COLLATE NOCASE
                    """
                ).fetchall()
            ],
            "dashboard": build_dashboard(connection),
            "units": UNITS,
            "sectors": SECTORS,
        }
    )


@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(build_dashboard(get_db()))


@app.get("/api/categories")
def api_categories():
    rows = get_db().execute(
        """
        SELECT c.*, (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id) AS item_count
        FROM categories c
        ORDER BY c.sector, c.name COLLATE NOCASE
        """
    ).fetchall()
    return jsonify(categories=[dict(row) for row in rows])


@app.get("/api/items")
def api_items():
    connection = get_db()
    items = fetch_items(connection)
    sector = request.args.get("sector")
    if sector in SECTORS:
        items = [item for item in items if item["sector"] == sector]
    search = (request.args.get("q") or "").strip().lower()
    if search:
        items = [
            item
            for item in items
            if search in item["name"].lower()
            or search in item["category_name"].lower()
        ]
    return jsonify(items=items)


@app.post("/api/items")
def api_create_item():
    data = request.get_json(silent=True) or {}
    name, category, unit, forecast = parse_item_payload(data)
    initial = parse_number(data.get("initial_quantity", 0), "saldo inicial")
    if initial > 1000000:
        raise ApiError("O saldo inicial informado é muito grande.")

    connection = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with connection:
        duplicate = find_duplicate_item(connection, name, category["sector"])
        if duplicate is not None:
            raise ApiError(
                f"Já existe um item com esse nome no setor {SECTORS[category['sector']]}.",
                409,
            )
        cursor = connection.execute(
            """
            INSERT INTO items (name, category_id, unit, monthly_forecast, quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, category["id"], unit, forecast, initial, now),
        )
        item_id = cursor.lastrowid
        if initial > 0:
            connection.execute(
                """
                INSERT INTO movements
                    (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
                VALUES (?, 'entrada', ?, 'Saldo inicial', 0, ?, ?)
                """,
                (item_id, initial, initial, now),
            )
    return jsonify(item=fetch_item(connection, item_id)), 201


@app.put("/api/items/<int:item_id>")
def api_update_item(item_id):
    data = request.get_json(silent=True) or {}
    name, category, unit, forecast = parse_item_payload(data)
    connection = get_db()
    if fetch_item(connection, item_id) is None:
        raise ApiError("Item não encontrado.", 404)
    with connection:
        duplicate = find_duplicate_item(
            connection, name, category["sector"], exclude_id=item_id
        )
        if duplicate is not None:
            raise ApiError(
                f"Já existe um item com esse nome no setor {SECTORS[category['sector']]}.",
                409,
            )
        connection.execute(
            """
            UPDATE items
            SET name = ?, category_id = ?, unit = ?, monthly_forecast = ?
            WHERE id = ?
            """,
            (name, category["id"], unit, forecast, item_id),
        )
    return jsonify(item=fetch_item(connection, item_id))


@app.delete("/api/items/<int:item_id>")
def api_delete_item(item_id):
    connection = get_db()
    if fetch_item(connection, item_id) is None:
        raise ApiError("Item não encontrado.", 404)
    with connection:
        connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return jsonify(ok=True)


@app.get("/api/movements")
def api_movements():
    connection = get_db()
    limit = request.args.get("limit", default=30, type=int)
    limit = max(1, min(limit or 30, 200))
    filters = []
    params = []
    sector = request.args.get("sector")
    if sector in SECTORS:
        filters.append("c.sector = ?")
        params.append(sector)
    movement_type = request.args.get("type")
    if movement_type in ("entrada", "saida"):
        filters.append("m.type = ?")
        params.append(movement_type)
    query = MOVEMENTS_QUERY
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY m.created_at DESC, m.id DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(query, params).fetchall()
    return jsonify(movements=[serialize_movement(row) for row in rows])


@app.post("/api/movements")
def api_create_movement():
    data = request.get_json(silent=True) or {}
    try:
        item_id = int(data.get("item_id"))
    except (TypeError, ValueError):
        raise ApiError("Selecione um item para movimentar.")

    movement_type = data.get("type")
    if movement_type not in ("entrada", "saida"):
        raise ApiError("Tipo de movimentação inválido.")

    quantity = parse_number(data.get("quantity"), "quantidade", minimum=0.001)
    if quantity > 1000000:
        raise ApiError("A quantidade informada é muito grande.")

    note = (data.get("note") or "").strip()
    if len(note) > 200:
        raise ApiError("A observação deve ter no máximo 200 caracteres.")

    connection = get_db()
    item = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        raise ApiError("Item não encontrado.", 404)

    current = round(item["quantity"], 3)
    if movement_type == "saida" and quantity > current + 1e-9:
        available = f"{current:g}".replace(".", ",")
        raise ApiError(
            f"Estoque insuficiente: restam {available} {UNIT_SHORT.get(item['unit'], item['unit'])} de {item['name']}.",
            409,
        )

    new_quantity = current + quantity if movement_type == "entrada" else current - quantity
    new_quantity = max(round(new_quantity, 3), 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with connection:
        connection.execute(
            "UPDATE items SET quantity = ? WHERE id = ?", (new_quantity, item_id)
        )
        cursor = connection.execute(
            """
            INSERT INTO movements
                (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                movement_type,
                quantity,
                note or None,
                current,
                new_quantity,
                now,
            ),
        )
        movement_id = cursor.lastrowid

    row = connection.execute(
        MOVEMENTS_QUERY + " WHERE m.id = ?", (movement_id,)
    ).fetchone()
    return (
        jsonify(
            item=fetch_item(connection, item_id),
            movement=serialize_movement(row),
        ),
        201,
    )


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------


@app.get("/api/reports/export")
def api_export_report():
    sector = request.args.get("sector")
    if sector not in SECTORS:
        sector = None

    items = fetch_items(get_db())
    if not items:
        raise ApiError(
            "Não há itens cadastrados para exportar. Importe uma planilha ou cadastre um item.",
            400,
        )

    if sector:
        sector_items = [item for item in items if item["sector"] == sector]
        if not sector_items:
            raise ApiError(
                f"Não há itens no setor {SECTORS[sector]} para exportar.",
                400,
            )
        groups = [(sector, sector_items)]
    else:
        groups = [
            (key, [item for item in items if item["sector"] == key])
            for key in SECTORS
        ]
        # Abas com itens primeiro: ao abrir o Excel, a primeira aba
        # sempre mostra dados, mesmo que um setor esteja vazio.
        groups.sort(key=lambda group: 0 if group[1] else 1)

    buffer = build_report_workbook(groups)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=report_filename("relatorio-estoque", sector),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/reports/template")
def api_report_template():
    buffer = build_report_workbook([(key, []) for key in SECTORS], template=True)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="modelo-estoque.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/reports/import")
def api_import_report():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise ApiError("Selecione uma planilha .xlsx para importar.")
    if not uploaded.filename.lower().endswith(".xlsx"):
        raise ApiError("O arquivo deve estar no formato .xlsx (Excel).")

    try:
        workbook = load_workbook(uploaded.stream, read_only=True, data_only=True)
        sheets = [
            (sheet.title, list(sheet.iter_rows(values_only=True)))
            for sheet in workbook.worksheets
        ]
        workbook.close()
    except Exception:
        raise ApiError(
            "Não foi possível ler a planilha. Verifique se o arquivo é um Excel válido."
        )

    if not any(rows for _, rows in sheets):
        raise ApiError("A planilha está vazia.")

    prepared = []
    total_rows = 0
    for sheet_title, sheet_rows in sheets:
        header = find_report_header(sheet_rows)
        if header is None:
            continue
        header_index, columns = header
        data_rows = sheet_rows[header_index + 1 :]
        total_rows += len(data_rows)
        prepared.append((sheet_title, sheet_rows, header_index, columns, data_rows))

    if not prepared:
        raise ApiError(
            "Cabeçalho não encontrado. A planilha precisa ter as colunas MATERIAIS e ESTOQUE."
        )
    if total_rows > 1000:
        raise ApiError("A planilha pode ter no máximo 1000 linhas de itens.")

    fallback_sector = request.form.get("sector")
    if fallback_sector not in SECTORS:
        fallback_sector = None

    connection = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created = 0
    updated = 0
    skipped = 0
    errors = []
    warnings = []
    changes = []

    def add_error(sheet, line, message, item=None):
        entry = {"sheet": sheet, "line": line, "message": message}
        if item:
            entry["item"] = item
        errors.append(entry)

    def add_warning(sheet, line, message, item=None):
        entry = {"sheet": sheet, "line": line, "message": message}
        if item:
            entry["item"] = item
        warnings.append(entry)

    with connection:
        for sheet_title, sheet_rows, header_index, columns, data_rows in prepared:
            sheet_sector = detect_import_sector(sheet_rows, header_index, sheet_title)

            for offset, row in enumerate(data_rows):
                line = header_index + 2 + offset
                if not row or all(value is None or value == "" for value in row):
                    continue

                def cell(key):
                    position = columns.get(key)
                    if position is None or position >= len(row):
                        return None
                    return row[position]

                name = str(cell("material") or "").strip()
                if not name:
                    skipped += 1
                    add_error(sheet_title, line, "Material não informado.")
                    continue
                if len(name) > 120:
                    skipped += 1
                    add_error(sheet_title, line, "Nome do material muito longo.", name)
                    continue

                row_sector = (
                    parse_import_sector(cell("sector")) if "sector" in columns else None
                )
                sector = row_sector or sheet_sector or fallback_sector
                if sector is None:
                    skipped += 1
                    add_error(sheet_title, line, "Setor não informado ou inválido.", name)
                    continue

                try:
                    quantity = parse_import_number(cell("quantity"))
                    forecast = (
                        parse_import_number(cell("forecast"))
                        if "forecast" in columns
                        else 0.0
                    )
                except (TypeError, ValueError):
                    skipped += 1
                    add_error(sheet_title, line, "Quantidade ou previsão inválida.", name)
                    continue

                if quantity < 0 or forecast < 0:
                    skipped += 1
                    add_error(
                        sheet_title,
                        line,
                        "Quantidade e previsão não podem ser negativas.",
                        name,
                    )
                    continue
                if quantity > 1000000 or forecast > 100000:
                    skipped += 1
                    add_error(
                        sheet_title,
                        line,
                        "Quantidade ou previsão acima do limite.",
                        name,
                    )
                    continue

                raw_unit = str(cell("unit") or "").strip()
                unit = normalize_unit_label(raw_unit) if raw_unit else None
                if unit is None:
                    unit = "Unidade"
                    if raw_unit and raw_unit != "*":
                        add_warning(
                            sheet_title,
                            line,
                            f"Unidade “{raw_unit}” não reconhecida; o item foi importado como Unidade.",
                            name,
                        )

                category = category_for_sector(connection, sector)
                if category is None:
                    skipped += 1
                    add_error(
                        sheet_title, line, "Categoria do setor não encontrada.", name
                    )
                    continue

                existing = connection.execute(
                    """
                    SELECT * FROM items
                    WHERE category_id = ? AND name = ? COLLATE NOCASE
                    """,
                    (category["id"], name),
                ).fetchone()

                if existing is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO items
                            (name, category_id, unit, monthly_forecast, quantity, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (name, category["id"], unit, forecast, quantity, now),
                    )
                    if quantity > 0:
                        connection.execute(
                            """
                            INSERT INTO movements
                                (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
                            VALUES (?, 'entrada', ?, 'Saldo inicial (importação)', 0, ?, ?)
                            """,
                            (cursor.lastrowid, quantity, quantity, now),
                        )
                    created += 1
                    changes.append(
                        {
                            "name": name,
                            "sector": sector,
                            "sector_label": SECTORS[sector],
                            "action": "created",
                        }
                    )
                else:
                    previous = round(existing["quantity"], 3)
                    connection.execute(
                        """
                        UPDATE items
                        SET unit = ?, monthly_forecast = ?, quantity = ?
                        WHERE id = ?
                        """,
                        (unit, forecast, quantity, existing["id"]),
                    )
                    difference = round(quantity - previous, 3)
                    if abs(difference) > 1e-9:
                        connection.execute(
                            """
                            INSERT INTO movements
                                (item_id, type, quantity, note, previous_quantity, new_quantity, created_at)
                            VALUES (?, ?, ?, 'Importação de planilha', ?, ?, ?)
                            """,
                            (
                                existing["id"],
                                "entrada" if difference > 0 else "saida",
                                abs(difference),
                                previous,
                                quantity,
                                now,
                            ),
                        )
                    updated += 1
                    changes.append(
                        {
                            "name": name,
                            "sector": sector,
                            "sector_label": SECTORS[sector],
                            "action": "updated",
                        }
                    )

    by_sector = {key: 0 for key in SECTORS}
    for change in changes:
        by_sector[change["sector"]] += 1

    return jsonify(
        ok=True,
        created=created,
        updated=updated,
        skipped=skipped,
        total=created + updated,
        by_sector=by_sector,
        items=changes[:200],
        items_truncated=len(changes) > 200,
        errors=errors[:50],
        errors_truncated=len(errors) > 50,
        warnings=warnings[:50],
        warnings_truncated=len(warnings) > 50,
    )


migrate_legacy_database()
init_db()


if __name__ == "__main__":
    # O modo debug expõe o depurador; habilite apenas localmente com FLASK_DEBUG=1.
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
