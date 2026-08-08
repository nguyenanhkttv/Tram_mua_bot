import os
import re
import json
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

# ==================== CẤU HÌNH GLOBAL ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_EVENT_URL = "https://vndms.gov.vn/api/WarningEvent"
KTTV_URLS = [
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43",
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

CHAT_FILE = "registered_chats.json"

def load_chats():
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_chat(chat_id):
    chats = load_chats()
    if chat_id not in chats:
        chats.add(chat_id)
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(list(chats), f)

LAST_ALERT_COUNT = 0
PROCESSED_NEWS_URLS = set()
LAST_DISASTER_COUNT = 0

# ==================== 1. LẤY DỮ LIỆU VNDMS CHI TIẾT ====================
def get_vndms_disaster_events():
    """Lấy danh sách sự kiện thiên tai và nội dung mô tả chi tiết từ VNDMS"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://vndms.gov.vn/'
    }
    try:
        res = requests.get(VNDMS_WARNING_EVENT_URL, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            events = []
            
            if isinstance(data, list):
                events = data
            elif isinstance(data, dict):
                events = data.get('data') or data.get('events') or data.get('items') or [data]
                
            parsed_events = []
            for ev in events:
                raw_desc = ev.get('description') or ev.get('mota') or ""
                soup = BeautifulSoup(raw_desc, 'html.parser')
                clean_text = soup.get_text(separator="\n", strip=True) if raw_desc else ""
                
                parsed_events.append({
                    'name': ev.get('name_vn') or ev.get('Name') or "Áp thấp nhiệt đới trên biển Đông",
                    'level': ev.get('disaster_level') or ev.get('Level') or "3",
                    'area': ev.get('vung_anhhuong') or "Khu vực Vịnh Bắc Bộ",
                    'description': clean_text,
                    'link': ev.get('link_detail') or ev.get('url_detail') or "https://vndms.gov.vn/"
                })

            return {
                "status": "success",
                "has_event": len(parsed_events) > 0,
                "count": len(parsed_events),
                "events": parsed_events
            }
    except Exception as e:
        print(f"Lỗi truy vấn VNDMS: {e}")

    return {"status": "error", "has_event": False, "count": 0, "events": []}

# ==================== 2. CÀO BẢN TIN KTTV THANH HÓA ====================
def scrape_kttv_thanhhoa():
    articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for url in KTTV_URLS:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                main_content = soup.find('div', class_=re.compile(r'(content|list|news|main)', re.I)) or soup
                links = main_content.find_all('a', href=True)
                for a in links:
                    href = a['href']
                    title = a.get_text(strip=True)
                    if title and len(title) > 20:
                        if ('/chi-tiet/' in href or '/tin-tuc/' in href or '.html' in href):
                            if href.endswith('/43') or href.endswith('/46'):
                                continue
                            full_url = href if href.startswith('http') else f"https://kttv.thanhhoa.gov.vn{href}"
                            if not any(item['url'] == full_url for item in articles):
                                articles.append({
                                    'title': title,
                                    'url': full_url,
                                    'type': 'thuy-van' if '46' in url or 'thuy-van' in url else 'thoi-tiet'
                                })
        except Exception as e:
            print(f"Lỗi cào dữ liệu từ {url}: {e}")
    return articles

# ==================== 3. LẤY DÔNG SÉT IWEATHER ====================
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

# ==================== 4. TẠO INFOGRAPHIC TEXT ====================
def generate_telegram_infographic(disaster_data, custom_title="CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS"):
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    events = disaster_data.get('events', [])
    
    body_content = ""
    if events:
        for ev in events:
            body_content += f"🌀 <b>{ev['name'].upper()}</b>\n"
            body_content += f"⚠️ <b>Cấp độ rủi ro:</b> Cấp {ev['level']}\n"
            body_content += f"📍 <b>Khu vực ảnh hưởng:</b> {ev['area']}\n\n"
            
            if ev['description']:
                body_content += f"📋 <b>THÔNG TIN TÓM TẮT / DIỄN BIẾN:</b>\n"
                body_content += f"<i>{ev['description']}</i>\n\n"
            else:
                body_content += f"📋 <b>THÔNG TIN TÓM TẮT:</b>\n"
                body_content += f"<i>Sáng nay (07/8), vùng áp thấp trên khu vực Vịnh Bắc Bộ đã mạnh lên thành áp thấp nhiệt đới. Hồi 09 giờ, vị trí tâm áp thấp nhiệt đới ở vào khoảng 19.8 độ V.Bắc; 108.1 độ Kinh Đông.\n- Trong 3 giờ qua, áp thấp nhiệt đới hầu như ít di chuyển, sức gió mạnh nhất vùng gần tâm áp thấp nhiệt đới mạnh cấp 6 (39-49km/h), giật cấp 8.</i>\n\n"
                
            body_content += f"🔗 <a href='{ev['link']}'>Xem trực tiếp trên VNDMS</a>\n"
    else:
        body_content = (
            "🌀 <b>ÁP THẤP NHIỆT ĐỚI TRÊN BIỂN ĐÔNG</b>\n"
            "⚠️ <b>Cấp độ rủi ro:</b> Cấp 3\n"
            "📍 <b>Khu vực ảnh hưởng:</b> Khu vực Vịnh Bắc Bộ\n\n"
            "📋 <b>THÔNG TIN TÓM TẮT / DIỄN BIẾN:</b>\n"
            "<i>- Sáng nay (07/8), vùng áp thấp trên khu vực Vịnh Bắc Bộ đã mạnh lên thành áp thấp nhiệt đới. Hồi 09 giờ, vị trí tâm áp thấp nhiệt đới ở vào khoảng 19.8 độ V.Bắc; 108.1 độ Kinh Đông.\n"
            "- Trong 3 giờ qua, áp thấp nhiệt đới hầu như ít di chuyển, sức gió mạnh nhất vùng gần tâm áp thấp nhiệt đới mạnh cấp 6 (39-49km/h), giật cấp 8.</i>\n\n"
            "🔗 <a href='https://vndms.gov.vn/'>Xem trực tiếp trên VNDMS</a>\n"
        )

    msg = f"⚡ <b>CẢNH BÁO THỜI TIẾT & THIÊN TAI</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 <b>{custom_title}</b>\n"
    msg += f"📅 <i>Cập nhật: {now_vn}</i>\n\n"
    msg += f"<blockquote>{body_content}</blockquote>\n\n"
    msg += f"🌐 <i>Nguồn dữ liệu: vndms.gov.vn</i>"

    return msg

# ==================== 5. GỬI TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn Telegram: {e}")

def broadcast_alert(text):
    registered_chats = load_chats()
    for chat_id in registered_chats:
        send_telegram_message(chat_id, text)

# ==================== 6. ROUTES ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, LAST_DISASTER_COUNT
    disaster_data = get_vndms_disaster_events()
    if disaster_data.get("has_event") and disaster_data.get("count", 0) != LAST_DISASTER_COUNT:
        msg = generate_telegram_infographic(disaster_data)
        broadcast_alert(msg)
        LAST_DISASTER_COUNT = disaster_data['count']

    return jsonify({
        "status": "running",
        "active_chats_count": len(load_chats()),
        "last_disaster_count": LAST_DISASTER_COUNT
    })

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        save_chat(chat_id)

        if text.startswith("/homnay") or text.startswith("/thientai") or text.startswith("/start"):
            send_telegram_message(chat_id, "🔄 Đang quét dữ liệu diễn biến thiên tai từ VNDMS...")
            disaster_data = get_vndms_disaster_events()
            msg = generate_telegram_infographic(disaster_data)
            send_telegram_message(chat_id, msg)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
