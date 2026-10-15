"""
HUL Bill Payment — voice-driven payment entry for the HUL Driver Hub app,
plus WhatsApp "Online Pay Confinace" group scanning.

Two actions:

1. enter_payment  — Opens the HUL Driver Hub app on this desktop, READS the
   bill number straight off the screen (no typing the bill number — the user
   asked for it to be read, not dictated), then fills the payment mode
   (GPay / Cash / Cheque), the amount and the details, and presses save.
   A screenshot before and after lets the model verify what it did.

2. scan_whatsapp  — Opens WhatsApp, finds the configured group (default
   "Online Pay Confinace"), and reads the recent messages — text AND the
   payment screenshots people post — into the ledger workbook. New payment
   records are appended; duplicates are skipped by fingerprint.

The screen reading everywhere is Gemini vision over screenshots; field
positions are located per-screenshot, so the app can be any size or zoom.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from openpyxl import Workbook

from actions.computer_control import (
    _click, _focus_window, _hotkey, _press, _screen_find, _smart_type,
    _screenshot,
)
from actions.screen_processor import _capture_screen
from core import gemini

PLUGIN = {
    "name": "hul_bill_payment",
    "description": (
        "HUL Driver Hub bill-payment entry and WhatsApp payment-group scanning. "
        "Use 'enter_payment' when the user asks to enter/post a bill payment in "
        "the HUL Driver app: it opens the app, reads the bill number from the "
        "screen itself, fills the payment mode (GPay/Cash/Cheque), amount and "
        "details, and saves. payment_mode, amount and details come from the "
        "user's words; the bill number is NOT asked — it is read from screen. "
        "Use 'scan_whatsapp' when the user asks to read/scan payment messages "
        "or payment screenshots from the WhatsApp group and record them. "
        "Do NOT use computer_control or browser_control for these — this plugin "
        "drives both apps itself."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "enter_payment | scan_whatsapp",
            },
            "payment_mode": {
                "type": "STRING",
                "description": "Payment mode spoken by the user: GPay, Cash, Cheque (or Cheque/UPI).",
            },
            "amount": {
                "type": "NUMBER",
                "description": "Payment amount in rupees, as the user said it.",
            },
            "details": {
                "type": "STRING",
                "description": "Extra details/note to put in the details field (cheque number, party name, remark).",
            },
            "group_name": {
                "type": "STRING",
                "description": "WhatsApp group to scan; defaults to the configured 'Online Pay Confinace'.",
            },
            "pages": {
                "type": "INTEGER",
                "description": "How many screens deep to scroll in the group (default 3).",
            },
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "hul_bill_payment",
    "title": "HUL Bill Payment",
    "fields": [
        {"key": "app_aumid", "label": "App AUMID", "type": "text",
         "placeholder": "no1huldriver.lovable.app-99146FCE_32eend4wnjgka!App"},
        {"key": "group_name", "label": "WhatsApp Group", "type": "text",
         "placeholder": "Online Pay Confinace"},
        {"key": "output_xlsx", "label": "Ledger XLSX", "type": "text",
         "placeholder": "C:/Users/.../Documents/HUL_Payment_Ledger.xlsx"},
    ],
    "action": {"label": "", "run": None},
}

# The installed PWA's Application User Model ID, discovered on this machine.
_DEFAULT_AUMID = "no1huldriver.lovable.app-99146FCE_32eend4wnjgka!App"
_DEFAULT_GROUP = "Online Pay Confinace"

_HEADERS = ["date", "bill_no", "payment_mode", "payment_amount", "details",
            "source", "fingerprint", "recorded_at"]

SHOT = Path(__file__).resolve().parent.parent / "data" / "hul_bill_payment"


def _cfg() -> dict:
    from memory.config_manager import get_plugin_config
    return get_plugin_config("hul_bill_payment")


def _ledger_path() -> Path:
    raw = str(_cfg().get("output_xlsx") or "").strip()
    if raw:
        return Path(raw).expanduser().with_suffix(".xlsx")
    return Path.home() / "Documents" / "HUL_Payment_Ledger.xlsx"


# ── Vision: ask Gemini to locate the form on the current screen ──────────────

_LOCATE_PROMPT = """You are looking at a screenshot of a billing app (HUL Driver Hub).
Find the payment-entry form. Report, as STRICT JSON only (no markdown, no prose):

{
 "bill_no_visible": "<the bill number shown on screen, exactly as displayed; null if none>",
 "bill_field": {"x": <int>, "y": <int>} or null,   // click target of the bill number input
 "mode_field": {"x": <int>, "y": <int>} or null,   // where payment mode (GPay/Cash/Cheque) is chosen
 "amount_field": {"x": <int>, "y": <int>} or null, // amount input
 "details_field": {"x": <int>, "y": <int>} or null,// details/note input
 "save_button": {"x": <int>, "y": <int>} or null,  // save/submit button
 "screen_summary": "<one line: what this screen shows>"
}

Coordinates are screen pixels of where a person would click. null for anything absent."""


def _vision_json(image_bytes: bytes, mime: str, prompt: str) -> dict:
    try:
        import google.genai.types as gtypes
        resp = gemini.call(
            [gtypes.Part.from_bytes(data=image_bytes, mime_type=mime), prompt],
            tier=gemini.SMART, timeout_ms=90_000,
        )
        if resp is None:
            return {}
        text = str(getattr(resp, "text", "") or "")
        text = text.strip().strip("`")
        if text.startswith("json"):
            text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        return json.loads(text[start:end + 1])
    except Exception as e:
        print(f"[HULBill] vision failed: {e}")
        return {}


def _open_hul_app() -> bool:
    aumid = str(_cfg().get("app_aumid") or _DEFAULT_AUMID).strip() or _DEFAULT_AUMID
    try:
        subprocess.run(
            ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
            check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(6.0)
        return True
    except Exception as e:
        print(f"[HULBill] could not launch app: {e}")
        return False


def _norm_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if "upi" in m or "gpay" in m or "google" in m or "online" in m or "phone" in m:
        return "GPay"
    if "cash" in m:
        return "Cash"
    if "cheq" in m or "check" in m or "bank" in m:
        return "Cheque"
    return (mode or "").strip()


def _grab() -> tuple[bytes, str]:
    return _capture_screen()


def _click_field(form: dict, key: str) -> bool:
    pos = form.get(key)
    if not pos or not isinstance(pos, dict):
        return False
    x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
    if not x or not y:
        return False
    _click(x, y)
    time.sleep(0.4)
    return True


def enter_payment(parameters: dict) -> str:
    mode = _norm_mode(str(parameters.get("payment_mode") or ""))
    amount = parameters.get("amount")
    details = str(parameters.get("details") or "").strip()

    if not amount:
        return "Payment amount chahiye — batayein kitne rupaye ka payment enter karna hai."
    if not mode:
        mode = _norm_mode(str(parameters.get("mode") or "GPay"))

    SHOT.mkdir(parents=True, exist_ok=True)

    # 1. The app must be on screen. If it already is, don't relaunch it.
    img, mime = _grab()
    form = _vision_json(img, mime, _LOCATE_PROMPT)
    if not form.get("bill_field") and not form.get("amount_field"):
        if not _open_hul_app():
            return "HUL Driver Hub app open nahi ho payi. Ek baar app kholein, phir boliye."
        img, mime = _grab()
        form = _vision_json(img, mime, _LOCATE_PROMPT)

    bill_no = str(form.get("bill_no_visible") or "").strip()
    if not form.get("amount_field") and not form.get("bill_field"):
        return ("HUL Driver Hub mein payment form nahi dikh raha. "
                "Bill screen par le aayein — bill number khud padh lunga.")

    said = f"Bill number {bill_no} screen par mila. " if bill_no else ""

    # 2. Bill number: click the field and type what the screen says.
    if form.get("bill_field") and bill_no:
        _click_field(form, "bill_field")
        _smart_type(bill_no)
        time.sleep(0.3)

    # 3. Payment mode.
    if form.get("mode_field"):
        _click_field(form, "mode_field")
        time.sleep(0.5)
        # The mode picker may be a dropdown or tiles — try clicking the word.
        coords = _screen_find(f"the option '{mode}' on screen")
        if coords:
            _click(x=coords[0], y=coords[1])
        else:
            _smart_type(mode)
        time.sleep(0.4)

    # 4. Amount.
    if form.get("amount_field"):
        _click_field(form, "amount_field")
        _smart_type(str(amount).strip())
        time.sleep(0.3)

    # 5. Details.
    if details and form.get("details_field"):
        _click_field(form, "details_field")
        _smart_type(details)
        time.sleep(0.3)

    # 6. Verify what we typed before saving.
    img2, mime2 = _grab()
    check = _vision_json(img2, mime2, (
        "Screenshot of a payment form after filling. STRICT JSON only: "
        '{"bill_no": "<value in the bill field or null>", '
        '"amount": "<value in the amount field or null>", '
        '"mode": "<selected payment mode or null>", '
        '"looks_filled": true/false}'
    ))
    print(f"[HULBill] pre-save check: {check}")

    # 7. Save.
    if form.get("save_button"):
        _click_field(form, "save_button")
    else:
        coords = _screen_find("the Save or Submit button of the payment form")
        if coords:
            _click(x=coords[0], y=coords[1])
        else:
            _hotkey("ctrl", "s")
    time.sleep(2.0)

    img3, _ = _grab()
    verdict = _vision_json(img3, mime2, (
        "Screenshot after pressing Save on a payment form. STRICT JSON only: "
        '{"saved": true/false, "evidence": "<toast, new entry, cleared form, or error text>"}'
    ))
    saved = bool(verdict.get("saved"))

    _append_ledger({
        "date": time.strftime("%Y-%m-%d"),
        "bill_no": bill_no or str(check.get("bill_no") or "N/A"),
        "payment_mode": mode,
        "payment_amount": amount,
        "details": details,
        "source": "HUL Driver Hub",
        "fingerprint": _fingerprint(bill_no, str(amount), mode),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    if saved:
        return (f"{said}Payment mode {mode}, amount {amount} rupees"
                f"{' — ' + details if details else ''} — form fill karke save ho gaya. "
                f"Ledger mein bhi record kar diya.")
    return (f"{said}Form fill kar diya ({mode}, {amount} rupees"
            f"{' — ' + details if details else ''}) lekin save confirm nahi hua — "
            f"screen par ek baar dekh lein, phir bata dein dobara try karoon.")


# ── Ledger ───────────────────────────────────────────────────────────────────

def _fingerprint(bill_no: str, amount: str, mode: str) -> str:
    import hashlib
    return hashlib.sha256(
        "|".join((bill_no, amount, mode)).casefold().encode()
    ).hexdigest()[:16]


def _existing_fps(path: Path) -> set[str]:
    from openpyxl import load_workbook
    if not path.exists():
        return set()
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return set()
    seen = set()
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and len(row) > 6 and row[6]:
                    seen.add(str(row[6]).strip())
    finally:
        wb.close()
    return seen


def _append_ledger(row: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from openpyxl import load_workbook
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        if not headers:
            ws.append(_HEADERS)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Ledger"
        ws.append(_HEADERS)
    ws.append([row.get(h, "") for h in _HEADERS])
    wb.save(path)
    wb.close()


# ── WhatsApp group scanning ──────────────────────────────────────────────────

_EXTRACT_PROMPT = """Screenshot of a WhatsApp group chat about bill payments.
Read BOTH the text messages and the images people have posted (payment
screenshots, UPI receipts). List every payment you can see as STRICT JSON:

{"payments": [
  {"sender": "<who sent it>",
   "bill_no": "<bill number visible in text or inside an image, or null>",
   "amount": <number or null>,
   "mode": "GPay|Cash|Cheque or null",
   "message": "<the text of the message, one line>"}
]}

No payments visible → {"payments": []}. JSON only, nothing else."""


def _open_whatsapp_group(group: str) -> bool:
    from actions.send_message import _open_app, _search_in_app
    if not _open_app("WhatsApp"):
        return False
    time.sleep(1.0)
    _search_in_app(group)
    time.sleep(1.2)
    _press("enter")
    time.sleep(1.5)
    return True


def scan_whatsapp(parameters: dict) -> str:
    group = str(parameters.get("group_name") or _cfg().get("group_name") or _DEFAULT_GROUP).strip()
    pages = max(1, min(6, int(parameters.get("pages") or 3)))

    if not _open_whatsapp_group(group):
        return "WhatsApp open nahi ho paya. WhatsApp install hai na? Phir se boliye."

    ledger = _ledger_path()
    seen = _existing_fps(ledger)
    found: list[dict] = []
    duplicates = 0

    SHOT.mkdir(parents=True, exist_ok=True)

    for page in range(pages):
        img, mime = _grab()
        (SHOT / f"group_p{page}.png").write_bytes(img)
        data = _vision_json(img, mime, _EXTRACT_PROMPT)
        for p in (data.get("payments") or []):
            bill = str(p.get("bill_no") or "N/A").strip()
            amt = p.get("amount")
            mode = _norm_mode(str(p.get("mode") or ""))
            fp = _fingerprint(bill, str(amt or ""), mode)
            if fp in seen:
                duplicates += 1
                continue
            seen.add(fp)
            found.append({
                "date": time.strftime("%Y-%m-%d"),
                "bill_no": bill,
                "payment_mode": mode,
                "payment_amount": amt if amt is not None else "",
                "details": str(p.get("sender") or "") + (" — " + p["message"] if p.get("message") else ""),
                "source": f"WhatsApp: {group}",
                "fingerprint": fp,
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        if page < pages - 1:
            # Page down inside the chat to reach older messages.
            _press("pagedown")
            time.sleep(1.2)

    if not found:
        return (f"'{group}' group mein {duplicates} duplicate chhod kar "
                f"koi nayi payment entry nahi mili.")

    for row in found:
        _append_ledger(row)

    bills = ", ".join(sorted({str(r["bill_no"]) for r in found if r["bill_no"] != "N/A"})[:8])
    return (f"WhatsApp group '{group}' se {len(found)} nayi payment padhi aur "
            f"{ledger} mein save kar di. {duplicates} duplicate skip hue. "
            f"Bills: {bills or 'bill number nahi mila'}.")


# ── Entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "enter_payment").strip().lower()
    try:
        if action in ("scan_whatsapp", "whatsapp", "group"):
            result = scan_whatsapp(params)
        else:
            result = enter_payment(params)
    except Exception as e:
        print(f"[HULBill] ❌ {action}: {e}")
        result = f"HUL bill payment '{action}' failed: {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result


PLUGIN_SETTINGS["action"]["run"] = None
