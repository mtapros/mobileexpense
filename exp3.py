from __future__ import annotations

import asyncio
import base64
import csv
import json
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:
    raise RuntimeError("tkinter is required to run this application.") from exc

try:
    from PIL import Image, ImageTk, ImageOps
    HAS_PIL = True
except ImportError:
    Image = None
    ImageTk = None
    ImageOps = None
    HAS_PIL = False

APP_TITLE = "Receipt Extractor - Qwen 3.6"
DEFAULT_HOST = "192.168.34.82"
DEFAULT_PORT = 1234
DEFAULT_ENDPOINT = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1/chat/completions"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
CONFIG_PATH = Path.home() / ".receipt_extractor_qwen36.json"
MONEY_QUANT = Decimal("0.01")
UPLOAD_DIR = Path.home() / "receipt_api_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_FIELDS: list[dict[str, str]] = [
    {"name": "merchant_name", "description": "Store, restaurant, vendor, or merchant name."},
    {"name": "merchant_address", "description": "Merchant address if visible."},
    {"name": "receipt_date", "description": "Transaction date in YYYY-MM-DD format if visible."},
    {"name": "receipt_time", "description": "Transaction time if visible."},
    {"name": "subtotal", "description": "Subtotal before tax, tip, discounts, or fees."},
    {"name": "tax", "description": "Total sales tax or VAT charged."},
    {"name": "tip", "description": "Tip or gratuity amount, if present."},
    {"name": "total", "description": "Final total paid."},
    {"name": "payment_method", "description": "Payment method, card brand, or last four digits if visible."},
    {"name": "expense_category", "description": "Best-fit category such as Meals, Supplies, Travel, Fuel, or Office."},
    {"name": "items", "description": "Array of line items. Each item should include description, quantity, unit_price, line_subtotal, and any visible raw price data."},
    {"name": "confidence", "description": "Overall extraction confidence from 0.0 to 1.0."},
    {"name": "notes", "description": "Short notes about ambiguity, missing data, or uncertain fields."},
]

KEY_FIELDS = ["merchant_name", "receipt_date", "subtotal", "tax", "total", "expense_category"]
STANDARD_ITEM_COLUMNS = [
    ("description", "Description"),
    ("quantity", "Qty"),
    ("unit_price", "Unit Price"),
    ("line_subtotal", "Sales Price"),
    ("allocated_tax", "Allocated Tax"),
    ("line_total", "Line Total"),
]


def normalize_field_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(name or "").strip().lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def titleize_field(name: str) -> str:
    return str(name or "").replace("_", " ").strip().title()


def money_to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    text = str(value).strip()
    if not text:
        return None
    filtered = []
    for ch in text:
        if ch.isdigit() or ch in {".", "-"}:
            filtered.append(ch)
    numeric = "".join(filtered)
    if numeric in {"", ".", "-", "-."}:
        return None
    try:
        return Decimal(numeric).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def decimal_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP):.2f}"


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return pretty_json(value)
    return str(value)


def safe_quantity(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "1"
    return str(value).strip()


def allocate_tax(items: list[dict[str, Any]], total_tax: Decimal | None) -> list[dict[str, Any]]:
    normalized = [dict(item) for item in items]
    if not normalized:
        return normalized
    tax = total_tax or Decimal("0.00")
    sales_values: list[Decimal] = []
    for item in normalized:
        sales = money_to_decimal(item.get("line_subtotal"))
        if sales is None:
            unit_price = money_to_decimal(item.get("unit_price"))
            qty_raw = item.get("quantity")
            try:
                qty = Decimal(str(qty_raw)) if qty_raw not in {None, ""} else Decimal("1")
            except InvalidOperation:
                qty = Decimal("1")
            if unit_price is not None:
                sales = (unit_price * qty).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        sales_values.append(sales or Decimal("0.00"))
    subtotal = sum(sales_values, Decimal("0.00"))
    if subtotal <= Decimal("0.00") or tax <= Decimal("0.00"):
        for idx, item in enumerate(normalized):
            sales = sales_values[idx]
            item["line_subtotal"] = decimal_to_str(sales)
            item["allocated_tax"] = decimal_to_str(Decimal("0.00"))
            item["line_total"] = decimal_to_str(sales)
        return normalized
    allocated: list[Decimal] = []
    running = Decimal("0.00")
    for idx, sales in enumerate(sales_values):
        if idx == len(sales_values) - 1:
            share = (tax - running).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        else:
            share = ((sales / subtotal) * tax).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            running += share
        allocated.append(share)
    diff = tax - sum(allocated, Decimal("0.00"))
    if allocated and diff != Decimal("0.00"):
        allocated[-1] = (allocated[-1] + diff).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    for idx, item in enumerate(normalized):
        sales = sales_values[idx]
        item["line_subtotal"] = decimal_to_str(sales)
        item["allocated_tax"] = decimal_to_str(allocated[idx])
        item["line_total"] = decimal_to_str((sales + allocated[idx]).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))
        item["quantity"] = safe_quantity(item.get("quantity"))
    return normalized


def standardize_items(raw_items: Any, total_tax: Decimal | None) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_items, start=1):
        if isinstance(entry, dict):
            description = (
                entry.get("description")
                or entry.get("name")
                or entry.get("item")
                or entry.get("title")
                or f"Item {index}"
            )
            quantity = entry.get("quantity", 1)
            unit_price = entry.get("unit_price") or entry.get("price_each") or entry.get("price")
            line_subtotal = entry.get("line_subtotal") or entry.get("subtotal") or entry.get("amount") or entry.get("line_total") or entry.get("total")
            standardized = {
                "description": str(description).strip(),
                "quantity": safe_quantity(quantity),
                "unit_price": decimal_to_str(money_to_decimal(unit_price)),
                "line_subtotal": decimal_to_str(money_to_decimal(line_subtotal)),
                "allocated_tax": "",
                "line_total": "",
                "raw": entry,
            }
            items.append(standardized)
        else:
            items.append(
                {
                    "description": str(entry).strip() or f"Item {index}",
                    "quantity": "1",
                    "unit_price": "",
                    "line_subtotal": "",
                    "allocated_tax": "",
                    "line_total": "",
                    "raw": entry,
                }
            )
    return allocate_tax(items, total_tax)


@dataclass
class FieldSpec:
    name: str
    description: str = ""

    def normalized_name(self) -> str:
        return normalize_field_name(self.name)


@dataclass
class ExtractionResult:
    image_path: str
    model: str
    extracted_at: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)
    approval_status: str = "Pending Review"
    approved_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, default=str)


class ReceiptClientError(Exception):
    pass


class ReceiptClient:
    def __init__(self, endpoint_url: str, model: str, api_key: str = "", timeout_seconds: int = 90, structured_output: bool = True) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.structured_output = bool(structured_output)

    def list_models(self) -> list[str]:
        models_url = self._models_url()
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(models_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise ReceiptClientError(f"Model list returned HTTP {error.code}: {body or error.reason}") from error
        except urllib.error.URLError as error:
            raise ReceiptClientError(f"Could not reach model endpoint: {error.reason}") from error
        data = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids: list[str] = []
        for item in data:
            if isinstance(item, dict):
                model_id = str(item.get("id", "")).strip()
                if model_id:
                    model_ids.append(model_id)
        return sorted(dict.fromkeys(model_ids))

    def _models_url(self) -> str:
        endpoint = self.endpoint_url.rstrip("/")
        if endpoint.endswith("/v1/chat/completions"):
            return endpoint[:-len("/chat/completions")] + "/models"
        if endpoint.endswith("/chat/completions"):
            return endpoint[:-len("/chat/completions")] + "/models"
        if endpoint.endswith("/v1"):
            return endpoint + "/models"
        return endpoint + "/v1/models"

    def extract_receipt(self, image_path: str, field_specs: list[FieldSpec], extra_instructions: str = "") -> ExtractionResult:
        if not image_path or not os.path.exists(image_path):
            raise ReceiptClientError("Choose a receipt image first.")
        if not self.endpoint_url:
            raise ReceiptClientError("Endpoint URL is required.")
        if not self.model:
            raise ReceiptClientError("Model is required.")
        payload = self._build_payload(image_path, field_specs, extra_instructions)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise ReceiptClientError(f"Extraction request returned HTTP {error.code}: {body or error.reason}") from error
        except urllib.error.URLError as error:
            raise ReceiptClientError(f"Could not reach endpoint: {error.reason}") from error
        try:
            raw_response = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise ReceiptClientError(f"Endpoint did not return JSON: {raw_text[:500]}") from error
        fields = self._parse_fields(raw_response)
        return ExtractionResult(
            image_path=image_path,
            model=self.model,
            extracted_at=datetime.now().isoformat(timespec="seconds"),
            fields=fields,
            raw_response=raw_response,
        )

    def _build_payload(self, image_path: str, field_specs: list[FieldSpec], extra_instructions: str) -> dict[str, Any]:
        image_data = self._image_to_data_url(image_path)
        schema_map = {spec.normalized_name(): (spec.description.strip() or f"Extract {spec.normalized_name()}") for spec in field_specs}
        system_prompt = (
            "You are a receipt extraction engine running Qwen 3.6. "
            "Return strict JSON only. Do not use markdown. Do not add commentary. "
            "Use null for unknown values. Preserve visible receipt values exactly when practical. "
            "Do not invent merchants, dates, amounts, or line items. "
            "Output valid JSON."
        )
        user_payload = {
            "task": "Extract receipt fields from this image into JSON.",
            "requested_fields": schema_map,
            "item_rules": [
                "Return items as an array when line items are visible.",
                "Each item should include description, quantity, unit_price, and line_subtotal when visible.",
                "Do not allocate tax yourself unless the receipt explicitly shows item-level tax.",
                "Keep raw observed money values as visible on the receipt.",
            ],
            "rules": [
                "Return a single JSON object.",
                "Include all requested fields.",
                "Use null for missing fields.",
                "Confidence should be a number from 0 to 1 when possible.",
                "Notes should summarize uncertainty briefly.",
            ],
            "extra_instructions": extra_instructions.strip(),
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                },
            ],
        }
        payload["response_format"] = {"type": "json_object"} if self.structured_output else {"type": "text"}
        return payload

    def _image_to_data_url(self, image_path: str) -> str:
        suffix = Path(image_path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ReceiptClientError("Image must be .jpg, .jpeg, .png, or .webp.")
        mime = ".png" if suffix == ".png" else ".webp" if suffix == ".webp" else ".jpeg"
        with open(image_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return f"data:image/{mime.lstrip('.')};base64,{encoded}"

    def _parse_fields(self, raw_response: dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw_response.get("fields"), dict):
            return dict(raw_response["fields"])
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if isinstance(content, str) and content.strip():
                parsed = self._extract_json(content)
                if isinstance(parsed.get("fields"), dict):
                    return dict(parsed["fields"])
                return parsed
        top = {k: v for k, v in raw_response.items() if k not in {"id", "object", "created", "model", "choices", "usage"}}
        return top

    def _extract_json(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(cleaned[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            raise ReceiptClientError("Model response was not valid JSON.")


def summarize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    tax_total = money_to_decimal(fields.get("tax"))
    items = standardize_items(fields.get("items"), tax_total)
    return {
        "merchant_name": fields.get("merchant_name"),
        "merchant_address": fields.get("merchant_address"),
        "receipt_date": fields.get("receipt_date"),
        "receipt_time": fields.get("receipt_time"),
        "subtotal": decimal_to_str(money_to_decimal(fields.get("subtotal"))),
        "tax": decimal_to_str(money_to_decimal(fields.get("tax"))),
        "tip": decimal_to_str(money_to_decimal(fields.get("tip"))),
        "total": decimal_to_str(money_to_decimal(fields.get("total"))),
        "payment_method": fields.get("payment_method"),
        "expense_category": fields.get("expense_category"),
        "confidence": fields.get("confidence"),
        "notes": fields.get("notes"),
        "items": items,
    }


class ApiServerController:
    def __init__(self, config_getter, log_callback=None):
        self.config_getter = config_getter
        self.log_callback = log_callback or (lambda msg: None)
        self.thread = None
        self.server = None
        self.loop = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.running_host = None
        self.running_port = None

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, host: str, port: int) -> tuple[bool, str]:
        if self.is_running():
            return False, "API server is already running."
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
            import multipart  # noqa: F401
        except Exception as error:
            return False, f"Install API dependencies first: pip install fastapi uvicorn python-multipart ({error})"
        self.thread = threading.Thread(target=self._run_server, args=(host, port), daemon=True)
        self.thread.start()
        for _ in range(50):
            if self.is_running() and self.running_port == port:
                return True, f"API server starting on http://{host}:{port}"
            time.sleep(0.1)
        return True, f"API server thread launched for http://{host}:{port}"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running() or self.server is None:
            return False, "API server is not running."
        self.server.should_exit = True
        self.log_callback("Stopping API server...")
        return True, "Stop signal sent to API server."

    def _run_server(self, host: str, port: int) -> None:
        import uvicorn
        app = self._create_app()
        config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        self.server = server
        self.running_host = host
        self.running_port = port
        self.log_callback(f"API server listening on http://{host}:{port}")
        server.run()
        self.log_callback("API server stopped.")
        self.server = None
        self.thread = None
        self.running_host = None
        self.running_port = None

    def _create_app(self):
        from fastapi import FastAPI, File, HTTPException, UploadFile
        from fastapi.responses import HTMLResponse
        app = FastAPI(title="Receipt API", version="1.0")
        controller = self

        @app.get("/", response_class=HTMLResponse)
        async def home():
            host = controller.running_host or "192.168.34.44"
            port = controller.running_port or 8000
            return f"""<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Receipt Capture</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin:0; background:#101114; color:#f5f7fa; }}
    .wrap {{ max-width:720px; margin:0 auto; padding:24px 18px 40px; }}
    .card {{ background:#181b20; border-radius:16px; padding:20px; box-shadow:0 10px 30px rgba(0,0,0,.25); }}
    h1 {{ font-size:28px; margin:0 0 8px; }}
    p {{ color:#98a2b3; line-height:1.5; }}
    input[type=file] {{ width:100%; margin:14px 0; color:#d0d5dd; }}
    button {{ width:100%; border:0; border-radius:12px; padding:16px; font-size:16px; font-weight:700; background:#2563eb; color:white; }}
    button:disabled {{ background:#475467; }}
    .preview {{ width:100%; margin-top:14px; border-radius:14px; overflow:hidden; background:#0c0d10; min-height:120px; display:flex; align-items:center; justify-content:center; }}
    .preview img {{ display:block; width:100%; height:auto; }}
    .status, pre {{ margin-top:14px; background:#111318; color:#d0d5dd; border-radius:12px; padding:14px; overflow:auto; white-space:pre-wrap; word-break:break-word; }}
    .ok {{ color:#32d583; }} .err {{ color:#f97066; }}
    .hint {{ font-size:13px; color:#98a2b3; margin-top:10px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Receipt Capture</h1>
      <p>Take a receipt photo with your phone and send it directly to the extractor server.</p>
      <input id="file" type="file" accept="image/*" capture="environment">
      <button id="sendBtn">Take / Choose Photo and Upload</button>
      <div class="hint">Server: http://{host}:{port}</div>
      <div class="preview" id="preview">No image selected.</div>
      <div class="status" id="status">Waiting for receipt photo.</div>
      <pre id="output"></pre>
    </div>
  </div>
  <script>
    const fileInput = document.getElementById('file');
    const sendBtn = document.getElementById('sendBtn');
    const preview = document.getElementById('preview');
    const statusEl = document.getElementById('status');
    const output = document.getElementById('output');
    let currentFile = null;

    function setStatus(msg, cls='') {{
      statusEl.className = 'status ' + cls;
      statusEl.textContent = msg;
    }}

    fileInput.addEventListener('change', () => {{
      const file = fileInput.files && fileInput.files[0];
      currentFile = file || null;
      if (!file) {{
        preview.textContent = 'No image selected.';
        return;
      }}
      const url = URL.createObjectURL(file);
      preview.innerHTML = '<img src="' + url + '" alt="Receipt preview">';
      setStatus('Photo selected: ' + file.name);
    }});

    sendBtn.addEventListener('click', async () => {{
      if (!currentFile) {{
        fileInput.click();
        return;
      }}
      sendBtn.disabled = true;
      output.textContent = '';
      setStatus('Uploading receipt...');
      try {{
        const form = new FormData();
        form.append('file', currentFile, currentFile.name || 'receipt.jpg');
        const uploadRes = await fetch('/receipts/upload', {{ method:'POST', body:form }});
        const uploadJson = await uploadRes.json();
        output.textContent = JSON.stringify(uploadJson, null, 2);
        if (!uploadRes.ok) throw new Error(uploadJson.detail || 'Upload failed');
        setStatus('Receipt uploaded. Processing...', '');
        const jobId = uploadJson.job_id;
        const poll = async () => {{
          const res = await fetch('/jobs/' + jobId);
          const data = await res.json();
          output.textContent = JSON.stringify(data, null, 2);
          if (data.status === 'processing') {{
            setTimeout(poll, 1500);
            return;
          }}
          if (data.status === 'complete') {{
            setStatus('Done. Receipt extracted successfully.', 'ok');
          }} else {{
            setStatus('Processing failed: ' + (data.error || 'Unknown error'), 'err');
          }}
          sendBtn.disabled = false;
        }};
        poll();
      }} catch (err) {{
        setStatus('Upload failed: ' + err.message, 'err');
        sendBtn.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""

        @app.get("/health")
        async def health():
            return {
                "ok": True,
                "service": "receipt-api",
                "version": "1.0",
                "server_time": datetime.now().isoformat(timespec="seconds"),
            }

        @app.post("/receipts/upload")
        async def upload_receipt(file: UploadFile = File(...)):
            suffix = Path(file.filename or "receipt.jpg").suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise HTTPException(status_code=400, detail="Only jpg, jpeg, png, or webp receipts are supported.")
            job_id = uuid.uuid4().hex
            save_path = UPLOAD_DIR / f"{job_id}{suffix}"
            with open(save_path, "wb") as handle:
                shutil.copyfileobj(file.file, handle)
            with controller.lock:
                controller.jobs[job_id] = {
                    "job_id": job_id,
                    "status": "processing",
                    "filename": file.filename,
                    "saved_path": str(save_path),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "summary": None,
                    "error": None,
                }
            threading.Thread(target=controller._process_job, args=(job_id, str(save_path)), daemon=True).start()
            controller.log_callback(f"Accepted API upload: {file.filename} -> job {job_id}")
            return {"job_id": job_id, "status": "processing"}

        @app.get("/jobs/{job_id}")
        async def get_job(job_id: str):
            with controller.lock:
                job = controller.jobs.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            return {
                "job_id": job["job_id"],
                "status": job["status"],
                "summary": job.get("summary"),
                "error": job.get("error"),
                "created_at": job.get("created_at"),
            }

        return app

    def _process_job(self, job_id: str, image_path: str) -> None:
        try:
            config = self.config_getter()
            field_specs = [FieldSpec(item["name"], item.get("description", "")) for item in config.get("fields", DEFAULT_FIELDS)]
            client = ReceiptClient(
                endpoint_url=config.get("endpoint_url", DEFAULT_ENDPOINT),
                model=config.get("model", DEFAULT_MODEL),
                api_key=config.get("api_key", ""),
                timeout_seconds=int(config.get("timeout_seconds", 90)),
                structured_output=bool(config.get("structured_output", True)),
            )
            result = client.extract_receipt(image_path, field_specs, config.get("extra_instructions", ""))
            summary = summarize_fields(result.fields)
            with self.lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = "complete"
                    self.jobs[job_id]["summary"] = summary
                    self.jobs[job_id]["raw_result"] = asdict(result)
            self.log_callback(f"Completed API job {job_id}")
        except Exception as error:
            with self.lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = "error"
                    self.jobs[job_id]["error"] = str(error)
            self.log_callback(f"API job {job_id} failed: {error}")


def make_scrollable_frame(parent: tk.Widget, bg: str) -> tuple[tk.Frame, tk.Frame]:
    container = tk.Frame(parent, bg=bg)
    container.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)
    canvas = tk.Canvas(container, bg=bg, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    content = tk.Frame(canvas, bg=bg)
    window_id = canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    def update_scrollregion(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def resize_content(event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    def scroll_content(event) -> str:
        canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    content.bind("<Configure>", update_scrollregion)
    canvas.bind("<Configure>", resize_content)
    canvas.bind("<MouseWheel>", scroll_content)
    content.bind("<MouseWheel>", scroll_content)
    return container, content


class StandardizedReceiptWindow:
    def __init__(self, parent: tk.Tk, result: ExtractionResult) -> None:
        self.parent = parent
        self.result = result
        self.top = tk.Toplevel(parent)
        self.top.title("Standardized Receipt Review")
        self.top.geometry("1180x860")
        self.top.configure(bg="#f3f4f6")
        self._build()

    def _build(self) -> None:
        fields = self.result.fields
        tax_total = money_to_decimal(fields.get("tax")) or Decimal("0.00")
        items = standardize_items(fields.get("items"), tax_total)
        scroll_container, scroll_body = make_scrollable_frame(self.top, "#f3f4f6")
        scroll_container.pack(fill="both", expand=True)
        card = tk.Frame(scroll_body, bg="#ffffff", padx=20, pady=20)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        header = tk.Frame(card, bg="#ffffff")
        header.pack(fill="x")
        tk.Label(header, text=str(fields.get("merchant_name") or "Unknown Merchant"), bg="#ffffff", fg="#111827", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        address_text = str(fields.get("merchant_address") or "").strip()
        if address_text:
            tk.Label(header, text=address_text, bg="#ffffff", fg="#4b5563", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        tk.Label(header, text=f"Receipt reviewed with {self.result.model}", bg="#ffffff", fg="#6b7280", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        meta = tk.Frame(card, bg="#ffffff")
        meta.pack(fill="x", pady=(18, 10))
        meta.columnconfigure(1, weight=1)
        meta.columnconfigure(3, weight=1)
        pairs = [("Date", fields.get("receipt_date")), ("Time", fields.get("receipt_time")), ("Payment", fields.get("payment_method")), ("Category", fields.get("expense_category"))]
        for idx, (label, value) in enumerate(pairs):
            row = idx // 2
            col = (idx % 2) * 2
            tk.Label(meta, text=label, bg="#ffffff", fg="#6b7280", font=("Segoe UI", 9, "bold")).grid(row=row, column=col, sticky="w", padx=(0, 12), pady=4)
            tk.Label(meta, text=str(value or ""), bg="#ffffff", fg="#111827", font=("Segoe UI", 10)).grid(row=row, column=col + 1, sticky="w", pady=4)
        line_title = tk.Frame(card, bg="#ffffff")
        line_title.pack(fill="x", pady=(10, 6))
        tk.Label(line_title, text="Standardized Line Items", bg="#ffffff", fg="#111827", font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(line_title, text="Sales price and allocated tax shown per item", bg="#ffffff", fg="#6b7280", font=("Segoe UI", 9)).pack(side="right")
        table_wrap = tk.Frame(card, bg="#ffffff")
        table_wrap.pack(fill="both", expand=True)
        columns = [col[0] for col in STANDARD_ITEM_COLUMNS]
        tree = ttk.Treeview(table_wrap, columns=columns, show="headings", height=18)
        for key, label in STANDARD_ITEM_COLUMNS:
            tree.heading(key, text=label)
            width = 320 if key == "description" else 110
            anchor = "w" if key == "description" else "e"
            tree.column(key, width=width, anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)
        if items:
            for item in items:
                tree.insert("", "end", values=(item.get("description", ""), item.get("quantity", ""), item.get("unit_price", ""), item.get("line_subtotal", ""), item.get("allocated_tax", ""), item.get("line_total", "")))
        else:
            tree.insert("", "end", values=("No line items were extracted.", "", "", "", "", ""))
        totals = tk.Frame(card, bg="#f9fafb", padx=16, pady=14, highlightbackground="#e5e7eb", highlightthickness=1)
        totals.pack(fill="x", pady=(14, 8))
        totals.columnconfigure(1, weight=1)
        summary_rows = [("Subtotal", decimal_to_str(money_to_decimal(fields.get("subtotal")))), ("Tax", decimal_to_str(money_to_decimal(fields.get("tax")))), ("Tip", decimal_to_str(money_to_decimal(fields.get("tip")))), ("Total", decimal_to_str(money_to_decimal(fields.get("total"))))]
        for idx, (label, value) in enumerate(summary_rows):
            fg = "#111827" if label == "Total" else "#374151"
            font = ("Segoe UI", 11, "bold") if label == "Total" else ("Segoe UI", 10)
            tk.Label(totals, text=label, bg="#f9fafb", fg=fg, font=font).grid(row=idx, column=0, sticky="w", pady=3)
            tk.Label(totals, text=value, bg="#f9fafb", fg=fg, font=font).grid(row=idx, column=1, sticky="e", pady=3)
        notes = str(fields.get("notes") or "").strip()
        confidence = fields.get("confidence")
        footer = tk.Frame(card, bg="#ffffff")
        footer.pack(fill="x", pady=(8, 0))
        if notes:
            tk.Label(footer, text=f"Notes: {notes}", bg="#ffffff", fg="#4b5563", justify="left", wraplength=900, font=("Segoe UI", 10)).pack(anchor="w")
        if confidence not in {None, ""}:
            tk.Label(footer, text=f"Confidence: {confidence}", bg="#ffffff", fg="#6b7280", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))


class ReceiptApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1560x980")
        self.minsize(1280, 820)
        self.configure(bg="#101114")
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self._configure_styles()
        self.config_data = self._load_config()
        self.field_specs = [FieldSpec(item["name"], item.get("description", "")) for item in self.config_data.get("fields", DEFAULT_FIELDS)]
        self.result: ExtractionResult | None = None
        self.preview_original = None
        self.preview_display = None
        self.api_server = ApiServerController(self._runtime_config, self._append_server_log)
        self.endpoint_var = tk.StringVar(value=self.config_data.get("endpoint_url", DEFAULT_ENDPOINT))
        self.api_key_var = tk.StringVar(value=self.config_data.get("api_key", ""))
        self.model_var = tk.StringVar(value=self.config_data.get("model", DEFAULT_MODEL))
        self.timeout_var = tk.StringVar(value=str(self.config_data.get("timeout_seconds", 90)))
        self.structured_var = tk.BooleanVar(value=bool(self.config_data.get("structured_output", True)))
        self.image_path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Load a receipt image to begin.")
        self.extra_var = tk.StringVar(value=self.config_data.get("extra_instructions", ""))
        self.api_host_var = tk.StringVar(value=self.config_data.get("api_host", "192.168.34.44"))
        self.api_port_var = tk.StringVar(value=str(self.config_data.get("api_port", 8000)))
        self.api_status_var = tk.StringVar(value="API server is stopped.")
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_styles(self) -> None:
        bg = "#101114"
        panel2 = "#1f232a"
        text = "#e8ecf1"
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabel", background=bg, foreground=text)
        self.style.configure("TButton", font=("Segoe UI", 10))
        self.style.configure("Treeview", background=panel2, fieldbackground=panel2, foreground=text, rowheight=28)
        self.style.configure("Treeview.Heading", background="#242934", foreground=text)

    def _guess_local_ip(self) -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return "0.0.0.0"

    def _load_config(self) -> dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"endpoint_url": DEFAULT_ENDPOINT, "api_key": "", "model": DEFAULT_MODEL, "timeout_seconds": 90, "structured_output": True, "extra_instructions": "", "fields": DEFAULT_FIELDS, "api_host": "192.168.34.44", "api_port": 8000}

    def _runtime_config(self) -> dict[str, Any]:
        return {
            "endpoint_url": self.endpoint_var.get().strip() or DEFAULT_ENDPOINT,
            "api_key": self.api_key_var.get().strip(),
            "model": self.model_var.get().strip() or DEFAULT_MODEL,
            "timeout_seconds": self._timeout_value(),
            "structured_output": bool(self.structured_var.get()),
            "extra_instructions": self.extra_text.get("1.0", "end").strip() if hasattr(self, 'extra_text') else self.extra_var.get().strip(),
            "fields": [asdict(spec) for spec in self.field_specs],
            "api_host": self.api_host_var.get().strip() or self._guess_local_ip(),
            "api_port": self._api_port_value(),
        }

    def _save_config(self) -> None:
        payload = self._runtime_config()
        CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _timeout_value(self) -> int:
        try:
            return max(5, int(float(self.timeout_var.get() or 90)))
        except Exception:
            return 90

    def _api_port_value(self) -> int:
        try:
            return max(1, min(65535, int(float(self.api_port_var.get() or 8000))))
        except Exception:
            return 8000

    def _client(self, timeout_override: int | None = None) -> ReceiptClient:
        return ReceiptClient(endpoint_url=self.endpoint_var.get(), model=self.model_var.get(), api_key=self.api_key_var.get(), timeout_seconds=timeout_override or self._timeout_value(), structured_output=self.structured_var.get())

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=3)
        self.columnconfigure(2, weight=2)
        self.rowconfigure(1, weight=1)
        header = tk.Frame(self, bg="#101114", padx=18, pady=14)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="Receipt Extractor", bg="#101114", fg="#f5f7fa", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Desktop UI + Android API server", bg="#101114", fg="#98a2b3", font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Label(header, textvariable=self.status_var, bg="#101114", fg="#d0d5dd", anchor="e", font=("Segoe UI", 10)).grid(row=0, column=1, rowspan=2, sticky="e")
        self._build_left_sidebar()
        self._build_center_preview()
        self._build_right_inspector()

    def _build_left_sidebar(self) -> None:
        left_outer = tk.Frame(self, bg="#181b20")
        left_outer.grid(row=1, column=0, sticky="nsew", padx=(14, 8), pady=(0, 14))
        left_outer.configure(width=360)
        left_outer.grid_propagate(False)
        scroll_container, left = make_scrollable_frame(left_outer, "#181b20")
        scroll_container.pack(fill="both", expand=True)
        left.configure(padx=14, pady=14)
        row = 0
        tk.Label(left, text="Workflow", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 12, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        for text, command in [("Open Receipt", self.choose_image), ("Analyze with Qwen", self.analyze_receipt), ("Review Formatted Receipt", self.open_standardized_receipt), ("Approve", self.approve_result), ("Mark Needs Correction", self.mark_needs_correction), ("Export JSON", self.export_json), ("Export CSV", self.export_csv)]:
            ttk.Button(left, text=text, command=command).grid(row=row, column=0, sticky="ew", pady=4)
            row += 1
        ttk.Separator(left, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=12)
        row += 1
        tk.Label(left, text="Receipt Image", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        tk.Entry(left, textvariable=self.image_path_var, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", relief="flat").grid(row=row, column=0, sticky="ew", pady=(4, 8))
        row += 1
        tk.Label(left, text="Server Settings", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        self._labeled_entry(left, row, "Endpoint", self.endpoint_var)
        row += 1
        self._labeled_entry(left, row, "Model", self.model_var)
        row += 1
        self._labeled_entry(left, row, "API Key", self.api_key_var, show="*")
        row += 1
        self._labeled_entry(left, row, "Timeout", self.timeout_var)
        row += 1
        ttk.Checkbutton(left, text="Structured JSON mode", variable=self.structured_var).grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        ttk.Button(left, text="Fetch Available Models", command=self.pick_model).grid(row=row, column=0, sticky="ew", pady=6)
        row += 1
        tk.Label(left, text="Extra Instructions", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        extra = tk.Text(left, height=6, bg="#111318", fg="#e8ecf1", insertbackground="#f5f7fa", relief="flat", wrap="word")
        extra.grid(row=row, column=0, sticky="nsew", pady=(4, 8))
        extra.insert("1.0", self.extra_var.get())
        self.extra_text = extra
        row += 1
        api_frame = tk.LabelFrame(left, text="Android API Server", bg="#181b20", fg="#f5f7fa")
        api_frame.grid(row=row, column=0, sticky="ew", pady=(6, 8))
        api_frame.columnconfigure(1, weight=1)
        tk.Label(api_frame, text="Host", bg="#181b20", fg="#98a2b3").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        tk.Entry(api_frame, textvariable=self.api_host_var, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", relief="flat").grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 4))
        tk.Label(api_frame, text="Port", bg="#181b20", fg="#98a2b3").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        tk.Entry(api_frame, textvariable=self.api_port_var, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", relief="flat").grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(api_frame, text="Start API Server", command=self.start_api_server).grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 6))
        ttk.Button(api_frame, text="Stop API Server", command=self.stop_api_server).grid(row=2, column=1, sticky="ew", padx=8, pady=(8, 6))
        tk.Label(api_frame, textvariable=self.api_status_var, bg="#181b20", fg="#d0d5dd", wraplength=300, justify="left").grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        row += 1
        ttk.Button(left, text="Save Settings", command=self.save_settings).grid(row=row, column=0, sticky="ew", pady=(0, 4))
        row += 1
        tk.Label(left, text="API Log", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        server_log = tk.Text(left, height=9, bg="#111318", fg="#d0d5dd", insertbackground="#f5f7fa", relief="flat", wrap="word")
        server_log.grid(row=row, column=0, sticky="nsew", pady=(4, 0))
        self.server_log_text = server_log
        left.columnconfigure(0, weight=1)
        left.rowconfigure(row, weight=1)

    def _labeled_entry(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        tk.Label(parent, text=label, bg="#181b20", fg="#98a2b3", font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w")
        entry = tk.Entry(parent, textvariable=variable, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", relief="flat", show=show or "")
        entry.grid(row=row, column=0, sticky="ew", pady=(20, 4))

    def _build_center_preview(self) -> None:
        center = tk.Frame(self, bg="#181b20", padx=14, pady=14)
        center.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 14))
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        toolbar = tk.Frame(center, bg="#181b20")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(toolbar, text="Receipt Preview", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(toolbar, text="Fit", command=self.fit_preview).pack(side="right", padx=4)
        ttk.Button(toolbar, text="100%", command=self.actual_size_preview).pack(side="right", padx=4)
        ttk.Button(toolbar, text="Rotate 90°", command=self.rotate_preview).pack(side="right", padx=4)
        frame = tk.Frame(center, bg="#101114")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(frame, bg="#0c0d10", highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.preview_canvas.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.preview_canvas.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.preview_canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.preview_canvas.create_text(420, 300, text="Open a receipt image to preview it here.", fill="#98a2b3", font=("Segoe UI", 14))
        self.preview_canvas.bind("<MouseWheel>", self._preview_mousewheel)

    def _build_right_inspector(self) -> None:
        right = tk.Frame(self, bg="#181b20", padx=14, pady=14)
        right.grid(row=1, column=2, sticky="nsew", padx=(8, 14), pady=(0, 14))
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)
        tk.Label(right, text="Review", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(right, text="Edit fields before opening the standardized receipt window.", bg="#181b20", fg="#98a2b3", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(2, 10))
        scroll_container, review_wrap = make_scrollable_frame(right, "#181b20")
        scroll_container.grid(row=2, column=0, sticky="nsew")
        review_wrap.rowconfigure(0, weight=2)
        review_wrap.rowconfigure(1, weight=2)
        review_wrap.rowconfigure(2, weight=2)
        review_wrap.columnconfigure(0, weight=1)
        key_frame = tk.LabelFrame(review_wrap, text="Key Fields", bg="#181b20", fg="#f5f7fa")
        key_frame.grid(row=0, column=0, sticky="nsew")
        key_frame.columnconfigure(1, weight=1)
        self.key_vars: dict[str, tk.StringVar] = {}
        for idx, field_name in enumerate(KEY_FIELDS):
            tk.Label(key_frame, text=titleize_field(field_name), bg="#181b20", fg="#98a2b3", anchor="w").grid(row=idx, column=0, sticky="w", padx=8, pady=6)
            var = tk.StringVar(value="")
            self.key_vars[field_name] = var
            tk.Entry(key_frame, textvariable=var, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", relief="flat").grid(row=idx, column=1, sticky="ew", padx=8, pady=6)
        items_frame = tk.LabelFrame(review_wrap, text="Line Items", bg="#181b20", fg="#f5f7fa")
        items_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 10))
        items_frame.rowconfigure(0, weight=1)
        items_frame.columnconfigure(0, weight=1)
        item_cols = [c[0] for c in STANDARD_ITEM_COLUMNS]
        self.item_tree = ttk.Treeview(items_frame, columns=item_cols, show="headings", height=8)
        for key, label in STANDARD_ITEM_COLUMNS:
            self.item_tree.heading(key, text=label)
            width = 220 if key == "description" else 90
            self.item_tree.column(key, width=width, anchor="w" if key == "description" else "e")
        self.item_tree.grid(row=0, column=0, sticky="nsew")
        item_scroll = ttk.Scrollbar(items_frame, orient="vertical", command=self.item_tree.yview)
        item_scroll.grid(row=0, column=1, sticky="ns")
        self.item_tree.configure(yscrollcommand=item_scroll.set)
        self.item_tree.bind("<Double-1>", self.edit_item_value)
        self.item_row_map: dict[str, int] = {}
        raw_frame = tk.LabelFrame(review_wrap, text="Raw JSON", bg="#181b20", fg="#f5f7fa")
        raw_frame.grid(row=2, column=0, sticky="nsew")
        raw_frame.rowconfigure(0, weight=1)
        raw_frame.columnconfigure(0, weight=1)
        self.raw_text = tk.Text(raw_frame, bg="#111318", fg="#d0d5dd", insertbackground="#f5f7fa", relief="flat", wrap="none")
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        raw_y = ttk.Scrollbar(raw_frame, orient="vertical", command=self.raw_text.yview)
        raw_x = ttk.Scrollbar(raw_frame, orient="horizontal", command=self.raw_text.xview)
        raw_y.grid(row=0, column=1, sticky="ns")
        raw_x.grid(row=1, column=0, sticky="ew")
        self.raw_text.configure(yscrollcommand=raw_y.set, xscrollcommand=raw_x.set)

    def _append_server_log(self, message: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        def update():
            self.server_log_text.insert("end", timestamped)
            self.server_log_text.see("end")
        self.after(0, update)

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(title="Choose receipt image", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp"), ("All Files", "*.*")])
        if not path:
            return
        self.image_path_var.set(path)
        self.status_var.set("Receipt loaded. Ready for extraction.")
        self.display_preview(path)

    def display_preview(self, path: str) -> None:
        self.preview_canvas.delete("all")
        if not HAS_PIL:
            self.preview_canvas.create_text(420, 300, text="Install Pillow to preview receipts: pip install Pillow", fill="#e11d48", font=("Segoe UI", 13))
            return
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.exif_transpose(image)
            self.preview_original = image
            self.fit_preview()
        except Exception as error:
            self.preview_canvas.create_text(420, 300, text=f"Could not display image:\n{error}", fill="#e11d48", font=("Segoe UI", 13))

    def fit_preview(self) -> None:
        if self.preview_original is None or not HAS_PIL:
            return
        canvas_w = max(self.preview_canvas.winfo_width(), 600)
        canvas_h = max(self.preview_canvas.winfo_height(), 500)
        image = self.preview_original.copy()
        image.thumbnail((canvas_w - 20, canvas_h - 20), Image.Resampling.LANCZOS)
        self._render_preview(image)

    def actual_size_preview(self) -> None:
        if self.preview_original is None or not HAS_PIL:
            return
        self._render_preview(self.preview_original.copy())

    def rotate_preview(self) -> None:
        if self.preview_original is None or not HAS_PIL:
            return
        self.preview_original = self.preview_original.rotate(90, expand=True)
        self.fit_preview()

    def _render_preview(self, image) -> None:
        self.preview_display = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, image=self.preview_display, anchor="nw")
        self.preview_canvas.configure(scrollregion=(0, 0, image.width, image.height))

    def _preview_mousewheel(self, event) -> str:
        units = -1 if event.delta > 0 else 1
        self.preview_canvas.yview_scroll(units, "units")
        return "break"

    def save_settings(self) -> None:
        self.extra_var.set(self.extra_text.get("1.0", "end").strip())
        self._save_config()
        messagebox.showinfo("Saved", "Settings saved.")

    def start_api_server(self) -> None:
        self.extra_var.set(self.extra_text.get("1.0", "end").strip())
        self._save_config()
        ok, message = self.api_server.start(self.api_host_var.get().strip() or self._guess_local_ip(), self._api_port_value())
        self.api_status_var.set(message)
        self._append_server_log(message)
        if not ok:
            messagebox.showerror("API Server", message)

    def stop_api_server(self) -> None:
        ok, message = self.api_server.stop()
        self.api_status_var.set(message)
        self._append_server_log(message)
        if not ok:
            messagebox.showwarning("API Server", message)

    def pick_model(self) -> None:
        self.status_var.set("Fetching available models...")
        threading.Thread(target=self._pick_model_worker, daemon=True).start()

    def _pick_model_worker(self) -> None:
        try:
            models = self._client(timeout_override=15).list_models()
            self.after(0, lambda: self._show_model_picker(models))
        except Exception as error:
            self.after(0, lambda: messagebox.showerror("Model List Error", str(error)))
            self.after(0, lambda: self.status_var.set("Could not fetch model list."))

    def _show_model_picker(self, models: list[str]) -> None:
        self.status_var.set(f"Found {len(models)} model(s).")
        if not models:
            messagebox.showwarning("No Models", "No models were returned by the server.")
            return
        top = tk.Toplevel(self)
        top.title("Choose Model")
        top.geometry("560x420")
        top.transient(self)
        top.grab_set()
        frame = tk.Frame(top, bg="#181b20", padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Available models", bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        listbox = tk.Listbox(frame, bg="#111318", fg="#f5f7fa", selectbackground="#2f80ed", activestyle="none")
        listbox.pack(fill="both", expand=True, pady=10)
        for model in models:
            listbox.insert("end", model)
            if model == self.model_var.get():
                listbox.selection_set("end")
        if not listbox.curselection():
            listbox.selection_set(0)
        def use_selected() -> None:
            selection = listbox.curselection()
            if selection:
                self.model_var.set(models[selection[0]])
                top.destroy()
        btns = tk.Frame(frame, bg="#181b20")
        btns.pack(fill="x")
        ttk.Button(btns, text="Use Selected", command=use_selected).pack(side="left")
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right")
        listbox.bind("<Double-1>", lambda _e: use_selected())

    def analyze_receipt(self) -> None:
        if not self.image_path_var.get().strip():
            messagebox.showwarning("No Image", "Choose a receipt image first.")
            return
        self.extra_var.set(self.extra_text.get("1.0", "end").strip())
        self._save_config()
        self.status_var.set("Analyzing receipt with Qwen 3.6...")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", "Analyzing receipt...\n")
        threading.Thread(target=self._analyze_worker, daemon=True).start()

    def _analyze_worker(self) -> None:
        try:
            result = self._client().extract_receipt(self.image_path_var.get(), self.field_specs, self.extra_var.get())
            self.result = result
            self.after(0, lambda: self.show_result(result))
        except Exception as error:
            self.after(0, lambda: self.show_error(str(error)))

    def show_result(self, result: ExtractionResult) -> None:
        self.status_var.set(f"Extraction complete at {result.extracted_at}. Click Review Formatted Receipt to inspect the standardized receipt.")
        self.refresh_review(result)
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", result.to_json())

    def show_error(self, message: str) -> None:
        self.status_var.set("Extraction failed.")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", f"Error:\n{message}\n")
        messagebox.showerror("Extraction Error", message)

    def refresh_review(self, result: ExtractionResult) -> None:
        for field_name, var in self.key_vars.items():
            var.set(format_value(result.fields.get(field_name)))
        self.refresh_items(result)

    def refresh_items(self, result: ExtractionResult) -> None:
        tax_total = money_to_decimal(result.fields.get("tax"))
        standardized_items = standardize_items(result.fields.get("items"), tax_total)
        result.fields["items"] = standardized_items
        self.item_tree.delete(*self.item_tree.get_children())
        self.item_row_map.clear()
        for index, item in enumerate(standardized_items):
            item_id = self.item_tree.insert("", "end", values=tuple(item.get(col[0], "") for col in STANDARD_ITEM_COLUMNS))
            self.item_row_map[item_id] = index

    def sync_key_fields_to_result(self) -> None:
        if not self.result:
            return
        for field_name, var in self.key_vars.items():
            text = var.get().strip()
            self.result.fields[field_name] = text if text else None
        tax_total = money_to_decimal(self.result.fields.get("tax"))
        self.result.fields["items"] = allocate_tax(self.result.fields.get("items", []), tax_total)

    def edit_item_value(self, event) -> None:
        if not self.result:
            return
        item_id = self.item_tree.identify_row(event.y)
        column = self.item_tree.identify_column(event.x)
        if not item_id or not column:
            return
        row_index = self.item_row_map.get(item_id)
        if row_index is None:
            return
        column_index = int(column.replace("#", "")) - 1
        field_key = STANDARD_ITEM_COLUMNS[column_index][0]
        current_value = self.result.fields.get("items", [])[row_index].get(field_key, "")
        top = tk.Toplevel(self)
        top.title(f"Edit Item {row_index + 1} - {titleize_field(field_key)}")
        top.geometry("560x240")
        top.transient(self)
        top.grab_set()
        frame = tk.Frame(top, bg="#181b20", padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=titleize_field(field_key), bg="#181b20", fg="#f5f7fa", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        editor = tk.Text(frame, height=5, bg="#111318", fg="#f5f7fa", insertbackground="#f5f7fa", wrap="word")
        editor.pack(fill="both", expand=True, pady=10)
        editor.insert("1.0", str(current_value or ""))
        def save_edit() -> None:
            raw = editor.get("1.0", "end").strip()
            self.result.fields["items"][row_index][field_key] = raw
            self.sync_key_fields_to_result()
            self.refresh_items(self.result)
            self.raw_text.delete("1.0", "end")
            self.raw_text.insert("1.0", self.result.to_json())
            top.destroy()
        btns = tk.Frame(frame, bg="#181b20")
        btns.pack(fill="x")
        ttk.Button(btns, text="Save", command=save_edit).pack(side="left")
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right")

    def open_standardized_receipt(self) -> None:
        if not self.result:
            messagebox.showwarning("No Result", "Analyze a receipt first.")
            return
        self.sync_key_fields_to_result()
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", self.result.to_json())
        StandardizedReceiptWindow(self, self.result)

    def approve_result(self) -> None:
        if not self.result:
            messagebox.showwarning("No Result", "Analyze a receipt first.")
            return
        self.sync_key_fields_to_result()
        approved_at = datetime.now().isoformat(timespec="seconds")
        self.result.approval_status = "Approved"
        self.result.approved_at = approved_at
        self.status_var.set(f"Approved at {approved_at}.")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", self.result.to_json())
        messagebox.showinfo("Approved", "Receipt extraction approved.")

    def mark_needs_correction(self) -> None:
        if not self.result:
            messagebox.showwarning("No Result", "Analyze a receipt first.")
            return
        self.sync_key_fields_to_result()
        self.result.approval_status = "Needs Correction"
        self.result.approved_at = ""
        self.status_var.set("Marked as needs correction.")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", self.result.to_json())
        messagebox.showinfo("Updated", "Marked as needs correction.")

    def export_json(self) -> None:
        if not self.result:
            messagebox.showwarning("No Result", "Analyze a receipt first.")
            return
        self.sync_key_fields_to_result()
        path = filedialog.asksaveasfilename(title="Export JSON", defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        Path(path).write_text(self.result.to_json(), encoding="utf-8")
        self.status_var.set(f"Exported JSON to {path}")

    def export_csv(self) -> None:
        if not self.result:
            messagebox.showwarning("No Result", "Analyze a receipt first.")
            return
        self.sync_key_fields_to_result()
        path = filedialog.asksaveasfilename(title="Export CSV", defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["field", "value"])
            for key in sorted(self.result.fields.keys()):
                value = self.result.fields.get(key)
                writer.writerow([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
        self.status_var.set(f"Exported CSV to {path}")

    def on_close(self) -> None:
        try:
            self.extra_var.set(self.extra_text.get("1.0", "end").strip())
            self._save_config()
        except Exception:
            pass
        self.api_server.stop()
        self.destroy()


def main() -> None:
    app = ReceiptApp()
    app.mainloop()


if __name__ == "__main__":
    main()
