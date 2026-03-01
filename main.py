"""
M3 Mobile — Stock Monitor
Каждый час проверяет стоки Treolan, OCS, M3
и шлёт алерты в Telegram при изменениях
"""

import os
import json
import asyncio
import logging
import httpx
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
TREOLAN_API = os.environ.get("TREOLAN_API", "https://treolan-api-production.up.railway.app")
OCS_API     = os.environ.get("OCS_API",     "https://ocs-api-production-de1e.up.railway.app")
M3_CSV      = os.environ.get("M3_STOCK_CSV", "")
DIFF_THRESHOLD = int(os.environ.get("DIFF_THRESHOLD", "1"))
STATE_FILE  = "/tmp/stock_state.json"

# ─── State ─────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ─── Fetch helpers ─────────────────────────────────
async def fetch_treolan(client: httpx.AsyncClient) -> dict:
    """Returns {articul: {stock, transit, name}}"""
    try:
        r = await client.get(f"{TREOLAN_API}/api/catalog", timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        return {
            item["articul"]: {
                "stock":   item.get("stock", 0),
                "transit": item.get("transit", 0),
                "name":    item.get("name", ""),
            }
            for item in items if item.get("articul")
        }
    except Exception as e:
        log.error(f"Treolan fetch error: {e}")
        return {}

async def fetch_ocs(client: httpx.AsyncClient) -> dict:
    """Returns {partNumber: {stock, transit, name}}"""
    try:
        r = await client.get(f"{OCS_API}/api/m3", timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        result = {}
        for item in items:
            key = item.get("partNumber") or item.get("itemId")
            if not key:
                continue
            stock   = sum(l.get("quantity", 0) for l in item.get("stock",   []))
            transit = sum(l.get("quantity", 0) for l in item.get("transit", []))
            result[key] = {
                "stock":   stock,
                "transit": transit,
                "name":    item.get("itemNameRus") or item.get("itemName", ""),
            }
        return result
    except Exception as e:
        log.error(f"OCS fetch error: {e}")
        return {}

async def fetch_m3(client: httpx.AsyncClient) -> dict:
    """Returns {articul: {stock, name}} from CSV"""
    if not M3_CSV:
        return {}
    try:
        r = await client.get(M3_CSV, timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return {}
        headers = [h.strip().strip('"').lower() for h in lines[0].split(",")]
        result = {}
        for line in lines[1:]:
            vals = [v.strip().strip('"') for v in line.split(",")]
            row = dict(zip(headers, vals))
            # Try common column names
            art = row.get("артикул") or row.get("sku") or row.get("articul") or ""
            stock_str = row.get("остаток") or row.get("stock") or row.get("наличие") or "0"
            name = row.get("наименование") or row.get("name") or ""
            if art:
                try:
                    result[art] = {"stock": int(float(stock_str)), "transit": 0, "name": name}
                except:
                    result[art] = {"stock": 0, "transit": 0, "name": name}
        return result
    except Exception as e:
        log.error(f"M3 fetch error: {e}")
        return {}

# ─── Diff engine ───────────────────────────────────
def compute_diffs(source: str, old: dict, new: dict) -> list[dict]:
    """Compare old vs new stock, return list of alert dicts"""
    alerts = []
    all_keys = set(old) | set(new)

    for key in all_keys:
        o = old.get(key, {})
        n = new.get(key, {})
        o_stock   = o.get("stock",   0)
        n_stock   = n.get("stock",   0)
        o_transit = o.get("transit", 0)
        n_transit = n.get("transit", 0)
        name = n.get("name") or o.get("name") or key

        # Появился в наличии
        if o_stock == 0 and n_stock > 0:
            alerts.append({
                "type": "appeared", "source": source,
                "articul": key, "name": name,
                "old_stock": o_stock, "new_stock": n_stock,
            })
        # Исчез
        elif o_stock > 0 and n_stock == 0:
            alerts.append({
                "type": "disappeared", "source": source,
                "articul": key, "name": name,
                "old_stock": o_stock, "new_stock": n_stock,
            })
        # Изменилось количество >= порога
        elif abs(n_stock - o_stock) >= DIFF_THRESHOLD and o_stock > 0 and n_stock > 0:
            alerts.append({
                "type": "changed", "source": source,
                "articul": key, "name": name,
                "old_stock": o_stock, "new_stock": n_stock,
                "diff": n_stock - o_stock,
            })

        # Новый транзит
        if o_transit == 0 and n_transit > 0:
            alerts.append({
                "type": "transit", "source": source,
                "articul": key, "name": name,
                "transit": n_transit,
            })

    return alerts

# ─── Telegram ──────────────────────────────────────
def format_alert(a: dict) -> str:
    source_emoji = {"Treolan": "🔵", "OCS": "🟢", "M3": "🟡"}.get(a["source"], "⚪")
    art  = a["articul"]
    name = a["name"][:50] + ("…" if len(a["name"]) > 50 else "")

    if a["type"] == "appeared":
        return (
            f"{source_emoji} <b>Появился в наличии</b> [{a['source']}]\n"
            f"📦 <code>{art}</code>\n"
            f"📝 {name}\n"
            f"✅ Стало: <b>{a['new_stock']} шт.</b>"
        )
    elif a["type"] == "disappeared":
        return (
            f"{source_emoji} <b>Исчез из наличия</b> [{a['source']}]\n"
            f"📦 <code>{art}</code>\n"
            f"📝 {name}\n"
            f"❌ Было: {a['old_stock']} шт."
        )
    elif a["type"] == "changed":
        arrow = "📈" if a["diff"] > 0 else "📉"
        sign  = "+" if a["diff"] > 0 else ""
        return (
            f"{source_emoji} {arrow} <b>Изменилось количество</b> [{a['source']}]\n"
            f"📦 <code>{art}</code>\n"
            f"📝 {name}\n"
            f"{a['old_stock']} → <b>{a['new_stock']} шт.</b> ({sign}{a['diff']})"
        )
    elif a["type"] == "transit":
        return (
            f"{source_emoji} 🚚 <b>Новый транзит</b> [{a['source']}]\n"
            f"📦 <code>{art}</code>\n"
            f"📝 {name}\n"
            f"🔜 Транзит: <b>{a['transit']} шт.</b>"
        )
    return ""

async def send_telegram(client: httpx.AsyncClient, text: str):
    try:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")

# ─── Main check job ────────────────────────────────
async def check_stocks():
    log.info("🔍 Checking stocks...")
    state = load_state()

    async with httpx.AsyncClient() as client:
        treolan, ocs, m3 = await asyncio.gather(
            fetch_treolan(client),
            fetch_ocs(client),
            fetch_m3(client),
        )

        all_alerts = []
        all_alerts += compute_diffs("Treolan", state.get("treolan", {}), treolan)
        all_alerts += compute_diffs("OCS",     state.get("ocs",     {}), ocs)
        all_alerts += compute_diffs("M3",      state.get("m3",      {}), m3)

        log.info(f"Found {len(all_alerts)} changes")

        # Send alerts (max 20 per run to avoid spam)
        for alert in all_alerts[:20]:
            text = format_alert(alert)
            if text:
                await send_telegram(client, text)
                await asyncio.sleep(0.3)  # avoid Telegram rate limit

        if len(all_alerts) > 20:
            await send_telegram(
                client,
                f"⚠️ Ещё {len(all_alerts) - 20} изменений не показаны (лимит)"
            )

        # Always save new state
        state["treolan"] = treolan
        state["ocs"]     = ocs
        state["m3"]      = m3
        state["last_check"] = datetime.now().isoformat()
        save_state(state)

    log.info("✅ Check complete")

# ─── FastAPI app ───────────────────────────────────
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 Starting stock monitor...")
    scheduler.add_job(check_stocks, "interval", hours=1, id="stock_check")
    scheduler.start()
    # Run immediately on startup
    asyncio.create_task(check_stocks())
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan, title="M3 Stock Monitor")

@app.get("/")
def root():
    state = load_state()
    return {
        "status": "running",
        "last_check": state.get("last_check", "never"),
        "treolan_items": len(state.get("treolan", {})),
        "ocs_items":     len(state.get("ocs",     {})),
        "m3_items":      len(state.get("m3",      {})),
    }

@app.post("/check")
async def force_check():
    """Принудительная проверка через API"""
    asyncio.create_task(check_stocks())
    return {"status": "check started"}
