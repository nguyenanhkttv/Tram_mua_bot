import os
import re
import json
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from html2image import Html2Image

# ==================== CẤU HÌNH GLOBAL ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
KTTV_URLS = [
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43",
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

# Quản lý lưu trữ Chat ID bền vững bằng File JSON
CHAT_FILE = "registered_chats.json"

def load_chats():
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_chat(chat_id):
    chats = load_chats()
    if chat_id not in chats:
        chats.add(chat_id)
        with open(CHAT_FILE, "w") as f:
            json.dump(list(chats), f)

LAST_ALERT_COUNT = 0
PROCESSED_NEWS_URLS = set()

# ==================== 1. TẠO INFOGRAPHIC HTML & RENDER ẢNH ====================
def generate_infographic_html(data):
    """Tạo HTML Infographic phong cách Glassmorphism Neon"""
    is_thuy_van = "thuy-van" in data.get('type', '')
    category_badge = "CẢNH BÁO THỦY VĂN" if is_thuy_van else "CẢNH BÁO THỜI TIẾT"
    bg_gradient = "linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%)" if is_thuy_van else "linear-gradient(135deg, #1e130c 0%, #9a400e 50%, #e65c00 100%)"
    header_icon = "🌊" if is_thuy_van else "⚡"
    
    title = data.get('title', 'BẢN TIN CẢNH BÁO KTTV THANH HÓA')
    date = data.get('date', datetime.now().strftime("%H:%M %d/%m/%Y"))
    risk_level = data.get('risk_level', 'Cấp 1 - 2. Cần chú ý theo dõi sát diễn biến.')
    affected_area = data.get('affected_area', 'Địa bàn tỉnh Thanh Hóa.')
    summary = data.get('summary', 'Chi tiết xem tại cổng thông tin Đài KTTV Thanh Hóa.')

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Roboto, sans-serif; }}
        body {{ width: 800px; background: {bg_gradient}; color: #ffffff; padding: 25px; border-radius: 16px; }}
        .card {{
            background: rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 16px;
            padding: 25px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }}
        .header {{ display: flex; align-items: center; border-bottom: 2px solid rgba(255, 255, 255, 0.2); padding-bottom: 15px; margin-bottom: 20px; }}
        .header .icon {{ font-size: 45px; margin-right: 15px; }}
        .header .badge {{
            background: #ff4757; color: #fff; padding: 4px 12px; border-radius: 20px;
            font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; display: inline-block; margin-bottom: 5px;
        }}
        .header h1 {{ font-size: 20px; color: #ffffff; line-height: 1.3; }}
        .time-tag {{ font-size: 12px; color: #dfe4ea; margin-top: 5px; }}
        .grid-info {{ display: flex; gap: 15px; margin-bottom: 20px; }}
        .info-box {{
            flex: 1; background: rgba(0, 0, 0, 0.25); padding: 15px; border-radius: 12px; border-left: 4px solid #eccc68;
        }}
        .info-box.danger {{ border-left-color: #ff4757; }}
        .info-box.water {{ border-left-color: #70a1ff; }}
        .info-box h3 {{ font-size: 12px; color: #eccc68; margin-bottom: 6px; text-transform: uppercase; }}
        .info-box p {{ font-size: 13px; line-height: 1.4; color: #f1f2f6; }}
        .content-body {{
            background: rgba(0, 0, 0, 0.2); padding: 18px; border-radius: 12px;
            font-size: 14px; line-height: 1.6; margin-bottom: 20px; border: 1px dashed rgba(255, 255, 255, 0.2);
        }}
        .footer {{
            display: flex; justify-content: space-between; align-items: center;
            font-size: 12px; color: #a4b0be; border-top: 1px solid rgba(255, 255, 255, 0.1); padding-top: 15px;
        }}
        .footer .source {{ font-weight: bold; color: #70a1ff; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="icon">{header_icon}</div>
            <div>
                <span class="badge">{category_badge}</span>
                <h1>{title}</h1>
                <div class="time-tag">📅 Cập nhật: {date} | Đài KTTV Tỉnh Thanh Hóa</div>
            </div>
        </div>
        <div class="grid-info">
            <div class="info-box danger">
                <h3>⚠️ Cấp độ Rủi ro</h3>
                <p>{risk_level}</p>
            </div>
            <div class="info-box water">
                <h3>📍 Khu vực ảnh hưởng</h3>
                <p>{affected_area}</p>
            </div>
        </div>
        <div class="content-body">
            <strong style="color: #ff7f50;">📌 NỘI DUNG TÓM TẮT DỰ BÁO:</strong><br><br>
            {summary}
        </div>
        <div class="footer">
            <span>🤖 Telegram Weather Bot System</span>
            <span class="source">🌐 kttv.thanhhoa.gov.vn</span>
        </div>
    </div>
</body>
</html>"""

def render_infographic_image(html_code, filename="infographic.png"):
    """Render HTML sang file ảnh PNG với Flag tương thích Server Headless"""
    try:
        # Cấu hình cờ Chạy ẩn (Headless) cho Linux/VPS
        custom_flags = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        hti = Html2Image(custom_flags=custom_flags)
        
        temp_html = "temp_render.html"
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        hti.screenshot(html_file=temp_html, save_as=filename, size=(820, 720))
        return filename
    except Exception as e:
        print(f"Lỗi render ảnh: {e}")
        return None

# ==================== 2. CÀO BẢN TIN KTTV THANH HÓA ====================
def scrape_kttv_thanhhoa():
    articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for url in KTTV_URLS:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                links = soup.find_all('a', href=True)
                for a in links:
                    href = a['href']
                    title = a.get_text(strip=True)
                    if title and len(title) > 18 and ('tin-tuc' in href or 'chi-tiet' in href or '/43' in href or '/46' in href):
                        full_url = href if href.startswith('http') else f"https://kttv.thanhhoa.gov.vn{href}"
                        articles.append({
                            'title': title,
                            'url': full_url,
                            'type': 'thuy-van' if 'thuy-van' in url else 'thoi-tiet'
                        })
        except Exception as e:
            print(f"Lỗi cào dữ liệu từ {url}: {e}")
            
    return articles

# ==================== 3. XỬ LÝ DỮ LIỆU IWEATHER ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'
    }
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"HTTP {res.status_code}"}

        raw_text = res.text
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, raw_text, re.IGNORECASE)
        unique_locations = list(set([m.strip(' ",') for m in matches]))

        matched_alerts = [{
            "location": loc,
            "intensity": "Mây dông / Sét phát triển",
            "time": now_vn.strftime('%H:%M %d/%m/%Y')
        } for loc in unique_locations]

        return {
            "status": "success",
            "has_warning": len(matched_alerts) > 0,
            "count": len(matched_alerts),
            "alerts": matched_alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== 4. GỬI TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi tin Text: {e}")

def send_telegram_photo(chat_id, photo_path, caption=""):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, data={'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}, files={'photo': photo}, timeout=15)
    except Exception as e:
        print(f"Lỗi gửi ảnh Telegram: {e}")

def broadcast_alert(text=None, photo_path=None, caption=""):
    registered_chats = load_chats()
    for chat_id in registered_chats:
        if text:
            send_telegram_message(chat_id, text)
        if photo_path:
            send_telegram_photo(chat_id, photo_path, caption)

# ==================== 5. ROUTES ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, PROCESSED_NEWS_URLS
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    
    # --- A. Kiểm tra Dông Sét iWeather ---
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚠️ **CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!**\n"
            msg += f"🕒 *Thời gian:* {iweather_data['updated_at']}\n"
            msg += f"📍 **Số khu vực phát hiện:** {current_count}\n"
            for idx, alert in enumerate(iweather_data['alerts'], 1):
                msg += f"\n**{idx}.** {alert['location']}"
            msg += "\n\n🌐 Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            broadcast_alert(text=msg)
            LAST_ALERT_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # --- B. Quét Bản tin mới & Bắn Infographic ---
    articles = scrape_kttv_thanhhoa()
    for art in articles[:2]:
        if art['url'] not in PROCESSED_NEWS_URLS:
            PROCESSED_NEWS_URLS.add(art['url'])
            
            info_data = {
                'title': art['title'],
                'type': art['type'],
                'date': now_vn,
                'risk_level': 'Cảnh báo cấp 1 - 2. Mưa dông, lốc, sét & dâng trào thủy văn.',
                'affected_area': 'Địa bàn tỉnh Thanh Hóa (Đặc biệt vùng núi & ven biển).',
                'summary': f"Bản tin chi tiết được cập nhật từ Đài KTTV Thanh Hóa.<br>Đường dẫn: <a href='{art['url']}' style='color:#70a1ff;'>{art['url']}</a>"
            }
            
            html_code = generate_infographic_html(info_data)
            img_path = render_infographic_image(html_code, "news_infographic.png")
            
            if img_path and os.path.exists(img_path):
                caption = f"🚨 <b>{art['title']}</b>\n🔗 <a href='{art['url']}'>Xem chi tiết bản tin</a>"
                broadcast_alert(photo_path=img_path, caption=caption)

    return jsonify({
        "status": "running",
        "active_chats_count": len(load_chats()),
        "last_alert_count": LAST_ALERT_COUNT,
        "processed_news_count": len(PROCESSED_NEWS_URLS)
    })

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # Lưu Chat ID vào file
        save_chat(chat_id)

        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/dongset"):
            send_telegram_message(chat_id, "⚡ Đang kiểm tra Dông Sét & cào bản tin mới nhất...")
            
            # 1. Trả về dông sét
            dw = get_iweather_storm_warning("Thanh Hóa")
            if dw.get("status") == "success":
                if dw.get("has_warning"):
                    msg = f"🚨 **CẢNH BÁO DÔNG SÉT TẠI THANH HÓA!**\n🕒 *Cập nhật:* {dw['updated_at']}\n"
                    msg += f"📍 **Phát hiện {dw['count']} khu vực:**\n"
                    for idx, alert in enumerate(dw['alerts'], 1):
                        msg += f"\n**{idx}.** {alert['location']}"
                else:
                    msg = f"✅ **AN TOÀN ({dw['updated_at']}):** Chưa phát hiện mây dông sét tại Thanh Hóa."
                send_telegram_message(chat_id, msg)

            # 2. Gửi Infographic bản tin mới nhất
            articles = scrape_kttv_thanhhoa()
            if articles:
                top_art = articles[0]
                now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
                info_data = {
                    'title': top_art['title'],
                    'type': top_art['type'],
                    'date': now_vn,
                    'risk_level': 'Cảnh báo rủi ro thiên tai do mưa lớn, lốc, sét & ngập lụt.',
                    'affected_area': 'Địa bàn tỉnh Thanh Hóa.',
                    'summary': f"Bản tin chi tiết xem tại: {top_art['url']}"
                }
                html_code = generate_infographic_html(info_data)
                img_path = render_infographic_image(html_code, "user_req_infographic.png")
                if img_path:
                    send_telegram_photo(chat_id, img_path, caption=f"📰 <b>BẢN TIN MỚI NHẤT:</b>\n{top_art['title']}")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
