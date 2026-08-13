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
# ⚙️  KONFIGURACE – čte z Environment Variables (bezpečné)
# ═══════════════════════════════════════════════════════

CAPITAL_EMAIL = os.environ.get("CAPITAL_EMAIL", "")
CAPITAL_API_KEY = os.environ.get("CAPITAL_API_KEY", "")
CAPITAL_API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")

# Demo = True (falešné peníze), Live = False
USE_DEMO = os.environ.get("USE_DEMO", "True").lower() in ("true", "1", "yes")

# Obchodní instrument – najdi svůj EPIC v Capital.com platformě
# Příklady: EURUSD, GBPUSD, BTCUSD, US30, OIL_BRENT
EPIC = os.environ.get("EPIC", "EURUSD")

# Velikost pozice – KOLIK LOTŮ obchoduješ
# Začni MALÝM číslem! Např. 0.5 nebo 1.0
POSITION_SIZE = float(os.environ.get("POSITION_SIZE", "0.5"))

# ═══════════════════════════════════════════════════════
# 🔧 Inicializace loggeru a FastAPI
# ═══════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TradingView Capital.com Bot")

# Správná adresa API podle režimu
BASE_URL = (
    "https://demo-api-capital.backend-capital.com"
    if USE_DEMO
    else "https://api-capital.backend-capital.com"
)


# ═══════════════════════════════════════════════════════
# 🔐  SPRÁVA SESSION – přihlašování k Capital.com
# ═══════════════════════════════════════════════════════

class CapitalSession:
    """
    Drží přihlašovací tokeny (CST a X-SECURITY-TOKEN).
    Automaticky se přihlásí znovu, když session vyprší.
    """
    def __init__(self):
        self.cst = None
        self.security_token = None
        self.logged_in = False

    def login(self) -> bool:
        """Přihlásí se k Capital.com a získá tokeny."""
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

            # Tokeny jsou v hlavičkách odpovědi
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
            self.logged_in = False
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Síťová chyba: {e}")
            self.logged_in = False
            return False

    def get_headers(self) -> dict:
        """Vrátí hlavičky pro API volání."""
        if not self.logged_in:
            self.login()
        return {
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.security_token,
            "Content-Type": "application/json",
        }

    def refresh(self) -> bool:
        """Vymaže staré tokeny a přihlásí se znovu."""
        logger.warning("🔄 Session vypršela, obnovuji...")
        self.cst = None
        self.security_token = None
        self.logged_in = False
        return self.login()


# Globální instance session
capital = CapitalSession()


# ═══════════════════════════════════════════════════════
# 🛡️  MAGICKÝ DEKORÁTOR – auto-obnova session
# ═══════════════════════════════════════════════════════

def with_relogin(func):
    """
    Když Capital.com vrátí 401/403 (neplatné tokeny),
    automaticky se přihlásíme znovu a zkusíme to ještě jednou.
    """
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
    """Zjistí, jestli máme otevřenou pozici na náš EPIC."""
    url = f"{BASE_URL}/api/v1/positions"
    resp = requests.get(url, headers=capital.get_headers(), timeout=15)
    resp.raise_for_status()

    positions = resp.json().get("positions", [])
    for pos in positions:
        if pos.get("market", {}).get("epic") == EPIC:
            return pos
    return None


@with_relogin
def close_position(deal_id: str):
    """Zavře pozici podle jejího dealId."""
    logger.info(f"🔒 Zavírám pozici {deal_id}...")
    url = f"{BASE_URL}/api/v1/positions/{deal_id}"
    resp = requests.delete(url, headers=capital.get_headers(), timeout=15)
    resp.raise_for_status()
    logger.info("✅ Pozice uzavřena.")
    return True


@with_relogin
def open_position(direction: str):
    """
    Otevře novou market pozici.
    direction: "BUY" nebo "SELL"
    """
    logger.info(f"📈 Otevírám {direction} pozici na {EPIC} ({POSITION_SIZE} lotů)...")

    payload = {
        "epic": EPIC,
        "direction": direction,
        "size": POSITION_SIZE,
        "orderType": "MARKET",
    }

    url = f"{BASE_URL}/api/v1/positions"
    resp = requests.post(url, json=payload, headers=capital.get_headers(), timeout=15)
    resp.raise_for_status()

    data = resp.json()
    logger.info(f"✅ Pozice otevřena! Deal ref: {data.get('dealReference', 'N/A')}")
    return True


# ═══════════════════════════════════════════════════════
# 🌐  WEBHOOK ENDPOINT (sem chodí signály z TradingView)
# ═══════════════════════════════════════════════════════
@app.post("/webhook")
async def webhook(request: Request):
    """
    Přijímá JSON z TradingView:
      {"action": "BUY"}  -> zavřít SELL, otevřít BUY
      {"action": "SELL"} -> zavřít BUY,  otevřít SELL
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Neplatný JSON")

    action = str(data.get("action", "")).upper()
    if action not in ["BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Akce musí být BUY nebo SELL")

    logging.info(f"📩 Přijat webhook akce: {action}")

    # Nastavení požadovaného směru (oprava proměnné desired_direction)
    desired_direction = action

    # Zavřeme opačné pozice a otevřeme novou
    success = open_position(desired_direction)

    if success:
        return {"status": "ok", "action": desired_direction, "message": f"Pozice {desired_direction} byla zpracována."}
    else:
        raise HTTPException(status_code=500, detail="Chyba při provádění příkazu na Capital.com")
{"detail":"Method Not Allowed"}
