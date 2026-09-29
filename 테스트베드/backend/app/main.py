import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://erp_user:erp_password_change_me@localhost:5432/erp").replace("postgresql+psycopg", "postgresql")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

app = FastAPI(title="AI ERP Testbed API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    price: Decimal = Field(ge=0)
    stock_quantity: int = Field(ge=0)


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    items: list[OrderItemCreate] = Field(min_length=1)


class AIQuestion(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def serialize(row):
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
    return result


def get_ai_context():
    with psycopg.connect(DATABASE_URL) as conn:
        products = conn.execute("SELECT name, stock_quantity, price FROM products ORDER BY stock_quantity ASC").fetchall()
        orders = conn.execute("SELECT COUNT(*), COALESCE(SUM(total_amount), 0) FROM orders").fetchone()
        recent = conn.execute("SELECT customer_name, total_amount, status, created_at FROM orders ORDER BY id DESC LIMIT 5").fetchall()
    return {"products": [{"name": p[0], "stock_quantity": p[1], "price": float(p[2])} for p in products], "order_summary": {"count": orders[0], "total_amount": float(orders[1])}, "recent_orders": [{"customer_name": x[0], "total_amount": float(x[1]), "status": x[2], "created_at": x[3].isoformat()} for x in recent]}


@app.get("/api/ai/status")
def ai_status():
    return {"configured": bool(GEMINI_API_KEY), "provider": "gemini", "model": GEMINI_MODEL}


@app.post("/api/ai/ask")
def ask_ai(request: AIQuestion):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY가 설정되지 않았습니다.")
    context = get_ai_context()
    prompt = f"너는 소규모 주문·재고 ERP의 분석 도우미다. 아래는 서버가 선별한 읽기 전용 데이터다. 데이터에 없는 내용은 추측하지 말고, 짧고 쉬운 한국어로 답하라. SQL을 만들거나 실행했다고 말하지 말라.\n\n데이터:\n{json.dumps(context, ensure_ascii=False)}\n\n사용자 질문:\n{request.question}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        return {"answer": result["candidates"][0]["content"]["parts"][0]["text"], "provider": "gemini", "model": GEMINI_MODEL}
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API 오류: {exc.read().decode('utf-8', errors='replace')[:500]}")
    except (KeyError, IndexError, urllib.error.URLError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini 응답을 처리하지 못했습니다: {exc}")


@app.get("/health")
def health():
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/products")
def list_products():
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute("SELECT id, sku, name, price, stock_quantity, created_at FROM products ORDER BY id").fetchall()
        columns = ["id", "sku", "name", "price", "stock_quantity", "created_at"]
    return [serialize(dict(zip(columns, row))) for row in rows]


@app.post("/api/products", status_code=201)
def create_product(product: ProductCreate):
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            row = conn.execute(
                "INSERT INTO products (sku, name, price, stock_quantity) VALUES (%s, %s, %s, %s) RETURNING id, sku, name, price, stock_quantity",
                (product.sku, product.name, product.price, product.stock_quantity),
            ).fetchone()
            conn.execute("INSERT INTO audit_logs (action, entity_type, entity_id, detail) VALUES (%s, %s, %s, %s)", ("CREATE", "product", row[0], json.dumps(product.model_dump(), default=str)))
            conn.commit()
        return serialize(dict(zip(["id", "sku", "name", "price", "stock_quantity"], row)))
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="SKU가 이미 존재합니다.")


@app.get("/api/orders")
def list_orders():
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute("SELECT id, customer_name, status, total_amount, created_at FROM orders ORDER BY id DESC").fetchall()
        columns = ["id", "customer_name", "status", "total_amount", "created_at"]
    return [serialize(dict(zip(columns, row))) for row in rows]


@app.post("/api/orders", status_code=201)
def create_order(order: OrderCreate):
    with psycopg.connect(DATABASE_URL) as conn:
        try:
            total = Decimal("0")
            product_rows = []
            for item in order.items:
                row = conn.execute("SELECT id, name, price, stock_quantity FROM products WHERE id = %s FOR UPDATE", (item.product_id,)).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"상품 ID {item.product_id}를 찾을 수 없습니다.")
                if row[3] < item.quantity:
                    raise HTTPException(status_code=400, detail=f"'{row[1]}'의 재고가 부족합니다.")
                total += row[2] * item.quantity
                product_rows.append((item, row))

            order_row = conn.execute("INSERT INTO orders (customer_name, total_amount) VALUES (%s, %s) RETURNING id", (order.customer_name, total)).fetchone()
            for item, product in product_rows:
                conn.execute("INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (%s, %s, %s, %s)", (order_row[0], product[0], item.quantity, product[2]))
                conn.execute("UPDATE products SET stock_quantity = stock_quantity - %s WHERE id = %s", (item.quantity, product[0]))
            conn.execute("INSERT INTO audit_logs (action, entity_type, entity_id, detail) VALUES (%s, %s, %s, %s)", ("CREATE", "order", order_row[0], json.dumps(order.model_dump(), default=str)))
            conn.commit()
            return {"id": order_row[0], "customer_name": order.customer_name, "total_amount": float(total), "status": "CONFIRMED"}
        except HTTPException:
            conn.rollback()
            raise
