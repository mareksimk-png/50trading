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
# ⚙️  KONFIGURACE
# ═══════════════════════════════════════════════════════

CAPITAL_EMAIL = os.environ.get("CAPITAL_EMAIL", "")
CAPITAL_API_KEY = os.environ.get("CAPITAL_API_KEY", "")
CAPITAL_API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")
USE_DEMO = os.environ.get("USE_DEMO", "True").lower() in ("true", "1", "yes")
EPIC = os.environ.get("EPIC", "CC.D.XAUUSD.CFD.IP")
POSITION_SIZE = float(os.environ.get("POSITION_SIZE", "0.5"))

# ═══════════════════════════════════════════════════════
# 🔧 Inicializace
# ═══════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(title="TradingView Capital.com Bot")

BASE_URL = "https://demo-api-capital.backend-capital.com" if USE_DEMO else "https://api-capital.backend-capital.com"

# ═══════════════════════════════════════════════════════
# 🔐  SESSION
# ═══════════════════════════════════════════════════════

class CapitalSession:
    def __init__(self):
        self.cst = None
        self.security_token = None
        self.logged_in = False

    def login(self) -> bool:
        url = f"{BASE_URL}/api/v1/session"
        headers = {"X-CAP-API-KEY": CAPITAL_API_KEY, "Content-Type": "application/json"}
        payload = {"identifier": CAPITAL_EMAIL, "password": CAPITAL_API_PASSWORD, "encryptedPassword": False}
        logger.info("🔑 Přihlašuji se k Capital.com...")
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            self.cst = resp.headers.get("CST")
            self.security_token = resp.headers.get("X-SECURITY-TOKEN")
            if not self.cst or not self.security_token:
                logger.error("❌ Tokeny nevráceny!")
                self.logged_in = False
                return False
            self.logged_in = True
            logger.info("✅ Přihlášení úspěšné!")
            return True
        except Exception as e:
            logger.error(f"❌ Chyba přihlášení: {e}")
            self.logged_in = False
            return False

    def get_headers(self) -> dict:
        if not self.logged_in:
            self.login()
        return {"CST": self.cst, "X-SECURITY-TOKEN": self.security_token, "Content-Type": "application/json"}

    def refresh(self) -> bool:
        logger.warning("🔄 Obnovuji session...")
        self.cst = None
        self.security_token = None
        self.logged_in = False
        return self.login()

capital = CapitalSession()

# ═══════════════════════════════════════════════════════
# 🛡️  DEKORÁTOR
# ═══════════════════════════════════════════════════════

def with_relogin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                logger.warning("⛔ Tokeny neplatné, obnovuji...")
                if capital.refresh():
                    return func(*args, **kwargs)
                raise Exception("Nepodařilo se obnovit session.")
            raise
    return wrapper

# ═══════════════════════════════════════════════════════
# 🛠  OBCHODNÍ FUNKCE
# ═══════════════════════════════════════════════════════

@with_relogin
def get_open_position():
    resp = requests.get(f"{BASE_URL}/api/v1/positions", headers=capital.get_headers(), timeout=15)
    resp.raise_for_status()
    for pos in resp.json().get("positions", []):
        if pos.get("market", {}).get("epic") == EPIC:
            return pos
    return None

@with_relogin
def close_position(deal_id: str):
    logger.info(f"🔒 Zavírám pozici {deal_id}...")
    requests.delete(f"{BASE_URL}/api/v1/positions/{deal_id}", headers=capital.get_headers(), timeout=15).raise_for_status()
    logger.info("✅ Pozice uzavřena.")
    return True

@with_relogin
def open_position(direction: str):
    logger.info(f"📈 Otevírám {direction} pozici na {EPIC} ({POSITION_SIZE} lotů)...")
    payload = {"epic": EPIC, "direction": direction, "size": POSITION_SIZE, "orderType": "MARKET"}
    resp = requests.post(f"{BASE_URL}/api/v1/positions", json=payload, headers=capital.get_headers(), timeout=15)
    resp.raise_for_status()
    logger.info(f"✅ Pozice otevřena! Deal ref: {resp.json().get('dealReference', 'N/A')}")
    return True

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
    logger.info(f"📩 Signál: {action}")
    current = get_open_position()
    desired = "BUY" if action == "BUY" else "SELL"
    if current:
        cd = current.get("position", {}).get("direction")
        did = current.get("position", {}).get("dealId")
        if cd != desired:
            logger.info(f"🔄 Převracím z {cd} na {desired}...")
            close_position(did)
        else:
            logger.info(f"ℹ️ Již držím {cd}.")
            return {"status": "ok", "message": "Pozice již otevřena."}
    open_position(desired)
    return {"status": "ok", "message": f"Signál {action} vykonán."}

@app.get("/")
async def health_check():
    return {"status": "alive", "bot": "Capital.com Bot", "epic": EPIC}

@app.get("/test-login")
async def test_login():
    return {"status": "ok" if capital.login() else "error", "message": "Přihlášení OK!" if capital.logged_in else "Selhalo"}

@app.get("/positions")
async def list_positions():
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/positions", headers=capital.get_headers(), timeout=15)
        resp.raise_for_status()
        positions = resp.json().get("positions", [])
        return {"status": "ok", "count": len(positions), "positions": positions}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ═══════════════════════════════════════════════════════
# 🚀  Spuštění
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
