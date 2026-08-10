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

# ==================== CẤU HÌNH HỆ THỐNG ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Gemini API Key lấy từ Environment Variable trên Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# Lưu danh sách Chat ID nhận thông báo tự động (RAM)
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()

# ==================== TEMPLATE HTML & THEMES ====================
HTML_TEMPLATE_STR = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <style>
        :root {
            --primary-color: {{ theme.primary }};
            --header-bg: {{ theme.header_bg }};
            --card-bg: {{ theme.card_bg }};
            --badge-bg: {{ theme.badge_bg }};
        }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 15px; display: flex; justify-content: center; }
        .container { width: 500px; background: #ffffff; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.15); overflow: hidden; border: 2px solid var(--primary-color); }
        .header { background: var(--header-bg); color: white; padding: 18px; text-align: center; }
        .header .sub { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; opacity: 0.95; }
        .header h1 { margin: 6px 0; font-size: 20px; font-weight: 800; text-shadow: 0 2px 4px rgba(0,0,0,0.2); }
        .header .meta { font-size: 11px; opacity: 0.85; font-style: italic; }
        
        .content { padding: 18px; color: #2c3e50; }
        .stats-grid { display: grid; grid-template-columns: repeat({{ stats|length }}, 1fr); gap: 8px; margin-bottom: 16px; }
        .stat-card { background: var(--card-bg); border-left: 4px solid var(--primary-color); padding: 8px; border-radius: 8px; text-align: center; }
        .stat-label { font-size: 10px; color: #666; font-weight: bold; text-transform: uppercase; }
        .stat-value { font-size: 17px; color: var(--primary-color); font-weight: 800; margin: 2px 0; }
        .stat-note { font-size: 10px; color: #888; }

        .section-title { font-size: 12px; font-weight: 700; color: var(--primary-color); border-bottom: 2px solid var(--card-bg); padding-bottom: 4px; margin: 12px 0 8px 0; text-transform: uppercase; }
        .area-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }
        .tag { background: var(--badge-bg); color: #333; font-size: 11px; padding: 4px 8px; border-radius: 10px; font-weight: 600; }

        .advice-box { background: #fff9db; border-left: 4px solid #f59f00; padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.4; }
        .advice-list { padding-left: 16px; margin: 4px 0 0 0; }
        .advice-list li { margin-bottom: 3px; }

        .footer { background: #f8f9fa; padding: 8px; text-align: center; font-size: 10px; color: #666; border-top: 1px solid #eee; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="sub">{{ agency|default('ĐÀI KHÍ TƯỢNG THỦY VĂN TỈNH THANH HÓA') }}</div>
        <h1>{{ title }}</h1>
        <div class="meta">Số: {{ doc_number }} | {{ issue_time }}</div>
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
        <div class="section-title">📍 KHU VỰC / ĐỊA BÀN ẢNH HƯỞNG</div>
        <div class="area-tags">
            {% for area in affected_areas %}
            <span class="tag">📍 {{ area }}</span>
            {% endfor %}
        </div>
        {% endif %}

        {% if warnings %}
        <div class="section-title">⚠️ TÁC ĐỘNG & KHUYẾN CÁO</div>
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
        Rủi ro thiên tai: <b>{{ risk_level|default('CẤP 1') }}</b> | Render tự động bởi Telegram Bot
    </div>
</div>
</body>
</html>
"""

THEME_MAP = {
    "NANG_NONG": {"primary": "#d9381e", "header_bg": "linear-gradient(135deg, #ff4e50, #f9d423)", "card_bg": "#fff0f0", "badge_bg": "#ffe3e3"},
    "MUA_LON": {"primary": "#0288d1", "header_bg": "linear-gradient(135deg, #00c6ff, #0072ff)", "card_bg": "#e1f5fe", "badge_bg": "#b3e5fc"},
    "BAO": {"primary": "#7b1fa2", "header_bg": "linear-gradient(135deg, #8e24aa, #ff1744)", "card_bg": "#f3e5f5", "badge_bg": "#e1bee7"},
    "LU_QUET": {"primary": "#e65100", "header_bg": "linear-gradient(135deg, #f7971e, #ffd200)", "card_bg": "#fff3e0", "badge_bg": "#ffe0b2"},
    "DONG_LOC": {"primary": "#f57f17", "header_bg": "linear-gradient(135deg, #fbc02d, #7b1fa2)", "card_bg": "#fffde7", "badge_bg": "#fff9c4"}
}

# ==================== BÓC TÁCH DỮ LIỆU PDF VÀ RENDER PNG ====================
def parse_pdf_bytes_with_ai(pdf_bytes):
    # 1. Trích xuất text từ PDF bằng pdfplumber
    raw_text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            raw_text += page.extract_text() or ""

    # 2. Thử bóc tách bằng Gemini AI (Ưu tiên gemini-1.5-flash & gemini-1.5-pro)
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            prompt = f"""
            Bạn là chuyên gia Khí tượng Thủy văn. Hãy phân tích bản tin KTTV dưới đây và trả về DUY NHẤT một chuỗi JSON chuẩn.
            
            Cấu trúc JSON bắt buộc:
            {{
              "type": "NANG_NONG" | "MUA_LON" | "BAO" | "LU_QUET" | "DONG_LOC",
              "title": "TIÊU ĐỀ BẢN TIN (VIẾT HOA)",
              "doc_number": "Số hiệu bản tin",
              "issue_time": "Thời gian phát hành",
              "stats": [
                 {{"label": "Tên chỉ số", "value": "Giá trị", "note": "Ghi chú ngắn"}}
              ],
              "affected_areas": ["Danh sách khu vực/huyện/trạm"],
              "warnings": ["Khuyên cáo 1", "Khuyên cáo 2"],
              "risk_level": "Cấp độ rủi ro"
            }}

            Nội dung bản tin PDF:
            {raw_text}
            """

            # Lần lượt thử các model ổn định nhất
            candidate_models = ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-pro']
            for model_name in candidate_models:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        prompt,
                        generation_config={"response_mime_type": "application/json"}
                    )
                    cleaned_json = response.text.replace("```json", "").replace("```", "").strip()
                    return json.loads(cleaned_json)
                except Exception as m_err:
                    print(f"Model {model_name} chưa sẵn sàng: {m_err}")
                    continue
        except Exception as ai_err:
            print(f"Lỗi gọi AI: {ai_err}")

    # 3. Chế độ dự phòng (Fallback) nếu AI lỗi hoặc chưa cài Key
    return {
        "type": "NANG_NONG" if "NẮNG NÓNG" in raw_text.upper() else "MUA_LON",
        "title": "BẢN TIN CẢNH BÁO THỜI TIẾT",
        "doc_number": "KTTV-THANHHOA",
        "issue_time": datetime.now().strftime("%H:%M %d/%m/%Y"),
        "stats": [
            {"label": "Trạng thái", "value": "Đã ghi nhận", "note": "Xem chi tiết trong file PDF"}
        ],
        "affected_areas": ["Địa bàn tỉnh Thanh Hóa"],
        "warnings": ["Theo dõi diễn biến thời tiết trong các bản tin tiếp theo."],
        "risk_level": "CẤP 1"
    }

async def render_html_to_png(data, output_path):
    bulletin_type = data.get("type", "NANG_NONG")
    theme = THEME_MAP.get(bulletin_type, THEME_MAP["NANG_NONG"])

    template = Template(HTML_TEMPLATE_STR)
    rendered_html = template.render(**data, theme=theme)

    async with async_playwright() as p:
        # Bổ sung args --no-sandbox để chạy mượt trên Linux Server
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page(viewport={"width": 550, "height": 850})
        await page.set_content(rendered_html)
        card = await page.query_selector(".container")
        if card:
            await card.screenshot(path=output_path, type="png")
        else:
            await page.screenshot(path=output_path, type="png")
        await browser.close()

# ==================== 1. IWEATHER (DÔNG SÉT) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        **HEADERS_DEFAULT,
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX',
        'Accept': 'application/json, text/plain, */*'
    }
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được iWeather (HTTP {res.status_code})"}

        raw_text = res.text
        matched_alerts = []
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, raw_text, re.IGNORECASE)
        unique_locations = list(set([m.strip(' ",') for m in matches]))

        for loc in unique_locations:
            matched_alerts.append({
                "location": loc,
                "intensity": "Mây dông / Sét phát triển",
                "time": now_vn.strftime('%H:%M %d/%m/%Y')
            })

        return {
            "status": "success",
            "province": province_keyword,
            "has_warning": len(matched_alerts) > 0,
            "count": len(matched_alerts),
            "alerts": matched_alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": f"Lỗi iWeather: {str(e)}"}

# ==================== 2. VNDMS (GIÁM SÁT THIÊN TAI) ====================
def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được VNDMS (HTTP {res.status_code})"}

        data = res.json()
        alerts = []
        if isinstance(data, list) and len(data) > 0:
            for item in data:
                alerts.append({
                    "id": item.get("Id") or item.get("Code") or str(hash(str(item))),
                    "title": item.get("DisasterName") or item.get("Name") or "CẢNH BÁO THIÊN TAI NGUY HIỂM",
                    "risk_level": item.get("RiskLevel", "Đang cập nhật"),
                    "start_time": item.get("StartDate", "Chưa xác định"),
                    "description": item.get("Description") or item.get("Note") or "Chưa có thông tin chi tiết."
                })

        return {
            "status": "success",
            "has_warning": len(alerts) > 0,
            "count": len(alerts),
            "alerts": alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": f"Lỗi VNDMS: {str(e)}"}

# ==================== FORMAT CÁC DẠNG TIN NHẮN TELEGRAM ====================
def format_iweather_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"⚡ **[RADAR DÔNG SÉT - IWEATHER]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n✅ **AN TOÀN:** Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    
    header = "⚠️ **[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]**" if is_auto else "⚡ **[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]**"
    msg = f"{header}\n"
    msg += f"🕒 *Thời gian quét:* `{data['updated_at']}`\n"
    msg += f"📡 *Tổng số vùng phát hiện:* **{data['count']} khu vực**\n"
    msg += "───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🌩️ **{idx}.** {alert['location']}\n"
    msg += "───────────────────\n"
    msg += "🌐 [Mở bản đồ Radar CMAX](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"🏛️ **[GIÁM SÁT THIÊN TAI - VNDMS]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n🟢 **KHÔNG CÓ CẢNH BÁO NÓNG:** Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."

    header = "🚨 **[CẢNH BÁO KHẨN CẤP TỪ VNDMS]**" if is_auto else "🏛️ **[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]**"
    msg = f"{header}\n"
    msg += f"🕒 *Cập nhật:* `{data['updated_at']}`\n"
    msg += f"📋 *Số bản tin hiện tại:* **{data['count']} tin**\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 **BẢN TIN {idx}: {alert['title'].upper()}**\n"
        msg += f"⏱ **Bắt đầu:** {alert['start_time']}\n"
        msg += f"⚠️ **Cấp độ rủi ro:** `{alert['risk_level']}`\n"
        msg += f"📝 **Nội dung:** {alert['description']}\n"
        msg += "▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    msg += "🌐 *Nguồn:* Cục QLĐĐ & PCTT (vndms.gov.vn)"
    return msg

def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def send_telegram_photo(chat_id, photo_path, caption=""):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo_file:
            payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
            files = {"photo": photo_file}
            requests.post(url, data=payload, files=files, timeout=30)
    except Exception as e:
        print(f"Lỗi gửi ảnh Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

# ==================== WEB ROUTES & WEBHOOK ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            msg_iweather = format_iweather_message(iweather_data, is_auto=True)
            broadcast_alert(msg_iweather)
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = [a for a in vndms_data['alerts'] if a['id'] not in SENT_VNDMS_IDS]
        for a in new_alerts:
            SENT_VNDMS_IDS.add(a['id'])
        if new_alerts:
            vndms_data_copy = dict(vndms_data)
            vndms_data_copy['alerts'] = new_alerts
            vndms_data_copy['count'] = len(new_alerts)
            msg_vndms = format_vndms_message(vndms_data_copy, is_auto=True)
            broadcast_alert(msg_vndms)

    return jsonify({"status": "running", "active_chats": list(REGISTERED_CHATS)})

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        REGISTERED_CHATS.add(chat_id)

        # 1. Nhận file PDF bản tin
        if "document" in message and message["document"].get("mime_type") == "application/pdf":
            send_telegram_message(chat_id, "📥 *Đã nhận bản tin PDF.* Đang bóc tách dữ liệu & vẽ Infographic...")
            try:
                file_id = message["document"]["file_id"]
                file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}").json()
                if file_info.get("ok"):
                    file_path_str = file_info["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path_str}"
                    pdf_bytes = requests.get(download_url).content

                    extracted_data = parse_pdf_bytes_with_ai(pdf_bytes)
                    output_png_path = f"infographic_{chat_id}.png"
                    asyncio.run(render_html_to_png(extracted_data, output_png_path))

                    caption = f"🎨 **INFOGRAPHIC {extracted_data.get('title')}**\n📋 Số hiệu: `{extracted_data.get('doc_number')}`"
                    send_telegram_photo(chat_id, output_png_path, caption=caption)

                    if os.path.exists(output_png_path):
                        os.remove(output_png_path)
            except Exception as e:
                send_telegram_message(chat_id, f"❌ Lỗi xử lý file PDF: {str(e)}")

        # 2. Lệnh tra cứu dông sét & thiên tai
        elif text.startswith("/start") or text.startswith("/dong") or text.startswith("/canhbao") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "🔍 *Đang truy vấn dữ liệu từ iWeather & VNDMS...*")
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            msg_iweather = format_iweather_message(iweather_data, is_auto=False) if iweather_data.get("status") == "success" else f"❌ Lỗi iWeather: {iweather_data.get('message')}"
            send_telegram_message(chat_id, msg_iweather)

            vndms_data = get_vndms_warning()
            msg_vndms = format_vndms_message(vndms_data, is_auto=False) if vndms_data.get("status") == "success" else f"❌ Lỗi VNDMS: {vndms_data.get('message')}"
            send_telegram_message(chat_id, msg_vndms)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
