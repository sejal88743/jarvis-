"""
Trading bot action for JARVIS — linked with the freqtrade bot in
D:\\Mark-LV-main\\JarvisProjects\\ai_stock_bot_setup\\freqtrade

Two capabilities, both exposed as one TOOL with a "mode" parameter:
  1. GET PRICE  — live price of a crypto pair via Binance public API
                  (no API key needed, works even without freqtrade setup)
  2. TRADE      — place a buy/sell order through the freqtrade REST API
                  (requires freqtrade API server running in dry-run or live mode)
"""
import json
import os
from urllib.request import urlopen, Request
from urllib.parse import quote_plus

# ── freqtrade installation path ──────────────────────────────────────────────
FREQTRADE_DIR = r"D:\Mark-LV-main\JarvisProjects\ai_stock_bot_setup\freqtrade"
FREQTRADE_API = os.environ.get("FREQTRADE_API_URL", "http://127.0.0.1:8080/api/v1")
FREQTRADE_USER = os.environ.get("FREQTRADE_API_USER", "freqtrader")
FREQTRADE_PASS = os.environ.get("FREQTRADE_API_PASS", "changeme")

# Binance public ticker endpoint (no key required)
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
BINANCE_24H = "https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"


def _get_json(url: str) -> dict:
    """GET a URL and parse the JSON response (5s timeout)."""
    req = Request(url, headers={"User-Agent": "JARVIS-Trading/1.0"})
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _freqtrade_post(path: str, payload: dict) -> dict:
    """POST an authenticated command to the freqtrade REST API."""
    import base64
    token = base64.b64encode(f"{FREQTRADE_USER}:{FREQTRADE_PASS}".encode()).decode()
    req = Request(
        f"{FREQTRADE_API}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_symbol(symbol: str) -> str:
    """'bitcoin' -> 'BTCUSDT', 'btc' -> 'BTCUSDT', 'ethusdt' -> 'ETHUSDT'"""
    s = symbol.strip().upper().replace("-", "").replace("/", "").replace(" ", "")
    aliases = {
        "BITCOIN": "BTCUSDT", "BTC": "BTCUSDT", "BTCUSDT": "BTCUSDT",
        "ETHEREUM": "ETHUSDT", "ETH": "ETHUSDT", "ETHUSDT": "ETHUSDT",
        "SOLANA": "SOLUSDT", "SOL": "SOLUSDT",
        "BNB": "BNBUSdt".upper(),
        "XRP": "XRPUSDT", "RIPPLE": "XRPUSDT",
        "DOGE": "DOGEUSDT", "DOGECOIN": "DOGEUSDT",
        "ADA": "ADAUSDT", "CARDANO": "ADAUSDT",
        "MATIC": "MATICUSDT", "POLYGON": "MATICUSDT",
        "LTC": "LTCUSDT", "LITECOIN": "LTCUSDT",
    }
    return aliases.get(s, s)


def trading_action(parameters: dict, player=None, session_memory=None) -> str:
    """Main entry point for JARVIS trading commands."""
    mode = (parameters.get("mode") or "price").lower()
    symbol_raw = parameters.get("symbol") or "BTC"

    try:
        if mode in ("price", "check", "get_price"):
            return _get_price(symbol_raw, player)
        if mode in ("buy", "trade_buy"):
            return _trade(symbol_raw, "buy", parameters.get("amount"), player)
        if mode in ("sell", "trade_sell"):
            return _trade(symbol_raw, "sell", parameters.get("amount"), player)
        return f"Sir, unknown trading mode '{mode}'. Use price, buy, or sell."
    except Exception as e:
        msg = f"Sir, the trading system hit an error: {e}"
        _log(msg, player)
        return msg


def _get_price(symbol_raw: str, player=None) -> str:
    """Fetch live price from Binance public API."""
    symbol = _normalize_symbol(symbol_raw)
    try:
        data = _get_json(BINANCE_TICKER.format(symbol=quote_plus(symbol)))
        price = float(data["price"])
        # also try 24h stats for a richer answer (non-fatal)
        change = ""
        try:
            h = _get_json(BINANCE_24H.format(symbol=quote_plus(symbol)))
            pct = float(h.get("priceChangePercent", 0))
            change = f" (24h: {'+' if pct >= 0 else ''}{pct:.2f}%)"
        except Exception:
            pass
        msg = f"Sir, {symbol} is currently trading at ${price:,.2f}{change}."
    except Exception as e:
        msg = f"Sir, I couldn't get the price for {symbol}: {e}"
    _log(msg, player)
    return msg


def _trade(symbol_raw: str, side: str, amount, player=None) -> str:
    """Place a buy/sell through the freqtrade REST API (dry-run or live)."""
    symbol = _normalize_symbol(symbol_raw)
    try:
        payload = {
            "pair": symbol.replace("USDT", "/USDT"),
            "side": side,
            "ordertype": "market",
        }
        if amount:
            payload["stakeamount"] = float(amount)
        result = _freqtrade_post("/forceenter" if side == "buy" else "/forceexit", payload)
        msg = f"Sir, {side} order sent to the trading bot for {symbol}. Response: {result}"
    except Exception as e:
        msg = (
            f"Sir, I couldn't place the {side} order for {symbol}. "
            f"The freqtrade bot API is not reachable ({e}). "
            f"Please make sure the bot is running with its API enabled."
        )
    _log(msg, player)
    return msg


def _log(message: str, player=None) -> None:
    print(f"[TradingBot] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "trading_bot",
    "description": (
        "Crypto trading assistant: get live crypto prices (BTC, ETH, SOL, etc.) "
        "and place buy/sell trades through the freqtrade trading bot. "
        "Use mode='price' to check price, mode='buy' or mode='sell' to trade."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "description": "price, buy, or sell",
                "enum": ["price", "buy", "sell"],
            },
            "symbol": {
                "type": "STRING",
                "description": "Crypto symbol, e.g. BTC, ETH, SOL, bitcoin",
            },
            "amount": {
                "type": "NUMBER",
                "description": "Stake amount in USDT for buy/sell (optional)",
            },
        },
        "required": ["mode"],
    },
    "handler": trading_action,
}
