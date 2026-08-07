import os
import re
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

# Bộ nhớ tạm lưu Chat ID và Trạng thái đã gửi
REGISTERED_CHATS = set()
LAST_ALERT_COUNT = 0
LAST_DISASTER_COUNT = 0
PROCESSED_NEWS_URLS = set()

# ==================== HÀM GỬI TIN TELEGRAM ====================
def send_telegram_message(chat_id, text, parse_mode="HTML"):
    """Gửi tin nhắn Telegram chuẩn HTML để không bị lỗi ký tự đặc biệt"""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn Telegram: {e}")

def broadcast_alert(text):
    """Gửi thông báo tới tất cả người dùng đã đăng ký"""
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

# ==================== 1. PHẦN IWEATHER (DÔNG SÉT) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
                "message": "Phát hiện vùng mây đối lưu nguy hiểm gây dông sét",
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
        return {"status": "error", "message": f"Lỗi xử lý dữ liệu: {str(e)}"}

# ==================== 2. PHẦN VNDMS (THIÊN TAI) ====================
def get_vndms_disaster_events():
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
                clean_desc = soup.get_text(separator="\n", strip=True) if raw_desc else ""
                
                # Tự động chọn Icon phù hợp theo loại thiên tai
                event_name = ev.get('name_vn') or ev.get('Name') or "Sự kiện thiên tai"
                icon = "🌀" # Mặc định bão/áp thấp
                if any(k in event_name.lower() for k in ['mưa', 'mưa lớn', 'ngập']):
                    icon = "🌧️"
                elif any(k in event_name.lower() for k in ['lũ', 'ngập lụt', 'thủy văn']):
                    icon = "🌊"
                elif any(k in event_name.lower() for k in ['sạt lở', 'đất', 'lũ quét']):
                    icon = "⛰️"
                elif any(k in event_name.lower() for k in ['nắng nóng', 'cháy rừng']):
                    icon = "☀️"
                
                parsed_events.append({
                    'icon': icon,
                    'name': event_name,
                    'level': ev.get('disaster_level') or ev.get('Level') or "Cần theo dõi",
                    'area': ev.get('vung_anhhuong') or "Chưa xác định",
                    'description': clean_desc,
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

def format_vndms_message(vndms_data):
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    msg = f"⚠️ <b>CẢNH BÁO THIÊN TAI ĐANG DIỄN RA (VNDMS)</b>\n"
    msg += f"🕒 <i>Cập nhật: {now_vn}</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n\n"
    
    for ev in vndms_data['events']:
        msg += f"{ev['icon']} <b>{ev['name'].upper()}</b>\n"
        msg += f"⚠️ <b>Cấp độ rủi ro:</b> Cấp {ev['level']}\n"
        msg += f"📍 <b>Khu vực ảnh hưởng:</b> {ev['area']}\n"
        if ev['description']:
            # Lấy 300 ký tự đầu của phần mô tả diễn biến
            msg += f"\n📋 <b>TÓM TẮT DIỄN BIẾN:</b>\n<i>{ev['description'][:300]}...</i>\n"
        msg += f"\n🔗 <a href='{ev['link']}'>Xem chi tiết bản tin trên VNDMS</a>\n\n"
        
    return msg
# ==================== 3. PHẦN KTTV THANH HÓA (/43 & /46) ====================
def scrape_kttv_thanhhoa():
    articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
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
                        if ('/chi-tiet/' in href or '/tin-tuc/' in href or re.search(r'/\d+$', href)):
                            if href.endswith('/43') or href.endswith('/46'):
                                continue
                                
                            full_url = href if href.startswith('http') else f"https://kttv.thanhhoa.gov.vn{href}"
                            category = 'THỦY VĂN ĐẶC BIỆT' if '46' in url else 'THỜI TIẾT NGUY HIỂM'
                            
                            if not any(item['url'] == full_url for item in articles):
                                articles.append({
                                    'title': title,
                                    'url': full_url,
                                    'category': category
                                })
        except Exception as e:
            print(f"Lỗi cào dữ liệu từ {url}: {e}")
            
    return articles

def format_kttv_message(article):
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    msg = f"📰 <b>BẢN TIN KTTV THANH HÓA MỚI NHẤT</b>\n"
    msg += f"🏷️ <b>Danh mục:</b> {article['category']}\n"
    msg += f"🕒 <i>Cập nhật: {now_vn}</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"📢 <b>{article['title']}</b>\n\n"
    msg += f"🔗 <a href='{article['url']}'>Xem chi tiết bản tin đầy đủ</a>"
    return msg

# ==================== WEB ROUTES (CRON JOB SCANNER) ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, LAST_DISASTER_COUNT, PROCESSED_NEWS_URLS
    
    # 1. Kiểm tra Dông Sét iWeather (Tách riêng tin)
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚡ <b>CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!</b>\n"
            msg += f"🕒 <i>Thời gian:</i> {iweather_data['updated_at']}\n"
            msg += f"📍 <b>Số khu vực phát hiện:</b> {current_count}\n"
            for idx, alert in enumerate(iweather_data['alerts'], 1):
                msg += f"\n<b>{idx}.</b> {alert['location']}"
            msg += "\n\n🌐 <a href='https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'>Xem trực quan Radar iWeather</a>"
            
            broadcast_alert(msg)
            LAST_ALERT_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # 2. Kiểm tra Thiên tai VNDMS (Tách riêng tin)
    vndms_data = get_vndms_disaster_events()
    if vndms_data.get("has_event") and vndms_data.get("count", 0) != LAST_DISASTER_COUNT:
        msg_vndms = format_vndms_message(vndms_data)
        broadcast_alert(msg_vndms)
        LAST_DISASTER_COUNT = vndms_data.get("count", 0)

    # 3. Kiểm tra Bản tin mới KTTV Thanh Hóa (Tách riêng từng tin bài)
    articles = scrape_kttv_thanhhoa()
    for art in articles[:2]:
        if art['url'] not in PROCESSED_NEWS_URLS:
            PROCESSED_NEWS_URLS.add(art['url'])
            msg_kttv = format_kttv_message(art)
            broadcast_alert(msg_kttv)

    return jsonify({
        "status": "running", 
        "active_chats": list(REGISTERED_CHATS),
        "last_alert_count": LAST_ALERT_COUNT,
        "last_disaster_count": LAST_DISASTER_COUNT,
        "processed_news_count": len(PROCESSED_NEWS_URLS)
    })

# ==================== WEBHOOK NHẬN LỆNH TELEGRAM ====================
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        REGISTERED_CHATS.add(chat_id)

        # Lệnh tra cứu Dông sét iWeather
        if text.startswith("/dong") or text.startswith("/dongset"):
            send_telegram_message(chat_id, "⚡ Đang quét dữ liệu mây đối lưu & dông sét từ iWeather...")
            data = get_iweather_storm_warning("Thanh Hóa")
            if data.get("status") == "success":
                if data.get("has_warning"):
                    msg = f"🚨 <b>CẢNH BÁO DÔNG SÉT TẠI THANH HÓA!</b>\n"
                    msg += f"🕒 <i>Cập nhật:</i> {data['updated_at']}\n"
                    msg += f"📍 <b>Phát hiện {data['count']} khu vực có mây dông:</b>\n"
                    for idx, alert in enumerate(data['alerts'], 1):
                        msg += f"\n<b>{idx}.</b> {alert['location']}"
                    msg += "\n\n🌐 <a href='https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'>Bản đồ Radar iWeather</a>"
                else:
                    msg = f"✅ <b>AN TOÀN ({data['updated_at']}):</b> Hiện chưa phát hiện mây dông hay cảnh báo sét tại khu vực Thanh Hóa."
            else:
                msg = f"❌ Lỗi: {data.get('message')}"
            send_telegram_message(chat_id, msg)

        # Lệnh tra cứu Thiên tai VNDMS
        elif text.startswith("/thientai") or text.startswith("/vndms"):
            send_telegram_message(chat_id, "🔄 Đang kiểm tra sự kiện thiên tai từ VNDMS...")
            vndms_data = get_vndms_disaster_events()
            if vndms_data.get("has_event"):
                msg_vndms = format_vndms_message(vndms_data)
                send_telegram_message(chat_id, msg_vndms)
            else:
                msg_vndms = "✅ <b>VNDMS:</b> Hiện không có sự kiện thiên tai diện rộng nguy hiểm nào đang diễn ra."
                send_telegram_message(chat_id, msg_vndms)

        # Lệnh tra cứu Bản tin KTTV Thanh Hóa
        elif text.startswith("/kttv") or text.startswith("/bantin"):
            send_telegram_message(chat_id, "📰 Đang cào bản tin mới nhất từ KTTV Thanh Hóa...")
            articles = scrape_kttv_thanhhoa()
            if articles:
                for art in articles[:2]:
                    msg_kttv = format_kttv_message(art)
                    send_telegram_message(chat_id, msg_kttv)
            else:
                send_telegram_message(chat_id, "ℹ️ Hiện chưa có bản tin mới từ trang KTTV Thanh Hóa.")

        # Lệnh /start: Kiểm tra và gửi riêng từng bản tin hiện có
        elif text.startswith("/start"):
            send_telegram_message(chat_id, "🤖 <b>TRẠM MƯA BÁO CÁO BOT KHỞI ĐỘNG</b>\nĐang kiểm tra và gửi báo cáo theo từng lĩnh vực...")
            
            # 1. Gửi bản tin dông sét
            data = get_iweather_storm_warning("Thanh Hóa")
            if data.get("has_warning"):
                msg_iweather = f"🚨 <b>CẢNH BÁO DÔNG SÉT TẠI THANH HÓA!</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n📍 Phát hiện {data['count']} khu vực."
            else:
                msg_iweather = f"✅ <b>DÔNG SÉT ({data.get('updated_at', '')}):</b> An toàn, không có mây dông."
            send_telegram_message(chat_id, msg_iweather)

            # 2. Gửi riêng bản tin VNDMS
            vndms_data = get_vndms_disaster_events()
            if vndms_data.get("has_event"):
                msg_vndms = format_vndms_message(vndms_data)
                send_telegram_message(chat_id, msg_vndms)

            # 3. Gửi riêng bài viết mới KTTV Thanh Hóa
            articles = scrape_kttv_thanhhoa()
            if articles:
                msg_kttv = format_kttv_message(articles[0])
                send_telegram_message(chat_id, msg_kttv)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
