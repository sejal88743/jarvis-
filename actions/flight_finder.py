#flight_finder.py
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import is_windows, is_mac, is_linux

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

_MONTH_MAP: dict[str, int] = {

    "january": 1, "february": 2, "march": 3,     "april": 4,
    "may": 5,     "june": 6,     "july": 7,       "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# English fast-path only — Gemini (below) normalizes date expressions in ANY
# language to YYYY-MM-DD, so no other language needs to be hardcoded here.
_RELATIVE_MAP_KEYS = {
    "today",
    "tomorrow",
}


def _parse_date(raw: str) -> str:

    raw   = raw.strip()
    lower = raw.lower()
    today = datetime.now()

    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    relative = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
    }
    for key, val in relative.items():
        if key in lower:
            return val.strftime("%Y-%m-%d")

    try:
        from core import gemini
        result = gemini.text(
            f"Today is {today.strftime('%Y-%m-%d')}. "
            f"Convert this date expression to YYYY-MM-DD: '{raw}'. "
            f"Return ONLY the date string, nothing else.",
            tier=gemini.FAST,
        )
        if re.match(r"\d{4}-\d{2}-\d{2}", result):
            return result
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Gemini date parse failed: {e}")

    for month_name, month_num in _MONTH_MAP.items():
        if month_name in lower:
            day_match = re.search(r"\d{1,2}", raw)
            if day_match:
                day  = int(day_match.group())
                year = today.year if month_num >= today.month else today.year + 1
                return f"{year}-{month_num:02d}-{day:02d}"

    # Last resort: today
    print(f"[FlightFinder] ⚠️ Could not parse date '{raw}' — using today.")
    return today.strftime("%Y-%m-%d")

_CABIN_CODE: dict[str, str] = {
    "economy":  "1",
    "premium":  "2",
    "business": "3",
    "first":    "4",
}


# The tfs blob is base64-encoded protobuf of the FULL search state (route,
# dates). A stale hardcoded one (2025-03-15 IST→LHR) used to OVERRIDE the q=
# query, so every search landed on Istanbul→London March. It is now derived
# per search — actually, q= alone is enough: Google parses the natural-language
# query into the real form. No tfs at all.

def _build_google_flights_url(
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None = None,
    passengers:  int        = 1,
    cabin:       str        = "economy",
) -> str:
    cabin_code = _CABIN_CODE.get(cabin.lower(), "1")
    base       = "https://www.google.com/travel/flights"

    origin      = origin.strip().replace(" ", "+")
    destination = destination.strip().replace(" ", "+")

    # Google Flights parses this natural-language q= into the actual form:
    # origin, destination and dates all come from the words themselves.
    if return_date:
        trip = (f"Flights from {origin} to {destination} "
                f"on {date} returning {return_date}")
    else:
        trip = f"Flights from {origin} to {destination} on {date}"

    return (
        f"{base}"
        f"?q={trip}"
        f"&curr=INR"
        f"&hl=en"
        f"&cabin={cabin_code}"
        f"&adults={passengers}"
    )



def _search_flights_browser(
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None,
    passengers:  int,
    cabin:       str,
) -> tuple[str, str]:
    import time
    from actions.browser_control import browser_control

    url = _build_google_flights_url(
        origin, destination, date, return_date, passengers, cabin
    )

    print(f"[FlightFinder] 🌐 Opening: {url}")
    browser_control({"action": "go_to", "url": url})
    time.sleep(5)

    raw = browser_control({"action": "get_text"})
    return (raw or ""), url


def _cheapest_across_months(
    origin:      str,
    destination: str,
    year:        int,
    months:      list[int],
    passengers:  int,
    cabin:       str,
) -> tuple[list[dict], str]:
    """Ask Google Flights for the CHEAPEST month directly.

    The user asked for 'the cheapest ticket in any month of 2026' — that is a
    calendar-range question, not a date question. Google Flights answers it
    natively: 'cheapest flights from X to Y in MONTH YEAR' opens the price-
    calendar view, and its text includes the cheapest price per month. No
    page-driving, no month clicks — the words do it.
    """
    import time
    from actions.browser_control import browser_control

    span = (min(months), max(months))
    if span[0] == span[1]:
        month_word = datetime(year, span[0], 1).strftime("%B %Y")
        query = (f"cheapest flights from {origin} to {destination} "
                 f"in {month_word}")
    else:
        first = datetime(year, span[0], 1).strftime("%B %Y")
        last  = datetime(year, span[1], 1).strftime("%B %Y")
        query = (f"cheapest flights from {origin} to {destination} "
                 f"between {first} and {last}")

    url = ("https://www.google.com/travel/flights"
           f"?q={query.replace(' ', '+')}&curr=INR&hl=en"
           f"&cabin={_CABIN_CODE.get(cabin, '1')}&adults={passengers}")

    print(f"[FlightFinder] 🌐 Cheapest-month search: {url}")
    browser_control({"action": "go_to", "url": url})
    time.sleep(6)

    raw = browser_control({"action": "get_text"})
    return (raw or ""), url


def _parse_cheapest_month(raw_text: str, origin: str, destination: str,
                          year: int) -> list[dict]:
    """Pull the cheapest price per month out of the calendar page text."""
    prompt = (
        f"This is Google Flights price-calendar text for {origin} to {destination} "
        f"in {year}. Extract the cheapest price for each month shown.\n\n"
        f"{raw_text[:12000]}\n\n"
        f'Return ONLY a JSON array: [{{"month":"March 2026","price":"12345",'
        f'"currency":"INR"}}] — months in calendar order, cheapest first. '
        f"If nothing readable, return []."
    )
    try:
        from core import gemini
        response = gemini.call(prompt, tier=gemini.SMART, timeout_ms=30_000)
        if response is None:
            return []
        text = re.sub(r"```(?:json)?", "", response.text).strip().rstrip("`").strip()
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[FlightFinder] ⚠️ cheapest-month parse failed: {e}")
        return []

def _parse_flights_with_gemini(
    raw_text:    str,
    origin:      str,
    destination: str,
    date:        str,
) -> list[dict]:
    from google import genai as _genai
    from google.genai import types

    prompt  = (
        f"Extract flight options from {origin} to {destination} on {date} "
        f"from this Google Flights page text:\n\n{raw_text[:12000]}\n\n"
        f"Return a JSON array of up to 5 flights:\n"
        f'[{{"airline":"...","departure":"HH:MM","arrival":"HH:MM",'
        f'"duration":"Xh Ym","stops":0,"price":"...","currency":"USD"}}]\n'
        f"If no flights found, return: []"
    )

    try:
        from core import gemini
        response = gemini.call(
            prompt,
            tier=gemini.SMART,
            timeout_ms=30_000,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a flight data extraction expert. "
                    "Extract flight information from raw webpage text. "
                    "Return ONLY valid JSON — no markdown, no explanation."
                )
            ),
        )
        text     = re.sub(r"```(?:json)?", "", response.text).strip().rstrip("`").strip()
        flights  = json.loads(text)
        return flights if isinstance(flights, list) else []
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Gemini parse failed: {e}")
        return []

def _format_spoken(
    flights:     list[dict],
    origin:      str,
    destination: str,
    date:        str,
) -> str:
    if not flights:
        return (
            f"I couldn't find any flights from {origin} to {destination} "
            f"on {date}, sir. The page may not have loaded correctly."
        )

    lines = [f"Here are the top flights from {origin} to {destination} on {date}, sir."]

    for i, f in enumerate(flights[:5], 1):
        airline   = f.get("airline",   "Unknown airline")
        departure = f.get("departure", "--:--")
        arrival   = f.get("arrival",   "--:--")
        duration  = f.get("duration",  "")
        stops     = f.get("stops",     0)
        price     = f.get("price",     "")
        currency  = f.get("currency",  "")

        stop_str  = "non-stop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
        price_str = f"{price} {currency}".strip() if price else "price unavailable"
        dur_str   = f", {duration}" if duration else ""

        lines.append(
            f"Option {i}: {airline}, departing {departure}, "
            f"arriving {arrival}{dur_str}, {stop_str}, {price_str}."
        )

    # Cheapest — strip non-digits for comparison
    priced = [f for f in flights if f.get("price")]
    if priced:
        cheapest = min(
            priced,
            key=lambda x: int(re.sub(r"[^\d]", "", str(x["price"])) or "999999"),
        )
        lines.append(
            f"The cheapest option is {cheapest.get('airline')} "
            f"at {cheapest.get('price')} {cheapest.get('currency', '')}."
        )

    return " ".join(lines)


def _format_text_report(
    flights:     list[dict],
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None,
    page_url:    str,
) -> str:
    lines = [
        "JARVIS — Flight Search Results",
        "─" * 50,
        f"Route     : {origin} → {destination}",
        f"Date      : {date}",
    ]
    if return_date:
        lines.append(f"Return    : {return_date}")
    lines += [
        f"Searched  : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Source    : {page_url}",
        "─" * 50,
        "",
    ]

    if not flights:
        lines.append("No flights found.")
    else:
        for i, f in enumerate(flights, 1):
            stops    = f.get("stops", 0)
            stop_str = "Non-stop" if stops == 0 else f"{stops} stop(s)"
            lines += [
                f"Flight {i}:",
                f"  Airline   : {f.get('airline',   'N/A')}",
                f"  Departure : {f.get('departure', 'N/A')}",
                f"  Arrival   : {f.get('arrival',   'N/A')}",
                f"  Duration  : {f.get('duration',  'N/A')}",
                f"  Stops     : {stop_str}",
                f"  Price     : {f.get('price', 'N/A')} {f.get('currency', '')}",
                "",
            ]

    return "\n".join(lines)

def _save_to_desktop(content: str, origin: str, destination: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"flights_{origin}_{destination}_{ts}.txt".replace(" ", "_")
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / filename

    filepath.write_text(content, encoding="utf-8")
    print(f"[FlightFinder] 💾 Saved: {filepath}")

    try:
        if is_windows():
            subprocess.Popen(["notepad.exe", str(filepath)])
        elif is_mac():
            subprocess.Popen(["open", "-t", str(filepath)])
        else:
            subprocess.Popen(["xdg-open", str(filepath)])
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Could not open text editor: {e}")

    return str(filepath)


def flight_finder(parameters: dict, player=None, speak=None) -> str:
    params = parameters or {}

    origin      = params.get("origin",      "").strip()
    destination = params.get("destination", "").strip()
    date_raw    = params.get("date",        "").strip()
    return_raw  = (params.get("return_date") or "").strip()
    passengers  = max(1, int(params.get("passengers", 1)))
    cabin       = params.get("cabin", "economy").strip().lower()
    save        = bool(params.get("save", False))

    # 'any month' / cheapest-across-a-year mode: date_raw can be empty when the
    # user asked for the lowest fare over a whole year or month range.
    find_cheapest = bool(params.get("find_cheapest")) or \
        (not date_raw and bool(params.get("cheapest_year")))

    if not origin or not destination:
        return "Please provide both origin and destination, sir."
    if not date_raw and not find_cheapest:
        return "Please provide a departure date, sir."

    # Normalise cabin value
    if cabin not in _CABIN_CODE:
        cabin = "economy"

    if player:
        player.write_log(f"[FlightFinder] {origin} → {destination}")

    try:
        if find_cheapest:
            year      = int(params.get("cheapest_year") or datetime.now().year + 1)
            months    = params.get("cheapest_months") or list(range(1, 13))
            if isinstance(months, list):
                months = [int(m) for m in months if 1 <= int(m) <= 12]
            else:
                months = list(range(1, 13))

            if speak:
                speak(f"Searching the cheapest flights from {origin} to "
                      f"{destination} across {year}, sir.")

            raw_text, page_url = _cheapest_across_months(
                origin, destination, year, months, passengers, cabin
            )
            if not raw_text:
                return "Could not retrieve the price calendar, sir. The page may not have loaded."

            months_found = _parse_cheapest_month(raw_text, origin, destination, year)
            if not months_found:
                return (f"I opened the Google Flights price calendar for {origin} "
                        f"to {destination} in {year}, sir, but could not read "
                        f"month prices from it. The page is open on screen — "
                        f"you can see it there.")

            def _rupees(x):
                try:
                    return int(re.sub(r"[^\d]", "", str(x.get("price", ""))) or 999999)
                except Exception:
                    return 999999

            best = min(months_found, key=_rupees)
            lines = [f"Cheapest months from {origin} to {destination} in {year}, sir:"]
            for m in months_found[:6]:
                lines.append(f"{m.get('month')}: {m.get('price')} {m.get('currency', '')}")
            lines.append(f"The lowest fare of the year is {best.get('month')} at "
                         f"{best.get('price')} {best.get('currency', '')}.")
            lines.append("The price calendar is open in the browser — say the "
                         "month and I will open its flights.")
            if save:
                report = (f"JARVIS — Cheapest months, {origin} → {destination}, {year}\n"
                          f"Source: {page_url}\n\n" +
                          "\n".join(f"{m.get('month')}: {m.get('price')} {m.get('currency', '')}"
                                     for m in months_found))
                result += f" Results saved to Desktop: {_save_to_desktop(report, origin, destination)}"
            spoken = " ".join(lines)
            if speak:
                speak(spoken)
            return spoken

        date        = _parse_date(date_raw)
        return_date = _parse_date(return_raw) if return_raw else None

        if player:
            player.write_log(f"[FlightFinder] {origin} → {destination} on {date}")
        if speak:
            speak(f"Searching flights from {origin} to {destination} on {date}, sir.")

        print(
            f"[FlightFinder] ▶️ {origin} → {destination} | {date}"
            f"{' → ' + return_date if return_date else ''}"
            f" | {cabin} | {passengers} pax"
        )

        raw_text, page_url = _search_flights_browser(
            origin, destination, date, return_date, passengers, cabin
        )

        if not raw_text:
            return "Could not retrieve flight data, sir. The page may not have loaded."

        if speak:
            speak("Analysing the results now, sir.")

        flights = _parse_flights_with_gemini(raw_text, origin, destination, date)
        spoken  = _format_spoken(flights, origin, destination, date)

        if speak:
            speak(spoken)

        result = spoken

        if save and flights:
            report     = _format_text_report(flights, origin, destination, date, return_date, page_url)
            saved_path = _save_to_desktop(report, origin, destination)
            result    += f" Results saved to Desktop: {saved_path}"

        return result

    except Exception as e:
        print(f"[FlightFinder] ❌ {e}")
        return f"Flight search failed, sir: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "flight_finder",
    "description": (
        "Searches Google Flights and speaks the best options. "
        "For 'cheapest ticket in any month of YEAR' or 'lowest fare in 2026' "
        "style requests, set find_cheapest=true and cheapest_year — the date "
        "field is then optional. The model must resolve city names, month and "
        "year ITSELF from the user's words before calling — never leave the "
        "page to pick them."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "origin": {
                "type": "STRING",
                "description": "Departure city or airport code"
            },
            "destination": {
                "type": "STRING",
                "description": "Arrival city or airport code"
            },
            "date": {
                "type": "STRING",
                "description": "Departure date (any format); optional in cheapest mode"
            },
            "find_cheapest": {
                "type": "BOOLEAN",
                "description": "true when the user wants the LOWEST fare across months/any date, not a specific date"
            },
            "cheapest_year": {
                "type": "INTEGER",
                "description": "Year to scan for the cheapest month, e.g. 2026"
            },
            "cheapest_months": {
                "type": "ARRAY",
                "description": "Optional month numbers (1-12) to restrict the cheapest-month scan",
                "items": {"type": "INTEGER"}
            },
            "return_date": {
                "type": "STRING",
                "description": "Return date for round trips"
            },
            "passengers": {
                "type": "INTEGER",
                "description": "Number of passengers (default: 1)"
            },
            "cabin": {
                "type": "STRING",
                "description": "economy | premium | business | first"
            },
            "save": {
                "type": "BOOLEAN",
                "description": "Save results to Notepad"
            }
        },
        "required": [
            "origin",
            "destination"
        ]
    },
    "handler": flight_finder,
}
