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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

app = Flask(__name__)

REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()

# ==================== TEMPLATE HTML CAO CẤP (FONTAWESOME + NET 2K) ====================
HTML_TEMPLATE_STR = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <!-- Nạp FontChữ & FontAwesome Icon từ CDN để không bị lỗi ô vuông trên Linux -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: {{ theme.primary }};
            --gradient: {{ theme.header_bg }};
            --card-bg: {{ theme.card_bg }};
        }
        * { box-sizing: border-box; }
        body {
            font-family: 'Montserrat', sans-serif;
            background: #f4f6f9;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
        }
        .container {
            width: 540px;
            background: #ffffff;
            border-radius: 20px;
            box-shadow: 0 15px 35px rgba(0,0,0,0.12);
            overflow: hidden;
            border: 2px solid rgba(0,0,0,0.05);
        }
        .header {
            background: var(--gradient);
            color: white;
            padding: 24px 20px;
            text-align: center;
            position: relative;
        }
        .header .agency {
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            opacity: 0.9;
            margin-bottom: 6px;
        }
        .header h1 {
            margin: 0;
            font-size: 22px;
            font-weight: 900;
            text-transform: uppercase;
            line-height: 1.3;
            text-shadow: 0 2px 4px rgba(0,0,0,0.15);
        }
        .header .meta {
            margin-top: 8px;
            font-size: 11px;
            opacity: 0.85;
            font-weight: 600;
        }
        
        .content { padding: 22px; color: #1e293b; }
        
        /* Grid thẻ chỉ số */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat({{ stats|length }}, 1fr);
            gap: 10px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: var(--card-bg);
            border-left: 4px solid var(--primary);
            padding: 12px 10px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 2px 6px rgba(0,0,0,0.03);
        }
        .stat-label {
            font-size: 10px;
            color: #64748b;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .stat-value {
            font-size: 18px;
            color: var(--primary);
            font-weight: 800;
        }
        .stat-note { font-size: 10px; color: #94a3b8; margin-top: 2px; }

        .section-title {
            font-size: 12px;
            font-weight: 800;
            color: var(--primary);
            margin: 16px 0 10px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* Badge khu vực */
        .area-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
        .tag {
            background: #f1f5f9;
            color: #334155;
            font-size: 11px;
            padding: 5px 10px;
            border-radius: 8px;
            font-weight: 600;
            border: 1px solid #e2e8f0;
            display: flex;
            align-items: center;
            gap: 4px;
        }

        /* Khuyến cáo an toàn */
        .advice-box {
            background: #fffbeb;
            border: 1px solid #fef3c7;
            border-left: 4px solid #f59e0b;
            padding: 12px 14px;
            border-radius: 10px;
            font-size: 12px;
            line-height: 1.6;
            color: #92400e;
        }
        .advice-list { padding-left: 18px; margin: 0; }
        .advice-list li { margin-bottom: 4px; }

        .footer {
            background: #f8fafc;
            padding: 12px;
            text-align: center;
            font-size: 11px;
            color: #64748b;
            border-top: 1px solid #f1f5f9;
            font-weight: 600;
        }
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
        <!-- Chỉ số -->
        <div class="stats-grid">
            {% for s in stats %}
            <div class="stat-card">
                <div class="stat-label">{{ s.label }}</div>
                <div class="stat-value">{{ s.value }}</div>
                {% if s.note %}<div class="stat-note">{{ s.note }}</div>{% endif %}
            </div>
            {% endfor %}
        </div>

        <!-- Vùng ảnh hưởng -->
        {% if affected_areas %}
        <div class="section-title"><i class="fa-solid fa-location-dot"></i> KHU VỰC / ĐỊA BÀN ẢNH HƯỞNG</div>
        <div class="area-tags">
            {% for area in affected_areas %}
            <span class="tag"><i class="fa-solid fa-location-arrow" style="color: var(--primary); font-size:9px;"></i> {{ area }}</span>
            {% endfor %}
        </div>
        {% endif %}

        <!-- Khuyến cáo -->
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

# ==================== BÓC TÁCH DỮ LIỆU PDF ====================
def parse_pdf_bytes_with_ai(pdf_bytes):
    """Bóc tách chính xác bằng Gemini AI"""
    if not GEMINI_API_KEY:
        raise Exception("Chưa cấu hình GEMINI_API_KEY!")

    genai.configure(api_key=GEMINI_API_KEY)

    # Đọc text từ PDF
    raw_text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            raw_text += page.extract_text() or ""

    if not raw_text.strip():
        raise Exception("File PDF không chứa dữ liệu văn bản!")

    # Tự động chọn Model khả dụng
    target_model = "gemini-1.5-flash"
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in models:
            if 'flash' in m:
                target_model = m
                break
    except Exception:
        pass

    model = genai.GenerativeModel(target_model)
    prompt = f"""
    Bạn là chuyên gia Khí tượng Thủy văn. Hãy phân tích bản tin KTTV dưới đây và xuất ra DUY NHẤT 1 chuỗi JSON chuẩn.

    Cấu trúc JSON bắt buộc:
    {{
      "type": "NANG_NONG" | "MUA_LON" | "BAO" | "LU_QUET" | "DONG_LOC",
      "title": "TIÊU ĐỀ BẢN TIN (VIẾT HOA GỌN GÀNG)",
      "doc_number": "Số hiệu bản tin",
      "issue_time": "Thời gian phát hành",
      "stats": [
         {{"label": "Tên chỉ số", "value": "Giá trị", "note": "Ghi chú ngắn"}}
      ],
      "affected_areas": ["Tên các huyện/thành phố/trạm ngắn gọn"],
      "warnings": ["Khuyến cáo 1", "Khuyến cáo 2"],
      "risk_level": "Cấp độ rủi ro"
    }}

    Nội dung PDF:
    {raw_text}
    """

    res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    return json.loads(res.text)

# ==================== CHỤP ẢNH PNG NÉT 2K ====================
async def render_html_to_png(data, output_path):
    bulletin_type = data.get("type", "NANG_NONG")
    theme = THEME_MAP.get(bulletin_type, THEME_MAP["NANG_NONG"])

    template = Template(HTML_TEMPLATE_STR)
    rendered_html = template.render(**data, theme=theme)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # Nâng nét gấp 2 lần bằng device_scale_factor=2
        page = await browser.new_page(
            viewport={"width": 600, "height": 900},
            device_scale_factor=2
        )
        await page.set_content(rendered_html)
        
        # Đợi icon FontAwesome nạp xong
        await page.wait_for_timeout(500)
        
        card = await page.query_selector(".container")
        if card:
            await card.screenshot(path=output_path, type="png")
        else:
            await page.screenshot(path=output_path, type="png")
        await browser.close()

# ==================== API WEBHOCK & LOGIC KHÁC ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print(e)

def send_telegram_photo(chat_id, photo_path, caption=""):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo_file:
            payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
            files = {"photo": photo_file}
            requests.post(url, data=payload, files=files, timeout=30)
    except Exception as e: print(e)

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]

        if "document" in message and message["document"].get("mime_type") == "application/pdf":
            send_telegram_message(chat_id, "📥 *Đã nhận PDF.* Đang phân tích dữ liệu & khởi tạo Infographic 2K...")
            
            try:
                file_id = message["document"]["file_id"]
                file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}").json()
                
                if file_info.get("ok"):
                    file_path_str = file_info["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path_str}"
                    pdf_bytes = requests.get(download_url).content
                    
                    # 1. Trích xuất bằng AI
                    extracted_data = parse_pdf_bytes_with_ai(pdf_bytes)
                    
                    # 2. Render ảnh nét 2K
                    output_png_path = f"infographic_{chat_id}.png"
                    asyncio.run(render_html_to_png(extracted_data, output_png_path))
                    
                    # 3. Phản hồi
                    caption = f"🎨 **{extracted_data.get('title')}**\n📋 Số: `{extracted_data.get('doc_number')}`"
                    send_telegram_photo(chat_id, output_png_path, caption=caption)
                    
                    if os.path.exists(output_png_path):
                        os.remove(output_png_path)
            
            except Exception as e:
                send_telegram_message(chat_id, f"❌ Lỗi xử lý bản tin: {str(e)}")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
