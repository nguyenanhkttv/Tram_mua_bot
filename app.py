import os
import re
import io
import json
import asyncio
import requests
import pdfplumber
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from jinja2 import Template
from playwright.async_api import async_playwright
import google.generativeai as genai

# ==================== CẤU HÌNH ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# Lưu danh sách Chat ID nhận thông báo tự động
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()

# ==================== TEMPLATE INFOGRAPHIC 2K & FONTAWESOME ====================
HTML_TEMPLATE_STR = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: {{ theme.primary }};
            --gradient: {{ theme.header_bg }};
            --card-bg: {{ theme.card_bg }};
        }
        * { box-sizing: border-box; }
        body { font-family: 'Roboto', sans-serif; background: #f4f6f9; margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 540px; background: #ffffff; border-radius: 18px; box-shadow: 0 12px 30px rgba(0,0,0,0.12); overflow: hidden; border: 1px solid #e2e8f0; }
        .header { background: var(--gradient); color: white; padding: 22px 20px; text-align: center; }
        .header .agency { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; opacity: 0.9; margin-bottom: 5px; }
        .header h1 { margin: 0; font-size: 21px; font-weight: 900; text-transform: uppercase; line-height: 1.3; }
        .header .meta { margin-top: 8px; font-size: 11px; opacity: 0.9; font-weight: 500; }
        .content { padding: 20px; color: #1e293b; }
        .stats-grid { display: grid; grid-template-columns: repeat({{ stats|length }}, 1fr); gap: 10px; margin-bottom: 18px; }
        .stat-card { background: var(--card-bg); border-left: 4px solid var(--primary); padding: 10px; border-radius: 8px; text-align: center; }
        .stat-label { font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase; margin-bottom: 3px; }
        .stat-value { font-size: 17px; color: var(--primary); font-weight: 900; }
        .stat-note { font-size: 10px; color: #94a3b8; margin-top: 2px; }
        .section-title { font-size: 12px; font-weight: 700; color: var(--primary); margin: 14px 0 8px 0; text-transform: uppercase; display: flex; align-items: center; gap: 6px; }
        .area-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
        .tag { background: #f1f5f9; color: #334155; font-size: 11px; padding: 4px 8px; border-radius: 6px; font-weight: 600; border: 1px solid #e2e8f0; }
        .advice-box { background: #fffbeb; border: 1px solid #fef3c7; border-left: 4px solid #f59e0b; padding: 10px 12px; border-radius: 8px; font-size: 12px; line-height: 1.5; color: #92400e; }
        .advice-list { padding-left: 16px; margin: 0; }
        .advice-list li { margin-bottom: 3px; }
        .footer { background: #f8fafc; padding: 10px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #f1f5f9; font-weight: 600; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="agency"><i class="fa-solid fa-building-columns"></i> ĐÀI KHÍ TƯỢNG THỦY VĂN TỈNH THANH HÓA</div>
        <h1>{{ title }}</h1>
        <div class="meta"><i class="fa-solid fa-file-signature"></i> {{ doc_number }} &nbsp;|&nbsp; <i class="fa-solid fa-clock"></i> {{ issue_time }}</div>
    </div>
    <div class="content">
        <div class="stats-grid">
            {% for s in stats %}
            <div class="stat-card">
                <div class="stat-label">{{ s.label }}</div>
                <div class="stat-value">{{ s.value }}</div>
                {% if s.note %}<div class="stat-note">{{ s.note }}</div>{% endif %}
            </div>
            {% endfor %}
        </div>
        {% if affected_areas %}
        <div class="section-title"><i class="fa-solid fa-location-dot"></i> KHU VỰC / ĐỊA BÀN ẢNH HƯỞNG</div>
        <div class="area-tags">
            {% for area in affected_areas %}
            <span class="tag"><i class="fa-solid fa-location-arrow" style="color: var(--primary); font-size:9px;"></i> {{ area }}</span>
            {% endfor %}
        </div>
        {% endif %}
        {% if warnings %}
        <div class="section-title"><i class="fa-solid fa-triangle-exclamation"></i> TÁC ĐỘNG & KHUYẾN CÁO</div>
        <div class="advice-box">
            <ul class="advice-list">
                {% for w in warnings %}
                <li>{{ w }}</li>
                {% endfor %}
            </ul>
        </div>
        {% endif %}
    </div>
    <div class="footer">
        <i class="fa-solid fa-shield-halved"></i> CẤP ĐỘ RỦI RO THIÊN TAI: <span style="color: var(--primary);">{{ risk_level|default('CẤP 1') }}</span>
    </div>
</div>
</body>
</html>
"""

THEME_MAP = {
    "NANG_NONG": {"primary": "#e11d48", "header_bg": "linear-gradient(135deg, #f43f5e, #fb923c)", "card_bg": "#fff1f2"},
    "MUA_LON": {"primary": "#0284c7", "header_bg": "linear-gradient(135deg, #0284c7, #38bdf8)", "card_bg": "#f0f9ff"},
    "BAO": {"primary": "#7c3aed", "header_bg": "linear-gradient(135deg, #7c3aed, #db2777)", "card_bg": "#f5f3ff"},
    "LU_QUET": {"primary": "#ea580c", "header_bg": "linear-gradient(135deg, #ea580c, #facc15)", "card_bg": "#fff7ed"},
    "DONG_LOC": {"primary": "#d97706", "header_bg": "linear-gradient(135deg, #d97706, #7c3aed)", "card_bg": "#fffbeb"}
}

# ==================== BÓC TÁCH GEMINI AI (ĐÃ SỬA CHUẨN MODEL) ====================
def parse_pdf_bytes_with_ai(pdf_bytes):
    if not GEMINI_API_KEY:
        raise Exception("Chưa cấu hình GEMINI_API_KEY!")

    # Cấu hình API Key VÀ ÉP DÙNG API VERSION V1 CHÍNH THỨC (Chống lỗi 404 v1beta)
    genai.configure(
        api_key=GEMINI_API_KEY,
        client_options={'api_endpoint': 'generativelanguage.googleapis.com'}
    )

    raw_text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            raw_text += page.extract_text() or ""

    if not raw_text.strip():
        raise Exception("Không thể trích xuất chữ từ PDF!")

    # Cấu hình khởi tạo model chuẩn không bị dính prefix
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash'
    )
    
    prompt = f"""
    Bạn là chuyên gia Khí tượng Thủy văn. Hãy phân tích bản tin KTTV dưới đây và xuất ra DUY NHẤT 1 chuỗi JSON chuẩn.

    Cấu trúc JSON bắt buộc:
    {{
      "type": "NANG_NONG" | "MUA_LON" | "BAO" | "LU_QUET" | "DONG_LOC",
      "title": "TIÊU ĐỀ BẢN TIN (VIẾT HOA)",
      "doc_number": "Số hiệu bản tin",
      "issue_time": "Thời gian phát hành",
      "stats": [
         {{"label": "Tên chỉ số", "value": "Giá trị", "note": "Ghi chú ngắn"}}
      ],
      "affected_areas": ["Danh sách khu vực ngắn gọn"],
      "warnings": ["Khuyến cáo 1", "Khuyến cáo 2"],
      "risk_level": "Cấp độ rủi ro"
    }}

    Nội dung bản tin PDF:
    {raw_text}
    """

    res = model.generate_content(
        prompt, 
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(res.text)

async def render_html_to_png(data, output_path):
    bulletin_type = data.get("type", "NANG_NONG")
    theme = THEME_MAP.get(bulletin_type, THEME_MAP["NANG_NONG"])

    template = Template(HTML_TEMPLATE_STR)
    rendered_html = template.render(**data, theme=theme)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 580, "height": 850}, device_scale_factor=2)
        await page.set_content(rendered_html)
        await page.wait_for_timeout(400)
        card = await page.query_selector(".container")
        if card:
            await card.screenshot(path=output_path, type="png")
        else:
            await page.screenshot(path=output_path, type="png")
        await browser.close()

# ==================== CÁC HÀM XỬ LÝ IWEATHER & VNDMS (CŨ) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {**HEADERS_DEFAULT, 'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX', 'Accept': 'application/json'}
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}"}
        matches = re.findall(r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)', res.text, re.IGNORECASE)
        unique_locs = list(set([m.strip(' ",') for m in matches]))
        alerts = [{"location": loc, "intensity": "Mây dông/Sét phát triển", "time": now_vn.strftime('%H:%M %d/%m/%Y')} for loc in unique_locs]
        return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}
    except Exception as e: return {"status": "error", "message": str(e)}

def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}"}
        data = res.json()
        alerts = []
        if isinstance(data, list):
            for item in data:
                alerts.append({
                    "id": item.get("Id") or item.get("Code") or str(hash(str(item))),
                    "title": item.get("DisasterName") or item.get("Name") or "CẢNH BÁO THIÊN TAI NGUY HIỂM",
                    "risk_level": item.get("RiskLevel", "Đang cập nhật"),
                    "start_time": item.get("StartDate", "Chưa xác định"),
                    "description": item.get("Description") or item.get("Note") or "Chưa có thông tin chi tiết."
                })
        return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}
    except Exception as e: return {"status": "error", "message": str(e)}

def format_iweather_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"⚡ **[RADAR DÔNG SÉT - IWEATHER]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n✅ **AN TOÀN:** Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    header = "⚠️ **[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]**" if is_auto else "⚡ **[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]**"
    msg = f"{header}\n🕒 *Thời gian quét:* `{data['updated_at']}`\n📡 *Tổng số vùng:* **{data['count']} khu vực**\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1): msg += f"🌩️ **{idx}.** {alert['location']}\n"
    msg += "───────────────────\n🌐 [Mở bản đồ Radar CMAX](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"🏛️ **[GIÁM SÁT THIÊN TAI - VNDMS]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n🟢 **KHÔNG CÓ CẢNH BÁO NÓNG:** Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."
    header = "🚨 **[CẢNH BÁO KHẨN CẤP TỪ VNDMS]**" if is_auto else "🏛️ **[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]**"
    msg = f"{header}\n🕒 *Cập nhật:* `{data['updated_at']}`\n📋 *Số bản tin:* **{data['count']} tin**\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 **BẢN TIN {idx}: {alert['title'].upper()}**\n⏱ **Bắt đầu:** {alert['start_time']}\n⚠️ **Cấp độ rủi ro:** `{alert['risk_level']}`\n📝 **Nội dung:** {alert['description']}\n▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    msg += "🌐 *Nguồn:* Cục QLĐĐ & PCTT (vndms.gov.vn)"
    return msg

def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    try: requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=5)
    except Exception as e: print(e)

def send_telegram_photo(chat_id, photo_path, caption=""):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo_file:
            requests.post(url, data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}, files={"photo": photo_file}, timeout=30)
    except Exception as e: print(e)

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS: send_telegram_message(chat_id, text)

# ==================== ROUTE CHẠY CRON TỰ ĐỘNG KHÔNG MẤT ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            broadcast_alert(format_iweather_message(iweather_data, is_auto=True))
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = [a for a in vndms_data['alerts'] if a['id'] not in SENT_VNDMS_IDS]
        if new_alerts:
            for a in new_alerts: SENT_VNDMS_IDS.add(a['id'])
            v_copy = dict(vndms_data)
            v_copy['alerts'] = new_alerts
            v_copy['count'] = len(new_alerts)
            broadcast_alert(format_vndms_message(v_copy, is_auto=True))

    return jsonify({"status": "running", "active_chats": list(REGISTERED_CHATS)})

# ==================== TELEGRAM WEBHOOK ====================
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        REGISTERED_CHATS.add(chat_id)

        # 1. Nếu gửi File PDF
        if "document" in message and message["document"].get("mime_type") == "application/pdf":
            send_telegram_message(chat_id, "📥 *Đã nhận PDF.* Đang bóc tách & tạo Infographic 2K...")
            try:
                file_id = message["document"]["file_id"]
                file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}").json()
                if file_info.get("ok"):
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info['result']['file_path']}"
                    pdf_bytes = requests.get(download_url).content
                    
                    extracted_data = parse_pdf_bytes_with_ai(pdf_bytes)
                    output_png_path = f"infographic_{chat_id}.png"
                    asyncio.run(render_html_to_png(extracted_data, output_png_path))
                    
                    caption = f"🎨 **{extracted_data.get('title')}**\n📋 Số: `{extracted_data.get('doc_number')}`"
                    send_telegram_photo(chat_id, output_png_path, caption=caption)
                    if os.path.exists(output_png_path): os.remove(output_png_path)
            except Exception as e:
                send_telegram_message(chat_id, f"❌ Lỗi xử lý bản tin: {str(e)}")

        # 2. Các câu lệnh tra cứu văn bản
        elif text.startswith("/start") or text.startswith("/dong") or text.startswith("/canhbao") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "🔍 *Đang quét dữ liệu iWeather & VNDMS...*")
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))
            
            vndms_data = get_vndms_warning()
            send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
