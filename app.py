import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException, Form, Query
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
from twilio.request_validator import RequestValidator
from dotenv import load_dotenv
from twilio.rest import Client

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import WorksheetNotFound

# ================== CARGA .env ==================
load_dotenv()

# ================ TWILIO ========================
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
assert ACCOUNT_SID and AUTH_TOKEN, "Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN en .env"

twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN)

# ============ GOOGLE SHEETS DESTINO =============
SHEET_ID    = os.getenv("WHATSAPP_SHEET_ID", "")
SHEET_TAB   = os.getenv("WHATSAPP_SHEET_TAB", "whatsapp_inbox_v2")  # única pestaña
CREDS_JSON  = os.getenv("GOOGLE_CREDS_JSON", "credenciales_google.json")
assert SHEET_ID, "Falta WHATSAPP_SHEET_ID en .env"

# ============== ZONA HORARIA LOCAL ==============
LOCAL_TZ = os.getenv("LOCAL_TZ", "America/Argentina/Buenos_Aires")

# ============== URL PÚBLICA Y TOKEN (proxy) =====
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
MEDIA_ACCESS_TOKEN = os.getenv("MEDIA_ACCESS_TOKEN", "")  # si está vacío, no exige token en el proxy

# ================ FASTAPI APP ===================
app = FastAPI(title="Twilio WhatsApp → Google Sheets (una sola pestaña)")

# ====== Validador de firma Twilio (seguridad) ===
validator = RequestValidator(AUTH_TOKEN)

def verify_twilio_signature(request: Request, body: dict) -> None:
    sig = request.headers.get("X-Twilio-Signature")
    if not sig:
        raise HTTPException(status_code=401, detail="Missing Twilio signature")
    url = str(request.url)
    if not validator.validate(url, body, sig):
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

# ============ Cliente gspread (service account) ============
_scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Detectar si estamos en Cloud Run o desarrollo local
try:
    # En Cloud Run, intentar cargar desde el secreto montado
    if os.path.exists("/tmp/credentials.json"):
        _creds = ServiceAccountCredentials.from_json_keyfile_name("/tmp/credentials.json", _scope)
    else:
        # En desarrollo local, usar el archivo normal
        _creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_JSON, _scope)
except Exception as e:
    # Fallback: intentar con variables de entorno (si configuramos así)
    try:
        import json
        creds_content = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if creds_content:
            creds_dict = json.loads(creds_content)
            _creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, _scope)
        else:
            raise Exception("No se pudieron cargar las credenciales de Google")
    except Exception as e2:
        print(f"Error cargando credenciales: {e}, {e2}")
        raise

_gclient = gspread.authorize(_creds)

# ====== Encabezados de la única pestaña (orden final) ======
MAIN_HEADERS = [
    "Fecha", "hora", "Telefono", "Nombre",
    "message_type", "num_media", "body",
    "media_urls", "media_types", "proxy_urls",
    "message_sid"
]

# ================= Helpers de Sheets =======================
def _open_ws(sheet_id: str, tab: str, headers: list[str]):
    ss = _gclient.open_by_key(sheet_id)
    try:
        ws = ss.worksheet(tab)
    except WorksheetNotFound:
        ws = ss.add_worksheet(title=tab, rows="1000", cols=str(max(12, len(headers) + 2)))
        ws.append_row(headers, value_input_option="RAW")
        return ws
    # Asegurar headers correctos en A1
    first = ws.row_values(1)
    if [h.strip().lower() for h in first] != [h.strip().lower() for h in headers]:
        ws.update('A1', [headers])
    return ws

def get_ws_main():
    return _open_ws(SHEET_ID, SHEET_TAB, MAIN_HEADERS)

def _col_index_map(ws):
    hdr = ws.row_values(1)
    return {hdr[i].strip().lower(): i + 1 for i in range(len(hdr))}

def sid_exists_in_main(ws, sid: str) -> bool:
    """Dedup por message_sid en la misma pestaña."""
    if not sid:
        return False
    idx = _col_index_map(ws).get("message_sid")
    if not idx:
        return False
    col_vals = ws.col_values(idx)
    return sid in set(col_vals[1:])  # omitir header

def to_local(dt_utc: datetime) -> datetime:
    try:
        return dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(LOCAL_TZ))
    except Exception:
        return dt_utc  # fallback

def normalize_num(e164_or_wa: str | None) -> str:
    """Devuelve solo dígitos del E.164/waid."""
    if not e164_or_wa:
        return ""
    s = str(e164_or_wa)
    return "".join(ch for ch in s if ch.isdigit())

def _proxy_url(message_sid: str, idx: int) -> str:
    if not PUBLIC_BASE_URL:
        return ""
    base = f"{PUBLIC_BASE_URL}/media/{message_sid}/{idx}"
    if MEDIA_ACCESS_TOKEN:
        return f"{base}?t={MEDIA_ACCESS_TOKEN}"
    return base

def append_in_main(form: dict):
    """
    Inserta 1 fila en la única pestaña (con proxy_urls opcional) y
    evita duplicados usando message_sid.
    """
    message_sid = form.get("MessageSid") or form.get("SmsMessageSid") or ""
    ws = get_ws_main()
    if sid_exists_in_main(ws, message_sid):
        return False  # ya procesado

    ts_utc = datetime.utcnow()
    local_dt = to_local(ts_utc)
    fecha = local_dt.strftime("%Y-%m-%d")
    hora  = local_dt.strftime("%H:%M:%S")

    from_wa  = form.get("From", "")
    wa_id    = form.get("WaId", "") or normalize_num(from_wa)
    profile  = form.get("ProfileName", "")
    body     = (form.get("Body") or "").strip()
    msg_type = form.get("MessageType", "")

    # Medios
    try:
        num_media = int(form.get("NumMedia", "0") or "0")
    except Exception:
        num_media = 0
    media_urls, media_types, proxy_urls = [], [], []
    for i in range(num_media):
        u = form.get(f"MediaUrl{i}")
        t = form.get(f"MediaContentType{i}")
        if u:
            media_urls.append(u)
            proxy_urls.append(_proxy_url(message_sid, i + 1))  # 1-based index
        if t:
            media_types.append(t)

    ws.append_row([
        fecha, hora, wa_id, profile,
        msg_type, num_media, body,
        " | ".join(media_urls),
        " | ".join(media_types),
        " | ".join(proxy_urls),
        message_sid
    ], value_input_option="RAW")

    return True

# ==================== ENDPOINTS ====================
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.get("/inbox")
def inbox(limit: int = 50):
    """Devuelve las últimas N filas de la pestaña principal."""
    ws = get_ws_main()
    values = ws.get_all_values()
    if len(values) <= 1:
        return []
    header, rows = values[0], values[1:]
    rows = rows[-limit:]
    out = []
    for r in rows:
        rec = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        out.append(rec)
    return JSONResponse(out)

@app.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(None),
    To: str = Form(None),
    Body: str = Form(""),
    MessageSid: str = Form(None),
    NumMedia: str = Form("0"),
    ProfileName: str = Form(None),
    WaId: str = Form(None),
    MessageType: str = Form(None),
    MessagingServiceSid: str = Form(None),
    AccountSid: str = Form(None),
):
    # 1) Validar firma Twilio
    form = dict(await request.form())
    verify_twilio_signature(request, form)

    # 2) Guardar en la única pestaña (formato final + dedup)
    append_in_main(form)

    # 3) Sin auto-reply
    return PlainTextResponse("", status_code=200)

# =============== MEDIA PROXY (ver imágenes sin login) ===============
@app.get("/media/{message_sid}/{index}")
def media_proxy(
    message_sid: str,
    index: int,
    t: str | None = Query(default=None, description="media access token"),
):
    # Token simple (opcional)
    if MEDIA_ACCESS_TOKEN and t != MEDIA_ACCESS_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Buscar el media en Twilio
    try:
        medias = twilio_client.messages(message_sid).media.list()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"No pude listar media: {e}")

    if index < 1 or index > len(medias):
        raise HTTPException(status_code=404, detail="No existe media con ese índice")

    media = medias[index - 1]
    content_url = f"https://api.twilio.com{media.uri.replace('.json','')}"
    r = requests.get(content_url, auth=(ACCOUNT_SID, AUTH_TOKEN), stream=True)
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Twilio devolvió {r.status_code}")

    content_type = r.headers.get("Content-Type", "application/octet-stream")
    headers = {"Content-Disposition": f'inline; filename="{media.sid}"'}
    return StreamingResponse(r.raw, media_type=content_type, headers=headers)

# ======== Launcher opcional (python app.py) ========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
