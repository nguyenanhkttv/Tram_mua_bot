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
VNDMS_WARNING_EVENT_URL = "https://vndms.gov.vn/api/WarningEvent"
KTTV_URLS = [
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43",
    "https://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)
CHAT_FILE = "registered_chats.json"

# State bộ nhớ tạm để tránh spam thông báo trùng
LAST_ALERT_COUNT = 0
LAST_DISASTER_COUNT = 0
PROCESSED_NEWS_URLS = set()
PROCESSED_WATER_ALERTS = set()

# ==================== LƯU TRỮ CHAT ID ====================
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

# ==================== 1. TẠO INFOGRAPHIC HTML & RENDER ẢNH ====================
def generate_infographic_html(data):
    """Tạo HTML Infographic Glassmorphism Neon"""
    is_thuy_van = "thuy-van" in data.get('type', '')
    category_badge = "CẢNH BÁO THỦY VĂN" if is_thuy_van else "CẢNH BÁO THỜI TIẾT"
    bg_gradient = "linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%)" if is_thuy_van else "linear-gradient(135deg, #1e130c 0%, #9a400e 50%, #e65c00 100%)"
    header_icon = "🌊" if is_thuy_van else "⚡"
    
    title = data.get('title', 'BẢN TIN CẢNH BÁO KTTV THANH HÓA')
    date = data.get('date', datetime.now().strftime("%H:%M %d/%m/%Y"))
    risk_level = data.get('risk_level', 'Cấp 1 - 2. Cần chú ý theo dõi sát diễn biến.')
    affected_area = data.get('affected_area', 'Địa bàn tỉnh Thanh Hóa.')
    summary = data.get('summary', 'Chi tiết xem tại cổng thông tin Đài KTTV Thanh Hóa.')

    # Khối Thiên tai VNDMS
    disaster_block = ""
    disaster_events = data.get('disaster_events', [])
    if disaster_events:
        event_items = "".join([f"• 🌀 <b>{ev.get('Name') or ev.get('title', 'Thiên tai')}</b> (Cấp: {ev.get('Level') or ev.get('level', 'N/A')})<br>" for ev in disaster_events])
        disaster_block = f"""
        <div style="margin-bottom: 15px; background: rgba(255, 71, 87, 0.25); border-left: 4px solid #ff4757; padding: 12px; border-radius: 10px;">
            <h3 style="color: #ff6b81; font-size: 12px; margin-bottom: 5px;">🌀 THIÊN TAI ĐANG DIỄN RA (VNDMS):</h3>
            <p style="font-size: 13px; color: #ffffff;">{event_items}</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Roboto, sans-serif; }}
        body {{ width: 800px; background: {bg_gradient}; color: #ffffff; padding: 25px; border-radius: 16px; }}
        .card {{ background: rgba(255, 255, 255, 0.08); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.18); border-radius: 16px; padding: 25px; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37); }}
        .header {{ display: flex; align-items: center; border-bottom: 2px solid rgba(255, 255, 255, 0.2); padding-bottom: 15px; margin-bottom: 20px; }}
        .header .icon {{ font-size: 45px; margin-right: 15px; }}
        .header .badge {{ background: #ff4757; color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; text-transform: uppercase; display: inline-block; margin-bottom: 5px; }}
        .header h1 {{ font-size: 20px; color: #ffffff; line-height: 1.3; }}
        .grid-info {{ display: flex; gap: 15px; margin-bottom: 15px; }}
        .info-box {{ flex: 1; background: rgba(0, 0, 0, 0.25); padding: 15px; border-radius: 12px; border-left: 4px solid #eccc68; }}
        .info-box.danger {{ border-left-color: #ff4757; }}
        .info-box.water {{ border-left-color: #70a1ff; }}
        .info-box h3 {{ font-size: 12px; color: #eccc68; margin-bottom: 6px; text-transform: uppercase; }}
        .info-box p {{ font-size: 13px; line-height: 1.4; color: #f1f2f6; }}
        .content-body {{ background: rgba(0, 0, 0, 0.2); padding: 18px; border-radius: 12px; font-size: 14px; line-height: 1.6; margin-bottom: 20px; border: 1px dashed rgba(255, 255, 255, 0.2); }}
        .footer {{ display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #a4b0be; border-top: 1px solid rgba(255, 255, 255, 0.1); padding-top: 15px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="icon">{header_icon}</div>
            <div>
                <span class="badge">{category_badge}</span>
                <h1>{title}</h1>
                <div style="font-size: 12px; color: #dfe4ea; margin-top: 5px;">📅 Cập nhật: {date} | Đài KTTV Tỉnh Thanh Hóa</div>
            </div>
        </div>
        {disaster_block}
        <div class="grid-info">
            <div class="info-box danger"><h3>⚠️ Cấp độ Rủi ro</h3><p>{risk_level}</p></div>
            <div class="info-box water"><h3>📍 Khu vực ảnh hưởng</h3><p>{affected_area}</p></div>
        </div>
        <div class="content-body">
            <strong style="color: #ff7f50;">📌 NỘI DUNG TÓM TẮT DỰ BÁO:</strong><br><br>{summary}
        </div>
        <div class="footer">
            <span>🤖 Telegram Weather Bot System</span>
            <span style="font-weight: bold; color: #70a1ff;">🌐 kttv.thanhhoa.gov.vn | vndms.gov.vn</span>
        </div>
    </div>
</body>
</html>"""

import shutil
def render_infographic_image(html_code, filename="infographic.png"):
    """Render HTML sang PNG hỗ trợ Render Headless Environment"""
    try:
        # Tìm đường dẫn chromium hoặc chrome trên hệ thống
        browser_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        
        custom_flags = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--headless'
        ]
        
        if browser_path:
            hti = Html2Image(browser_executable=browser_path, custom_flags=custom_flags)
        else:
            hti = Html2Image(custom_flags=custom_flags)
            
        temp_html = "temp_render.html"
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        hti.screenshot(html_file=temp_html, save_as=filename, size=(820, 750))
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
                for a in soup.find_all('a', href=True):
                    href, title = a['href'], a.get_text(strip=True)
                    if title and len(title) > 18 and any(k in href for k in ['tin-tuc', 'chi-tiet', '/43', '/46']):
                        full_url = href if href.startswith('http') else f"https://kttv.thanhhoa.gov.vn{href}"
                        articles.append({'title': title, 'url': full_url, 'type': 'thuy-van' if 'thuy-van' in url else 'thoi-tiet'})
        except Exception as e:
            print(f"Lỗi cào KTTV: {e}")
    return articles

# ==================== 3. XỬ LÝ API IWEATHER & VNDMS ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://iweather.gov.vn/'}
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code == 200:
            matches = re.findall(r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)', res.text, re.IGNORECASE)
            unique_locs = list(set([m.strip(' ",') for m in matches]))
            return {
                "status": "success",
                "has_warning": len(unique_locs) > 0,
                "count": len(unique_locs),
                "alerts": [{"location": loc, "time": now_vn.strftime('%H:%M %d/%m/%Y')} for loc in unique_locs],
                "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
            }
    except Exception as e:
        print(f"Lỗi iWeather: {e}")
    return {"status": "error", "has_warning": False, "count": 0, "alerts": []}

def get_vndms_disaster_events():
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://vndms.gov.vn/'}
    try:
        res = requests.get(VNDMS_WARNING_EVENT_URL, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            events = data if isinstance(data, list) else (data.get('data') or data.get('events') or [])
            return {"status": "success", "has_event": len(events) > 0, "count": len(events), "events": events}
    except Exception as e:
        print(f"Lỗi VNDMS Event: {e}")
    return {"status": "error", "has_event": False, "count": 0, "events": []}

def check_vndms_water_levels_thanhhoa():
    """Lọc riêng báo động mực nước sông (BD1, BD2, BD3) tại Tỉnh Thanh Hóa từ VNDMS"""
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://vndms.gov.vn/'}
    keywords = ["thanh hóa", "thanh hoá", "thanh hoa"]
    alerts = []
    
    for level in [1, 2, 3]:
        try:
            res = requests.get(f"https://vndms.gov.vn/water_level?lv={level}", headers=headers, timeout=10)
            if res.status_code == 200:
                items = res.json() if isinstance(res.json(), list) else res.json().get('data', [])
                for item in items:
                    if any(kw in json.dumps(item, ensure_ascii=False).lower() for kw in keywords):
                        alerts.append({
                            'station': item.get('station_name') or item.get('TenTram') or item.get('name', 'Trạm thủy văn'),
                            'river': item.get('river_name') or item.get('TenSong') or 'Sông chưa xác định',
                            'level': level,
                            'value': item.get('value') or item.get('MucNuoc') or 'N/A',
                            'time': item.get('time') or item.get('ThoiGian') or ''
                        })
        except Exception as e:
            print(f"Lỗi mực nước LV{level}: {e}")
    return alerts

# ==================== 4. GỬI TELEGRAM ====================
def send_telegram_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi Text: {e}")

def send_telegram_photo(chat_id, photo_path, caption=""):
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(f"{TELEGRAM_API_URL}/sendPhoto", data={'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}, files={'photo': photo}, timeout=15)
    except Exception as e:
        print(f"Lỗi gửi Photo: {e}")

def broadcast_alert(text=None, photo_path=None, caption=""):
    for chat_id in load_chats():
        if text: send_telegram_message(chat_id, text)
        if photo_path: send_telegram_photo(chat_id, photo_path, caption)

# ==================== 5. LUỒNG QUÉT TỰ ĐỘNG KHÔNG TRÙNG ====================
def run_all_checks():
    global LAST_ALERT_COUNT, LAST_DISASTER_COUNT, PROCESSED_NEWS_URLS, PROCESSED_WATER_ALERTS
    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")

    # A. Cảnh báo Dông Sét iWeather
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        cur_cnt = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and cur_cnt != LAST_ALERT_COUNT:
            msg = f"⚠️ **CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!**\n🕒 *Thời gian:* {iweather_data['updated_at']}\n📍 **Số khu vực phát hiện:** {cur_cnt}\n"
            for idx, a in enumerate(iweather_data['alerts'], 1):
                msg += f"\n**{idx}.** {a['location']}"
            msg += "\n\n🌐 Radar: https://iweather.gov.vn/"
            broadcast_alert(text=msg)
            LAST_ALERT_COUNT = cur_cnt
        elif not iweather_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # B. Cảnh báo Thiên tai VNDMS
    disaster_data = get_vndms_disaster_events()
    if disaster_data.get("has_event") and disaster_data.get("count", 0) != LAST_DISASTER_COUNT:
        msg = f"🌀 **CẢNH BÁO THIÊN TAI ĐANG DIỄN RA (VNDMS)!**\n📍 **Số sự kiện:** {disaster_data['count']}\n"
        for idx, ev in enumerate(disaster_data['events'], 1):
            msg += f"\n**{idx}.** {ev.get('Name') or 'Sự kiện'} *(Cấp: {ev.get('Level') or 'Theo dõi'})*"
        broadcast_alert(text=msg)
        LAST_DISASTER_COUNT = disaster_data['count']

    # C. Cảnh báo Mực nước Sông Thanh Hóa (BD1, BD2, BD3)
    water_alerts = check_vndms_water_levels_thanhhoa()
    if water_alerts:
        new_water_msgs = []
        for a in water_alerts:
            alert_id = f"{a['station']}_{a['level']}_{a['value']}"
            if alert_id not in PROCESSED_WATER_ALERTS:
                PROCESSED_WATER_ALERTS.add(alert_id)
                badge = "🚨 [BÁO ĐỘNG 3]" if a['level'] == 3 else ("⚠️ [BÁO ĐỘNG 2]" if a['level'] == 2 else "📢 [BÁO ĐỘNG 1]")
                new_water_msgs.append(f"{badge}\n📍 **Trạm:** {a['station']} ({a['river']})\n📏 **Mực nước:** {a['value']}m")
        
        if new_water_msgs:
            msg_w = "🌊 **CẢNH BÁO MỰC NƯỚC SÔNG NGUY HIỂM TẠI THANH HÓA!**\n\n" + "\n\n".join(new_water_msgs)
            broadcast_alert(text=msg_w)

    # D. Cào Bản tin mới KTTV Thanh Hóa -> Infographic
    articles = scrape_kttv_thanhhoa()
    for art in articles[:2]:
        if art['url'] not in PROCESSED_NEWS_URLS:
            PROCESSED_NEWS_URLS.add(art['url'])
            info_data = {
                'title': art['title'], 'type': art['type'], 'date': now_vn,
                'risk_level': 'Cảnh báo rủi ro thiên tai, mưa dông, lốc, sét & dâng trào thủy văn.',
                'affected_area': 'Địa bàn tỉnh Thanh Hóa.',
                'summary': f"Chi tiết bản tin: <a href='{art['url']}'>{art['url']}</a>",
                'disaster_events': disaster_data.get('events', [])
            }
            html_code = generate_infographic_html(info_data)
            img_path = render_infographic_image(html_code, "news_infographic.png")
            if img_path and os.path.exists(img_path):
                broadcast_alert(photo_path=img_path, caption=f"🚨 <b>{art['title']}</b>\n🔗 <a href='{art['url']}'>Xem bản tin gốc</a>")

# ==================== 6. ROUTES & TELEGRAM WEBHOOK ====================
@app.route('/')
def home():
    run_all_checks()
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
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        save_chat(chat_id)

        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/thientai"):
            send_telegram_message(chat_id, "⚡ Đang tra cứu Dông sét, Thiên tai, Mực nước sông & Bản tin mới nhất...")
            run_all_checks()

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
