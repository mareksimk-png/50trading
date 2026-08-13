"""
TradingView -> Capital.com Demo Bot
Strategie: Always-in-market (otočná pozice)
"""

import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from functools import wraps

# ═══════════════════════════════════════════════════════
# ⚙️  KONFIGURACE – čte z Environment Variables
# ═══════════════════════════════════════════════════════

CAPITAL_EMAIL = os.environ.get("CAPITAL_EMAIL", "")
CAPITAL_API_KEY = os.environ.get("CAPITAL_API_KEY", "")
CAPITAL_API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")

USE_DEMO = os.environ.get("USE_DEMO", "True").lower() in ("true", "1", "yes")
EPIC = os.environ.get("EPIC", "EURUSD")
POSITION_SIZE = float(os.environ.get("POSITION_SIZE", "0.5"))

# ═══════════════════════════════════════════════════════
# 🔧 Inicializace
# ═══════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TradingView Capital.com Bot")

BASE_URL = (
    "https://demo-api-capital.backend-capital.com"
    if USE_DEMO
    else "https://api-capital.backend-capital.com"
)


# ═══════════════════════════════════════════════════════
# 🔐  SPRÁVA SESSION
# ═══════════════════════════════════════════════════════

class CapitalSession:
    def __init__(self):
        self.cst = None
        self.security_token = None
        self.logged_in = False

    def login(self) -> bool:
        url = f"{BASE_URL}/api/v1/session"
        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "identifier": CAPITAL_EMAIL,
            "password": CAPITAL_API_PASSWORD,
            "encryptedPassword": False,
        }

        logger.info("🔑 Přihlašuji se k Capital.com...")
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()

            self.cst = resp.headers.get("CST")
            self.security_token = resp.headers.get("X-SECURITY-TOKEN")

            if not self.cst or not self.security_token:
                logger.error("❌ Capital.com nevrátil tokeny!")
                self.logged_in = False
                return False

            self.logged_in = True
            logger.info("✅ Přihlášení úspěšné!")
            return True

        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Přihlášení selhalo: {e}")
            if e.response is not None:
                logger.error(f"Response: {e.response.text}")
            self.logged_in = False
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Síťová chyba: {e}")
            self.logged_in = False
            return False

    def get_headers(self) -> dict:
        if not self.logged_in:
            self.login()
        return {
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.security_token,
            "Content-Type": "application/json",
        }

    def refresh(self) -> bool:
        logger.warning("🔄 Session vypršela, obnovuji...")
        self.cst = None
        self.security_token = None
        self.logged_in = False
        return self.login()


capital = CapitalSession()


# ═══════════════════════════════════════════════════════
# 🛡️  DEKORÁTOR – auto-obnova session
# ═══════════════════════════════════════════════════════

def with_relogin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                logger.warning("⛔ Capital odmítl tokeny, zkouším znovu...")
                if capital.refresh():
                    return func(*args, **kwargs)
                else:
                    raise Exception("Nepodařilo se obnovit session.")
            else:
                raise
    return wrapper


# ═══════════════════════════════════════════════════════
# 🛠  OBCHODNÍ FUNKCE
# ═══════════════════════════════════════════════════════

@with_relogin
def get_open_position():
    url = f"{BASE_URL}/api/v1/positions"
    try:
        resp = requests.get(url, headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        positions = resp.json().get("positions", [])
        for pos in positions:
            if pos.get("market", {}).get("epic") == EPIC:
                return pos
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Chyba při získávání pozic: {e}")
        if e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        raise


@with_relogin
def close_position(deal_id: str):
    logger.info(f"🔒 Zavírám pozici {deal_id}...")
    url = f"{BASE_URL}/api/v1/positions/{deal_id}"
    try:
        resp = requests.delete(url, headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        logger.info("✅ Pozice uzavřena.")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Chyba při zavírání: {e}")
        if e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        raise


@with_relogin
def open_position(direction: str):
    logger.info(f"📈 Otevírám {direction} pozici na {EPIC} ({POSITION_SIZE} lotů)...")
    
    payload = {
        "epic": EPIC,
        "direction": direction,
        "size": POSITION_SIZE,
        "orderType": "MARKET",
    }

    url = f"{BASE_URL}/api/v1/positions"
    try:
        resp = requests.post(url, json=payload, headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"✅ Pozice otevřena! Deal ref: {data.get('dealReference', 'N/A')}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Chyba při otevírání pozice: {e}")
        if e.response is not None:
            logger.error(f"Status code: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        raise


# ═══════════════════════════════════════════════════════
# 🌐  ENDPOINTY
# ═══════════════════════════════════════════════════════

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Neplatný JSON")

    action = data.get("action", "").upper()
    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Akce musí být BUY nebo SELL")

    logger.info(f"📩 Přijat signál: {action}")

    current = get_open_position()
    desired_direction = "BUY" if action == "BUY" else "SELL"

    if current:
        current_direction = current.get("position", {}).get("direction")
        deal_id = current.get("position", {}).get("dealId")

        if current_direction != desired_direction:
            logger.info(f"🔄 Mám opačnou pozici ({current_direction}). Převracím...")
            close_position(deal_id)
        else:
            logger.info(f"ℹ️ Již držím {current_direction}. Nic se nemění.")
            return {"status": "ok", "message": "Pozice již správně otevřena."}

    success = open_position(desired_direction)
    return {"status": "ok", "message": f"Signál {action} vykonán."}


@app.get("/")
async def health_check():
    return {
        "status": "alive",
        "bot": "TradingView Capital.com Bot",
        "mode": "DEMO" if USE_DEMO else "LIVE",
        "epic": EPIC,
        "size": POSITION_SIZE,
    }


@app.get("/test-login")
async def test_login():
    """Otestuj přihlášení k Capital.com."""
    success = capital.login()
    if success:
        return {"status": "ok", "message": "Přihlášení k Capital.com funguje!"}
    else:
        return {"status": "error", "message": "Přihlášení selhalo. Zkontroluj API klíč, heslo a email."}


@app.get("/positions")
async def list_positions():
    """Vypíše všechny otevřené pozice (diagnostika)."""
    try:
        url = f"{BASE_URL}/api/v1/positions"
        resp = requests.get(url, headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        positions = resp.json().get("positions", [])
        return {
            "status": "ok",
            "positions_count": len(positions),
            "positions": positions,
        }
    except requests.exceptions.HTTPError as e:
        return {
            "status": "error",
            "message": str(e),
            "response": e.response.text if e.response else "N/A",
        }


@app.get("/markets")
async def search_markets(search: str = ""):
    """Vyhledá trhy podle názvu (pro nalezení správného EPIC)."""
    try:
        url = f"{BASE_URL}/api/v1/markets?searchTerm={search or EPIC}"
        resp = requests.get(url, headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": "ok",
            "markets": data,
        }
    except requests.exceptions.HTTPError as e:
        return {
            "status": "error",
            "message": str(e),
            "response": e.response.text if e.response else "N/A",
        }


# ═══════════════════════════════════════════════════════
# 🚀  Spuštění
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
