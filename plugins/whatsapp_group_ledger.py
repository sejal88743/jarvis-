import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

PLUGIN = {
    "name": "whatsapp_group_ledger",
    "description": (
        "Save payment details read from WhatsApp directly to the configured XLSX ledger, or process group screenshots from a folder. "
        "Records include date, group, sender, bill number, and amount; duplicate records are skipped."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "group_name": {"type": "STRING", "description": "Name of the WhatsApp group or source chat."},
            "source_folder": {"type": "STRING", "description": "Folder containing the images to process."},
            "output_xlsx": {"type": "STRING", "description": "Where to save the ledger workbook (.xlsx)."},
            "sheet_name": {"type": "STRING", "description": "Worksheet name inside the Excel file."},
            "record_text": {"type": "STRING", "description": "Payment details read from the current WhatsApp conversation, including sender, bill number, and amount."},
            "sender_name": {"type": "STRING", "description": "Payment sender name for direct ledger entry."},
            "bill_no": {"type": "STRING", "description": "Bill or reference number for direct ledger entry."},
            "payment_amount": {"type": "NUMBER", "description": "Payment amount in rupees for direct ledger entry."},
            "date": {"type": "STRING", "description": "Payment date in YYYY-MM-DD format; defaults to today."},
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "whatsapp_group_ledger",
    "title": "WhatsApp Group Ledger",
    "fields": [
        {"key": "group_name", "label": "Group Name", "type": "text", "placeholder": "Family Payments / Office Group"},
        {"key": "source_folder", "label": "Images Folder", "type": "text", "placeholder": "C:/Users/.../WhatsApp Images"},
        {"key": "output_xlsx", "label": "Output XLSX", "type": "text", "placeholder": "C:/Users/.../whatsapp_ledger.xlsx"},
        {"key": "sheet_name", "label": "Sheet Name", "type": "text", "placeholder": "Ledger"},
    ],
    "action": {
        "label": "PROCESS GROUP IMAGES",
        "run": None,
    },
}


DEFAULT_HEADERS = [
    "date",
    "group_name",
    "sender_name",
    "bill_no",
    "payment_amount",
    "source_image",
    "image_hash",
    "processed_at",
]


def _expand_path(raw: str | None) -> Path:
    if not raw or not str(raw).strip():
        return Path.cwd()
    return Path(str(raw).strip()).expanduser()


def _default_output_path() -> Path:
    documents = Path.home() / "Documents"
    return documents / "WhatsApp_Payment_Ledger.xlsx"


def _image_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_money(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace("₹", "").replace("RS", "").replace("rs", "").replace(",", "")
    text = text.replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_ledger_record(ocr_text: str) -> dict:
    text = (ocr_text or "").replace("\r", "\n")
    lower = text.lower()

    payment_match = None
    for pattern in [
        r"(?:rs|₹)\s*[:#-]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"payment\s*[:#-]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"amount\s*[:#-]?\s*([0-9]+(?:\.[0-9]+)?)",
    ]:
        m = re.search(pattern, lower, flags=re.IGNORECASE)
        if m:
            payment_match = m.group(1)
            break
    payment_amount = _normalize_money(payment_match) if payment_match else None

    bill_no = None
    for pattern in [
        r"(?:bill\s*(?:no|num|number)|invoice\s*(?:no|num|number)|attachment\s*(?:no|num|number)|attech\s*(?:bill\s*)?(?:no|num|number))\s*[:#-]?\s*([a-z0-9/-]+)",
        r"(?:bill\s*[:#-]?\s*)([a-z0-9/-]+)",
        r"(?:ref\s*(?:no|#)\s*[:#-]?\s*)([a-z0-9/-]+)",
    ]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            bill_no = m.group(1).strip()
            break

    sender_name = None
    for pattern in [
        r"(?:sender\s*name|sender|from|by)\s*[:#-]?\s*([A-Za-z][A-Za-z .'-]+)",
        r"name\s*[:#-]?\s*([A-Za-z][A-Za-z .'-]+)",
    ]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() not in {"name", "sender", "from", "by"}:
                sender_name = candidate
                break

    # Explicit 'Sender Name: X' strings are common in WhatsApp payment screenshots.
    m = re.search(r"sender\s*name\s*[:#-]?\s*([A-Za-z][A-Za-z .'-]+)", text, flags=re.IGNORECASE)
    if m:
        sender_name = m.group(1).strip()

    # If the OCR mishandles case, clean up badly captured strings.
    if sender_name:
        sender_name = re.sub(r"\s+", " ", sender_name).strip()
    if bill_no:
        bill_no = bill_no.strip()

    return {
        "payment_amount": payment_amount,
        "bill_no": bill_no,
        "sender_name": sender_name,
    }


def _existing_hashes(path: Path) -> set[str]:
    if not path or not path.exists():
        return set()
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return set()
    sheets = wb.sheetnames or ["Sheet"]
    seen = set()
    for sheet_name in sheets:
        ws = wb[sheet_name]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row:
                continue
            hash_value = None
            for idx in (6, 7):
                if len(row) > idx and isinstance(row[idx], str):
                    hash_value = row[idx]
                    break
            if hash_value is None and len(row) > 6:
                hash_value = row[6]
            if isinstance(hash_value, str) and hash_value.strip():
                seen.add(hash_value.strip())
    wb.close()
    return seen


def _is_duplicate(path: Path, hash_value: str) -> bool:
    if not path or not path.exists():
        return False
    return hash_value in _existing_hashes(path)


def _record_fingerprint(group_name: str, sender_name: str, bill_no: str,
                        payment_amount: float | None, record_text: str = "") -> str:
    if bill_no and bill_no != "N/A":
        identity = "|".join((group_name, bill_no, str(payment_amount or "")))
    else:
        identity = "|".join((group_name, sender_name, str(payment_amount or ""), record_text))
    normalized = " ".join(identity.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _append_direct_record(values: dict, group_name: str, output_path: Path,
                          sheet_name: str) -> str:
    record_text = str(values.get("record_text") or "").strip()
    extracted = _extract_ledger_record(record_text)
    sender_name = str(values.get("sender_name") or extracted.get("sender_name") or "").strip()
    bill_no = str(values.get("bill_no") or extracted.get("bill_no") or "").strip()
    amount = _normalize_money(str(values.get("payment_amount"))) if values.get("payment_amount") is not None else extracted.get("payment_amount")

    if amount is None and not bill_no and not sender_name:
        return "No payment details found. Include a sender, bill number, or payment amount before saving."

    record_hash = _record_fingerprint(group_name, sender_name, bill_no, amount, record_text)
    if _is_duplicate(output_path, record_hash):
        return f"Duplicate payment record skipped; it is already in {output_path}."

    raw_date = str(values.get("date") or "").strip()
    try:
        date_value = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d") if raw_date else datetime.now().strftime("%Y-%m-%d")
    except ValueError:
        return "Date must use YYYY-MM-DD format."

    row = {
        "date": date_value,
        "group_name": group_name,
        "sender_name": sender_name or "Unknown sender",
        "bill_no": bill_no or "N/A",
        "payment_amount": amount if amount is not None else "",
        "source_image": "WhatsApp conversation",
        "image_hash": record_hash,
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _append_excel_rows(output_path, [row], sheet_name=sheet_name)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise OSError(f"Excel file was not created: {output_path}")
    return f"Saved WhatsApp payment record to {output_path}."


def _write_ledger_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(DEFAULT_HEADERS)
    for row in rows:
        row_data = {
            "date": row.get("date"),
            "group_name": row.get("group_name"),
            "sender_name": row.get("sender_name"),
            "bill_no": row.get("bill_no"),
            "payment_amount": row.get("payment_amount"),
            "source_image": row.get("source_image"),
            "image_hash": row.get("image_hash") or row.get("hash"),
            "processed_at": row.get("processed_at"),
        }
        ws.append([
            row_data["date"],
            row_data["group_name"],
            row_data["sender_name"],
            row_data["bill_no"],
            row_data["payment_amount"],
            row_data["source_image"],
            row_data["image_hash"],
            row_data["processed_at"],
        ])
    wb.save(path)
    wb.close()


def _append_excel_rows(path: Path, rows: list[dict], sheet_name: str = "Ledger") -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name[:31] or "Ledger"
        ws.append(DEFAULT_HEADERS)
    else:
        wb = load_workbook(path)
        target_name = sheet_name[:31] or "Ledger"
        if target_name in wb.sheetnames:
            ws = wb[target_name]
        else:
            ws = wb.create_sheet(target_name)
            for column, header in enumerate(DEFAULT_HEADERS, start=1):
                ws.cell(1, column, header)
    for row in rows:
        row_data = {
            "date": row.get("date"),
            "group_name": row.get("group_name"),
            "sender_name": row.get("sender_name"),
            "bill_no": row.get("bill_no"),
            "payment_amount": row.get("payment_amount"),
            "source_image": row.get("source_image"),
            "image_hash": row.get("image_hash") or row.get("hash"),
            "processed_at": row.get("processed_at"),
        }
        ws.append([
            row_data["date"],
            row_data["group_name"],
            row_data["sender_name"],
            row_data["bill_no"],
            row_data["payment_amount"],
            row_data["source_image"],
            row_data["image_hash"],
            row_data["processed_at"],
        ])
    wb.save(path)
    wb.close()


def _ocr_image_to_text(image_path: Path) -> str:
    try:
        from PIL import Image
        from core import gemini

        img = Image.open(image_path)
        response = gemini.call(["Extract all readable payment, bill number, and sender details from this image. Return plain text only.", img], tier=gemini.SMART, timeout_ms=90000)
        if response is None:
            return ""
        text = getattr(response, "text", str(response))
        return str(text or "")
    except Exception:
        return ""


def _process_group_images(values: dict, player=None, session_memory=None) -> str:
    group_name = str(values.get("group_name") or "").strip()
    source_folder = str(values.get("source_folder") or "").strip()
    output_xlsx = str(values.get("output_xlsx") or "").strip()
    sheet_name = str(values.get("sheet_name") or "Ledger").strip() or "Ledger"

    if not group_name:
        return "Group name is required before processing images."

    out_path = _expand_path(output_xlsx) if output_xlsx else None
    if out_path is not None:
        out_path = out_path.with_suffix(".xlsx")
    if values.get("record_text") or values.get("sender_name") or values.get("bill_no") or values.get("payment_amount") is not None:
        if out_path is None:
            out_path = _default_output_path()
        return _append_direct_record(values, group_name, out_path, sheet_name)
    if not source_folder:
        return "Source folder is required for image processing. For details already read in WhatsApp, pass record_text or the sender, bill number, and amount."

    src = _expand_path(source_folder)
    if not src.exists() or not src.is_dir():
        return f"Folder not found: {src}"

    out_path = out_path or (src / "whatsapp_group_ledger.xlsx")

    image_files = sorted(
        [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}],
        key=lambda p: p.stat().st_mtime,
    )

    if not image_files:
        return f"No image files found in {src}."

    existing_hashes = _existing_hashes(out_path)
    new_rows: list[dict] = []
    processed_count = 0
    skipped_duplicates = 0
    skipped_empty = 0

    for image_path in image_files:
        file_hash = _image_hash(image_path)
        if file_hash in existing_hashes:
            skipped_duplicates += 1
            continue

        text = _ocr_image_to_text(image_path)
        record = _extract_ledger_record(text)
        if record.get("payment_amount") is None and not record.get("bill_no") and not record.get("sender_name"):
            skipped_empty += 1
            continue

        payment_amount = record.get("payment_amount")
        sender_name = record.get("sender_name") or "Unknown sender"
        bill_no = record.get("bill_no") or "N/A"
        dt = datetime.fromtimestamp(image_path.stat().st_mtime).strftime("%Y-%m-%d")
        row = {
            "date": dt,
            "group_name": group_name,
            "sender_name": sender_name,
            "bill_no": bill_no,
            "payment_amount": payment_amount if payment_amount is not None else "",
            "source_image": str(image_path.name),
            "image_hash": file_hash,
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        new_rows.append(row)
        processed_count += 1

    if not new_rows:
        return (
            f"No new valid payment records were added. {skipped_duplicates} duplicate image(s) were skipped, "
            f"{skipped_empty} unreadable image(s) were ignored."
        )

    if out_path.exists():
        _append_excel_rows(out_path, new_rows, sheet_name=sheet_name)
    else:
        _append_excel_rows(out_path, new_rows, sheet_name=sheet_name)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise OSError(f"Excel file was not created: {out_path}")

    return (
        f"Processed {processed_count} new image(s) into {out_path}. "
        f"Skipped {skipped_duplicates} duplicate image(s) and ignored {skipped_empty} unreadable image(s)."
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Save current WhatsApp payment details or process group images into XLSX."""
    from memory.config_manager import get_plugin_config

    values = get_plugin_config("whatsapp_group_ledger")
    values.update(parameters or {})
    if not values.get("output_xlsx"):
        values["output_xlsx"] = str(_default_output_path())
    return _process_group_images(values, player=player, session_memory=session_memory)


PLUGIN_SETTINGS["action"]["run"] = _process_group_images
