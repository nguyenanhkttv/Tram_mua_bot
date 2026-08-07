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

# Quản lý lưu trữ Chat ID bền vững bằng File JSON
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

# ==================== 1. TẠO TỔNG HỢP INFOGRAPHIC DẠNG TELEGRAM HTML ====================
def generate_telegram_infographic(data):
    """Tạo tin nhắn dạng Card Infographic bằng chuẩn Telegram HTML"""
    is_thuy_van = "thuy-van" in data.get('type', '')
    badge = "🌊 <b>CẢNH BÁO THỦY VĂN</b>" if is_thuy_van else "⚡ <b>CẢNH BÁO THỜI TIẾT</b>"
    
    title = data.get('title', 'BẢN TIN CẢNH BÁO KTTV THANH HÓA')
    date = data.get('date', datetime.now().strftime("%H:%M %d/%m/%Y"))
    risk_level = data.get('risk_level', 'Cấp 1 - 2. Cần chú ý theo dõi sát diễn biến.')
    affected_area = data.get('affected_area', 'Địa bàn tỉnh Thanh Hóa.')
    summary = data.get('summary', '')

    # Khối sự kiện thiên tai VNDMS
    disaster_text = ""
    disaster_events = data.get('disaster_events', [])
    if disaster_events:
        disaster_text += "\n🌀 <b>THIÊN TAI ĐANG DIỄN RA (VNDMS):</b>\n"
        for ev in disaster_events:
            name = ev.get('name_vn') or ev.get('Name') or "Thiên tai đang diễn ra"
            level = ev.get('disaster_level') or ev.get('Level') or "Cần theo dõi"
            area = ev.get('vung_anhhuong') or "Chưa xác định"
            disaster_text += f"• <b>{name}</b> (Cấp {level})\n  📍 <i>Khu vực: {area}</i>\n"

    # Định dạng khung Card dạng blockquote của Telegram
    msg = f"{badge}\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 <b>{title.upper()}</b>\n"
    msg += f"📅 <i>Cập nhật: {date}</i>\n"
    msg += f"{disaster_text}\n"
    msg += f"<blockquote>"
    msg += f"⚠️ <b>CẤP ĐỘ RỦI RO:</b>\n{risk_level}\n\n"
    msg += f"📍 <b>KHU VỰC ẢNH HƯỞNG:</b>\n{affected_area}\n"
    if summary:
        msg += f"\n📌 <b>NỘI DUNG TÓM TẮT:</b>\n{summary}"
    msg += f"</blockquote>\n\n"
    msg += f"🌐 <i>Đài KTTV Thanh Hóa | vndms.gov.vn</i>"

    return msg

# ==================== 2. CÀO BẢN TIN KTTV THANH HÓA ====================
def scrape_kttv_thanhhoa():
    articles = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    for url in KTTV_URLS:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # 1. Tìm khu vực chứa danh sách bài viết chính (bỏ qua menu/sidebar/tin nổi bật)
                main_content = soup.find('div', class_=re.compile(r'(content|list|news|main)', re.I)) or soup
                
                # 2. Tìm tất cả các thẻ <a> trong vùng nội dung chính
                links = main_content.find_all('a', href=True)
                
                for a in links:
                    href = a['href']
                    title = a.get_text(strip=True)
                    
                    # 3. Lọc tiêu đề hợp lệ và tránh link trang danh mục tổng (/43, /46)
                    if title and len(title) > 20:
                        # Link bài viết chi tiết thường chứa ID dạng số hoặc slug bài
                        if ('/chi-tiet/' in href or '/tin-tuc/' in href or '.html' in href or re.search(r'/\d+$', href)):
                            # Bỏ qua link dẫn ngược lại trang danh mục tổng
                            if href.endswith('/43') or href.endswith('/46') or href.endswith('/tin-tuc/thoi-tiet-nguy-hiem') or href.endswith('/tin-tuc/thuy-van-dac-biet'):
                                continue
                                
                            full_url = href if href.startswith('http') else f"https://kttv.thanhhoa.gov.vn{href}"
                            
                            # Tránh trùng lặp
                            if not any(item['url'] == full_url for item in articles):
                                articles.append({
                                    'title': title,
                                    'url': full_url,
                                    'type': 'thuy-van' if '46' in url or 'thuy-van' in url else 'thoi-tiet'
                                })
        except Exception as e:
            print(f"Lỗi cào dữ liệu từ {url}: {e}")
            
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
    """Lấy danh sách sự kiện thiên tai từ Hệ thống Giám sát Thiên tai VNDMS"""
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
        print(f"Lỗi gửi tin nhắn Telegram: {e}")

def broadcast_alert(text):
    registered_chats = load_chats()
    for chat_id in registered_chats:
        send_telegram_message(chat_id, text, parse_mode="HTML")

# ==================== 5. ROUTES ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, PROCESSED_NEWS_URLS, LAST_DISASTER_COUNT
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    
    # --- A. Kiểm tra Dông Sét iWeather ---
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚠️ <b>CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!</b>\n"
            msg += f"🕒 <i>Thời gian:</i> {iweather_data['updated_at']}\n"
            msg += f"📍 <b>Số khu vực phát hiện:</b> {current_count}\n"
            for idx, alert in enumerate(iweather_data['alerts'], 1):
                msg += f"\n<b>{idx}.</b> {alert['location']}"
            msg += "\n\n🌐 Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            broadcast_alert(msg)
            LAST_ALERT_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # --- B. Kiểm tra Sự kiện thiên tai VNDMS ---
    disaster_data = get_vndms_disaster_events()
    if disaster_data.get("has_event") and disaster_data.get("count", 0) != LAST_DISASTER_COUNT:
        msg = f"🌀 <b>CẢNH BÁO THIÊN TAI ĐANG DIỄN RA (VNDMS)!</b>\n"
        msg += f"📍 <b>Số sự kiện ghi nhận:</b> {disaster_data['count']}\n"
        for idx, ev in enumerate(disaster_data['events'], 1):
            name = ev.get('name_vn') or ev.get('Name') or "Sự kiện thiên tai"
            level = ev.get('disaster_level') or ev.get('Level') or "Cần theo dõi"
            area = ev.get('vung_anhhuong') or "Chưa xác định"
            msg += f"\n<b>{idx}.</b> {name}"
            msg += f"\n   ⚠️ <i>Cấp độ rủi ro:</i> Cấp {level}"
            msg += f"\n   📍 <i>Khu vực:</i> {area}\n"
        msg += "\n🌐 Giám sát: https://vndms.gov.vn/"
        broadcast_alert(msg)
        LAST_DISASTER_COUNT = disaster_data['count']

    # --- C. Quét Bản tin mới KTTV & Bắn Infographic Text ---
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
                'summary': f"Bản tin chi tiết được cập nhật từ Đài KTTV Thanh Hóa.\n🔗 <a href='{art['url']}'>Nhấn vào đây để xem chi tiết bản tin</a>",
                'disaster_events': disaster_data.get('events', [])
            }
            
            infographic_msg = generate_telegram_infographic(info_data)
            broadcast_alert(infographic_msg)

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

        # Lưu Chat ID
        save_chat(chat_id)

        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/thientai"):
            send_telegram_message(chat_id, "⚡ Đang tra cứu Dông sét, Thiên tai VNDMS & Bản tin mới nhất...")
            
            # 1. Trả về dông sét
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

            # 2. Trả về sự kiện thiên tai VNDMS
            disaster_data = get_vndms_disaster_events()
            if disaster_data.get("has_event"):
                msg_dis = f"🌀 <b>SỰ KIỆN THIÊN TAI ĐANG DIỄN RA (VNDMS):</b>\n"
                for idx, ev in enumerate(disaster_data['events'], 1):
                    name = ev.get('name_vn') or ev.get('Name') or "Thiên tai"
                    level = ev.get('disaster_level') or ev.get('Level') or "Cần theo dõi"
                    area = ev.get('vung_anhhuong') or "Chưa xác định"
                    msg_dis += f"\n<b>{idx}.</b> {name}\n   ⚠️ <i>Cấp độ rủi ro:</i> Cấp {level}\n   📍 <i>Khu vực:</i> {area}\n"
                send_telegram_message(chat_id, msg_dis)

            # 3. Gửi Infographic dạng Card HTML
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
                    'summary': f"Bản tin chi tiết xem tại: <a href='{top_art['url']}'>{top_art['url']}</a>",
                    'disaster_events': disaster_data.get('events', [])
                }
                infographic_msg = generate_telegram_infographic(info_data)
                send_telegram_message(chat_id, infographic_msg)
            # 4. Lệnh tra cứu nhanh tin tức hôm nay
            if text.startswith("/homnay"):
                send_telegram_message(chat_id, "🔄 Đang quét dữ liệu mới nhất hôm nay (07/08/2026)...")
            
            disaster_data = get_vndms_disaster_events()
            articles = scrape_kttv_thanhhoa()
            
            now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
            
            top_title = articles[0]['title'] if articles else "BẢN TIN CẢNH BÁO THỜI TIẾT THANH HÓA"
            top_url = articles[0]['url'] if articles else "https://kttv.thanhhoa.gov.vn"
            
            info_data = {
                'title': top_title,
                'type': 'thoi-tiet',
                'date': now_vn,
                'risk_level': 'Cảnh báo rủi ro thiên tai Cấp 1 - 3.',
                'affected_area': 'Vịnh Bắc Bộ & Tỉnh Thanh Hóa.',
                'summary': f"Chi tiết bản tin: <a href='{top_url}'>{top_url}</a>",
                'disaster_events': disaster_data.get('events', [])
            }
            
            msg_today = generate_telegram_infographic(info_data)
            send_telegram_message(chat_id, msg_today)
            
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
