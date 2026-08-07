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

# Quản lý Chat ID
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

# ==================== 1. ĐỊNH DẠNG TIN NHẮN CARD TELEGRAM ====================
def generate_telegram_infographic(data):
    """Tạo tin nhắn Card Infographic bóc tách chính xác dữ liệu VNDMS & KTTV"""
    badge = "⚡ <b>CẢNH BÁO THỜI TIẾT & THIÊN TAI</b>"
    title = data.get('title', 'CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS')
    date = data.get('date', datetime.now().strftime("%H:%M %d/%m/%Y"))
    
    disaster_events = data.get('disaster_events', [])
    event_details_text = ""
    
    if disaster_events:
        for ev in disaster_events:
            # Lấy chuẩn Key từ API VNDMS
            name = ev.get('title_vn') or ev.get('name_disaster') or "Áp thấp nhiệt đới trên biển Đông"
            level = ev.get('disaster_level') or ev.get('level') or "3"
            area = ev.get('vung_anhhuong') or ev.get('vunganhhuong') or "Khu vực Vịnh Bắc Bộ"
            link = ev.get('url_detail') or ev.get('link_detail') or "https://vndms.gov.vn/"
            direction = ev.get('huong_dichuyen') or ""
            
            # Xử lý tóm tắt diễn biến
            raw_desc = ev.get('description') or ""
            clean_desc = re.sub(r'<[^>]+>', '', raw_desc).strip()
            desc_text = f"\n📝 <b>Diễn biến:</b> {clean_desc[:180]}..." if clean_desc else ""
            
            event_details_text += f"🌀 <b>SỰ KIỆN:</b> {name}\n"
            event_details_text += f"⚠️ <b>Cấp độ rủi ro:</b> Cấp {level}\n"
            event_details_text += f"📍 <b>Khu vực ảnh hưởng:</b> {area}\n"
            if direction:
                event_details_text += f"🧭 <b>Hướng di chuyển:</b> Hướng {direction}\n"
            event_details_text += f"{desc_text}\n"
            event_details_text += f"🔗 <a href='{link}'>Xem chi tiết bản tin VNDMS</a>\n\n"
    else:
        summary = data.get('summary', 'Chi tiết xem tại cổng thông tin KTTV.')
        event_details_text = f"📍 <b>KHU VỰC:</b> {data.get('affected_area', 'Thanh Hóa')}\n"
        event_details_text += f"⚠️ <b>RỦI RO:</b> {data.get('risk_level', 'Cấp 1 - 2')}\n"
        event_details_text += f"📌 <b>NỘI DUNG:</b> {summary}\n"

    msg = f"{badge}\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 <b>{title.upper()}</b>\n"
    msg += f"📅 <i>Cập nhật: {date}</i>\n\n"
    msg += f"<blockquote>{event_details_text}</blockquote>\n\n"
    msg += f"🌐 <i>Nguồn dữ liệu: kttv.thanhhoa.gov.vn | vndms.gov.vn</i>"

    return msg

# ==================== 2. CÀO BẢN TIN KTTV THANH HÓA ====================
def scrape_kttv_thanhhoa():
    articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    
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
                        if ('/chi-tiet/' in href or '/tin-tuc/' in href or '.html' in href or re.search(r'/\d+$', href)):
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
            print(f"Lỗi cào KTTV Thanh Hóa: {e}")
            
    return articles

# ==================== 3. XỬ LÝ DỮ LIỆU IWEATHER & VNDMS ====================
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

def get_vndms_disaster_events():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
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
                events = data.get('data', []) or data.get('events', []) or data.get('items', [])

            return {
                "status": "success",
                "has_event": len(events) > 0,
                "count": len(events),
                "events": events
            }
    except Exception as e:
        print(f"Lỗi truy vấn VNDMS: {e}")

    return {"status": "error", "has_event": False, "count": 0, "events": []}

# ==================== 4. GỬI TELEGRAM ====================
def send_telegram_message(chat_id, text, parse_mode="HTML"):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": parse_mode,
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def broadcast_alert(text):
    registered_chats = load_chats()
    for chat_id in registered_chats:
        send_telegram_message(chat_id, text)

# ==================== 5. ROUTES FLASK ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, PROCESSED_NEWS_URLS, LAST_DISASTER_COUNT
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    
    # A. Kiểm tra Dông Sét iWeather
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚠️ <b>PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!</b>\n"
            msg += f"🕒 <i>Thời gian:</i> {iweather_data['updated_at']}\n"
            msg += f"📍 <b>Số khu vực:</b> {current_count}\n"
            for idx, alert in enumerate(iweather_data['alerts'], 1):
                msg += f"\n<b>{idx}.</b> {alert['location']}"
            msg += "\n\n🌐 Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            broadcast_alert(msg)
            LAST_ALERT_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # B. Kiểm tra VNDMS
    disaster_data = get_vndms_disaster_events()
    if disaster_data.get("has_event") and disaster_data.get("count", 0) != LAST_DISASTER_COUNT:
        info_data = {
            'title': 'CẢNH BÁO THIÊN TAI TỰ ĐỘNG (VNDMS)',
            'date': now_vn,
            'disaster_events': disaster_data.get('events', [])
        }
        broadcast_alert(generate_telegram_infographic(info_data))
        LAST_DISASTER_COUNT = disaster_data['count']

    # C. Quét Bản tin mới KTTV Thanh Hóa
    articles = scrape_kttv_thanhhoa()
    for art in articles[:2]:
        if art['url'] not in PROCESSED_NEWS_URLS:
            PROCESSED_NEWS_URLS.add(art['url'])
            info_data = {
                'title': art['title'],
                'type': art['type'],
                'date': now_vn,
                'risk_level': 'Cảnh báo rủi ro thiên tai KTTV.',
                'affected_area': 'Địa bàn tỉnh Thanh Hóa.',
                'summary': f"Bản tin chi tiết: <a href='{art['url']}'>{art['url']}</a>",
                'disaster_events': disaster_data.get('events', [])
            }
            broadcast_alert(generate_telegram_infographic(info_data))

    return jsonify({
        "status": "running",
        "active_chats_count": len(load_chats()),
        "last_alert_count": LAST_ALERT_COUNT,
        "last_disaster_count": LAST_DISASTER_COUNT,
        "processed_news_count": len(PROCESSED_NEWS_URLS)
    })

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        save_chat(chat_id)

        # 1. Lệnh /start hoặc /dong (Tổng hợp toàn bộ)
        if text.startswith("/start") or text.startswith("/dong"):
            send_telegram_message(chat_id, "⚡ Đang tra cứu Dông sét, Thiên tai VNDMS & Bản tin mới nhất...")
            
            # Quét Dông Sét
            dw = get_iweather_storm_warning("Thanh Hóa")
            if dw.get("status") == "success":
                if dw.get("has_warning"):
                    msg = f"🚨 <b>CẢNH BÁO DÔNG SÉT TẠI THANH HÓA!</b>\n🕒 <i>Cập nhật:</i> {dw['updated_at']}\n"
                    msg += f"📍 <b>Phát hiện {dw['count']} khu vực:</b>\n"
                    for idx, alert in enumerate(dw['alerts'], 1):
                        msg += f"\n<b>{idx}.</b> {alert['location']}"
                else:
                    msg = f"✅ <b>AN TOÀN ({dw['updated_at']}):</b> Chưa phát hiện mây dông sét tại Thanh Hóa."
                send_telegram_message(chat_id, msg)

            # Quét VNDMS & Bản tin KTTV
            disaster_data = get_vndms_disaster_events()
            articles = scrape_kttv_thanhhoa()
            top_art = articles[0] if articles else None
            now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")

            info_data = {
                'title': top_art['title'] if top_art else 'CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS',
                'type': top_art['type'] if top_art else 'thoi-tiet',
                'date': now_vn,
                'summary': f"Chi tiết: <a href='{top_art['url']}'>{top_art['url']}</a>" if top_art else "",
                'disaster_events': disaster_data.get('events', [])
            }
            send_telegram_message(chat_id, generate_telegram_infographic(info_data))

        # 2. Lệnh /homnay hoặc /thientai (Chuyên tra cứu VNDMS)
        elif text.startswith("/homnay") or text.startswith("/thientai"):
            send_telegram_message(chat_id, "🔄 Đang quét dữ liệu mới nhất từ VNDMS...")
            disaster_data = get_vndms_disaster_events()
            now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
            
            info_data = {
                'title': 'CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS',
                'date': now_vn,
                'disaster_events': disaster_data.get('events', [])
            }
            send_telegram_message(chat_id, generate_telegram_infographic(info_data))

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
