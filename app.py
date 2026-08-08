import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

# ==================== CẤU HÌNH ENDPOINTS ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_LIST_API = "https://vndms.gov.vn/api/Disaster/GetListDisaster"
VNDMS_DETAIL_API = "https://vndms.gov.vn/api/Disaster/GetDetailDisaster"

KTTV_TH_DANGEROUS_WEATHER = "https://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43"
KTTV_TH_SPECIAL_HYDROLOGY = "https://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
KTTV_BASE_URL = "https://kttv.thanhhoa.gov.vn"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

REGISTERED_CHATS = set()
LAST_ALERT_COUNT = 0
PROCESSED_VNDMS_IDS = set()
PROCESSED_KTTV_URLS = set()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

# ==================== HTML INFOGRAPHIC TEMPLATE ====================
INFOGRAPHIC_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Infographic Cảnh Báo Thời Tiết & Thiên Tai</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #ffffff; margin: 0; padding: 20px; display: flex; justify-content: center; }
        .card { background: {{ theme.bg }}; border: 2px solid {{ theme.border }}; border-radius: 20px; padding: 25px; width: 100%; max-width: 650px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6); backdrop-filter: blur(10px); }
        .header { display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid rgba(255, 255, 255, 0.15); padding-bottom: 12px; margin-bottom: 15px; }
        .brand { font-size: 13px; font-weight: bold; letter-spacing: 1.2px; color: #cbd5e1; text-transform: uppercase; }
        .disaster-title { font-size: 20px; font-weight: 800; color: {{ theme.title_color }}; margin: 10px 0; text-transform: uppercase; line-height: 1.4; }
        .badge-source { background: {{ theme.badge_bg }}; color: #ffffff; padding: 5px 14px; border-radius: 20px; font-size: 12px; font-weight: bold; display: inline-block; }
        .grid-info { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 15px 0; background: rgba(0, 0, 0, 0.25); padding: 12px; border-radius: 10px; }
        .grid-item { font-size: 13px; }
        .grid-item label { display: block; color: #94a3b8; font-size: 11px; margin-bottom: 2px; text-transform: uppercase; }
        .grid-item strong { color: #f8fafc; font-size: 13px; }
        .content-box { background: rgba(255, 255, 255, 0.05); border-left: 4px solid {{ theme.border }}; padding: 15px; border-radius: 8px; font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; word-wrap: break-word; max-height: 450px; overflow-y: auto; }
        .footer { margin-top: 18px; display: flex; justify-content: space-between; font-size: 11px; color: #64748b; border-top: 1px solid rgba(255, 255, 255, 0.1); padding-top: 10px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="brand">{{ alert.source_name }}</div>
            <div class="badge-source">📍 {{ alert.location }}</div>
        </div>
        <div class="disaster-title">{{ theme.icon }} {{ alert.title }}</div>
        <div class="grid-info">
            <div class="grid-item"><label>📍 PHẠM VI/KHU VỰC</label><strong>{{ alert.location }}</strong></div>
            <div class="grid-item"><label>🕒 THỜI GIAN PHÁT TIN</label><strong>{{ alert.time }}</strong></div>
            <div class="grid-item"><label>🏷 LOẠI BẢN TIN</label><strong>{{ alert.category }}</strong></div>
            <div class="grid-item"><label>📡 NGUỒN TRUY XUẤT</label><strong>{{ alert.domain }}</strong></div>
        </div>
        <div class="content-box">
            <strong style="color: {{ theme.title_color }}; display: block; margin-bottom: 8px;">📋 NỘI DUNG CHI TIẾT BẢN TIN:</strong>
            {{ alert.content }}
        </div>
        <div class="footer">
            <span>Thời gian quét: {{ updated_at }}</span>
            <span>Cơ quan Quản lý Khí tượng Thủy văn & PCTT</span>
        </div>
    </div>
</body>
</html>
"""

def get_disaster_theme(title, category=""):
    t = (title + " " + category).lower()
    if any(k in t for k in ["bão", "áp thấp"]):
        return {"icon": "🌀", "bg": "linear-gradient(135deg, #1e1b4b, #311b92)", "border": "#7c3aed", "title_color": "#a78bfa", "badge_bg": "#dc2626"}
    elif any(k in t for k in ["thủy văn", "lũ", "ngập", "triều cường", "sông"]):
        return {"icon": "🌊", "bg": "linear-gradient(135deg, #022c22, #065f46)", "border": "#34d399", "title_color": "#6ee7b7", "badge_bg": "#059669"}
    elif any(k in t for k in ["mưa", "mưa lớn", "dông"]):
        return {"icon": "🌧", "bg": "linear-gradient(135deg, #0c4a6e, #0369a1)", "border": "#38bdf8", "title_color": "#7dd3fc", "badge_bg": "#0284c7"}
    elif any(k in t for k in ["sạt lở", "lũ quét"]):
        return {"icon": "🏔", "bg": "linear-gradient(135deg, #3f2c20, #573823)", "border": "#f59e0b", "title_color": "#fcd34d", "badge_bg": "#d97706"}
    else:
        return {"icon": "⚠️", "bg": "linear-gradient(135deg, #1f2937, #111827)", "border": "#ef4444", "title_color": "#fca5a5", "badge_bg": "#dc2626"}

# ==================== CÀO KTTV THANH HÓA ====================
def scrape_kttv_thanhhoa_category(url, category_name):
    now_vn = datetime.utcnow() + timedelta(hours=7)
    articles = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code != 200: return articles
        soup = BeautifulSoup(res.text, 'html.parser')
        news_items = soup.find_all('a', href=True)
        valid_links = []
        for a in news_items:
            href = a['href']
            if '/tin-tuc/' in href and href not in valid_links and href != url:
                if not href.startswith('http'): href = KTTV_BASE_URL + href
                valid_links.append(href)

        for link in valid_links[:3]:
            try:
                art_res = requests.get(link, headers=HEADERS, timeout=10)
                if art_res.status_code == 200:
                    art_soup = BeautifulSoup(art_res.text, 'html.parser')
                    title_elem = art_soup.find('h1') or art_soup.find('h2') or art_soup.find('div', class_='title')
                    title = title_elem.get_text(strip=True) if title_elem else "CẢNH BÁO KTTV THANH HÓA"
                    time_elem = art_soup.find('span', class_='date') or art_soup.find('div', class_='date')
                    pub_time = time_elem.get_text(strip=True) if time_elem else now_vn.strftime('%H:%M %d/%m/%Y')

                    content_elem = art_soup.find('div', class_='content') or art_soup.find('div', class_='detail-content') or art_soup.find('article')
                    if content_elem:
                        for br in content_elem.find_all("br"): br.replace_with("\n")
                        content = content_elem.get_text(separator="\n", strip=True)
                    else:
                        content = art_soup.get_text(separator="\n", strip=True)[:1000]

                    articles.append({
                        "url": link,
                        "title": title,
                        "time": pub_time,
                        "category": category_name,
                        "location": "Tỉnh Thanh Hóa",
                        "content": content,
                        "source_name": "ĐÀI KTTV TỈNH THANH HÓA",
                        "domain": "kttv.thanhhoa.gov.vn"
                    })
            except Exception as e: print(f"Lỗi KTTV detail: {e}")
    except Exception as e: print(f"Lỗi KTTV category: {e}")
    return articles

def fetch_all_kttv_thanhhoa():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    all_news = scrape_kttv_thanhhoa_category(KTTV_TH_DANGEROUS_WEATHER, "Thời tiết nguy hiểm") + scrape_kttv_thanhhoa_category(KTTV_TH_SPECIAL_HYDROLOGY, "Thủy văn đặc biệt")
    return {"status": "success", "count": len(all_news), "alerts": all_news, "updated_at": now_vn.strftime("%H:%M %d/%m/%Y")}

# ==================== QUÉT IWEATHER ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=HEADERS, timeout=10)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}"}
        matches = re.findall(r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)', res.text, re.IGNORECASE)
        unique_locations = list(set([m.strip(' ",') for m in matches]))
        matched_alerts = [{"location": loc, "intensity": "Mây dông / Sét phát triển", "message": "Phát hiện vùng mây đối lưu nguy hiểm gây dông sét", "time": now_vn.strftime('%H:%M %d/%m/%Y')} for loc in unique_locations]
        return {"status": "success", "has_warning": len(matched_alerts) > 0, "count": len(matched_alerts), "alerts": matched_alerts, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}
    except Exception as e: return {"status": "error", "message": str(e)}

# ==================== QUÉT VNDMS (CHÍNH XÁC NỘI DUNG POPUP) ====================
def fetch_vndms_popup_data():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    alerts = []
    try:
        res = requests.get(VNDMS_LIST_API, headers=HEADERS, timeout=10)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}"}
        raw_data = res.json()
        disaster_list = raw_data.get("data", []) if isinstance(raw_data, dict) else raw_data
        if not isinstance(disaster_list, list): disaster_list = [raw_data]

        for item in disaster_list:
            if not isinstance(item, dict): continue
            event_id = str(item.get("id") or item.get("DisasterId") or item.get("code") or "")
            title = item.get("name") or item.get("title") or item.get("DisasterName") or "CẢNH BÁO THIÊN TAI"
            start_time = item.get("startDate") or item.get("time") or item.get("createdDate") or now_vn.strftime('%d/%m/%Y - %H:%M')
            location = item.get("affectedArea") or item.get("location") or item.get("khuVuc") or "Toàn quốc / Biển Đông"
            risk_level = str(item.get("riskLevel") or item.get("level") or "3")

            raw_content = ""
            if event_id:
                try:
                    d_res = requests.get(f"{VNDMS_DETAIL_API}?id={event_id}", headers=HEADERS, timeout=5)
                    if d_res.status_code == 200:
                        d_data = d_res.json().get("data", {})
                        if isinstance(d_data, dict): raw_content = d_data.get("dienBien") or d_data.get("content") or d_data.get("description") or ""
                except Exception: pass

            if not raw_content: raw_content = item.get("description") or item.get("summary") or "Đang cập nhật chi tiết..."

            clean_text = re.sub(r'<br\s*/?>', '\n', raw_content, flags=re.IGNORECASE)
            clean_text = re.sub(r'</p>', '\n', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<[^>]+>', '', clean_text).strip()

            alerts.append({
                "id": event_id,
                "title": title,
                "category": f"Thiên tai Cấp {risk_level}",
                "location": location,
                "time": start_time,
                "content": clean_text,
                "source_name": "HỆ THỐNG GIÁM SÁT THIÊN TAI (VNDMS)",
                "domain": "vndms.gov.vn"
            })
        return {"status": "success", "alerts": alerts, "updated_at": now_vn.strftime("%H:%M %d/%m/%Y")}
    except Exception as e: return {"status": "error", "message": str(e)}

# ==================== HÀM FORMAT TELEGRAM TỔNG HỢP ====================
def format_vndms_telegram(alert, updated_at):
    theme = get_disaster_theme(alert['title'], alert['category'])
    msg = f"⚡ **CẢNH BÁO THỜI TIẾT & THIÊN TAI**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 **CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS**\n"
    msg += f"📅 *Cập nhật:* {updated_at}\n\n"
    msg += f"{theme['icon']} **{alert['title'].upper()}**\n"
    msg += f"⚠️ **Cấp độ rủi ro:** {alert['category']}\n"
    msg += f"📍 **Khu vực ảnh hưởng:** {alert['location']}\n"
    msg += f"🕒 **Thời gian bắt đầu:** {alert['time']}\n\n"
    msg += f"📋 **THÔNG TIN TÓM TẮT / DIỄN BIẾN:**\n{alert['content']}\n\n"
    msg += f"🔗 [Xem trực tiếp trên VNDMS](https://vndms.gov.vn/)"
    return msg

def format_kttv_telegram(alert, updated_at):
    theme = get_disaster_theme(alert['title'], alert['category'])
    msg = f"⚡ **CẢNH BÁO THỜI TIẾT & THỦY VĂN THANH HÓA**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 **{alert['source_name']}**\n"
    msg += f"🏷 **Danh mục:** {alert['category']}\n"
    msg += f"📅 *Cập nhật:* {updated_at}\n\n"
    msg += f"{theme['icon']} **{alert['title'].upper()}**\n"
    msg += f"📍 **Khu vực:** {alert['location']}\n"
    msg += f"🕒 **Thời gian phát tin:** {alert['time']}\n\n"
    msg += f"📋 **NỘI DUNG BẢN TIN:**\n{alert['content'][:1500]}\n\n"
    msg += f"🔗 [Xem chi tiết bản tin gốc]({alert['url']})"
    return msg

def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print(f"Lỗi gửi Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

# ==================== WEB ROUTES ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT, PROCESSED_VNDMS_IDS, PROCESSED_KTTV_URLS
    
    # 1. QUÉT DÔNG SẾT IWEATHER
    iw_data = get_iweather_storm_warning("Thanh Hóa")
    if iw_data.get("status") == "success":
        current_count = iw_data.get("count", 0)
        if iw_data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚠️ **CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SẾT TẠI THANH HÓA!**\n"
            msg += f"🕒 *Thời gian quét:* {iw_data['updated_at']}\n"
            msg += f"📍 **Số khu vực phát hiện:** {current_count}\n"
            for idx, alert in enumerate(iw_data['alerts'], 1):
                msg += f"\n**{idx}.** {alert['location']}\n"
            msg += "\n🌐 Radar iWeather: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            broadcast_alert(msg)
            LAST_ALERT_COUNT = current_count
        elif not iw_data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    # 2. QUÉT THIÊN TAI VNDMS (Đã bổ sung chuẩn định dạng & gửi tới từng Chat ID)
    vndms_data = fetch_vndms_popup_data()
    if vndms_data.get("status") == "success":
        for alert in vndms_data.get("alerts", []):
            event_key = alert["id"] if alert["id"] else alert["title"]
            if event_key not in PROCESSED_VNDMS_IDS:
                msg = format_vndms_telegram(alert, vndms_data["updated_at"])
                broadcast_alert(msg)
                PROCESSED_VNDMS_IDS.add(event_key)

    # 3. QUÉT KTTV THANH HÓA (2 LINK)
    kttv_data = fetch_all_kttv_thanhhoa()
    if kttv_data.get("status") == "success":
        for alert in kttv_data.get("alerts", []):
            if alert["url"] not in PROCESSED_KTTV_URLS:
                msg = format_kttv_telegram(alert, kttv_data["updated_at"])
                broadcast_alert(msg)
                PROCESSED_KTTV_URLS.add(alert["url"])

    return jsonify({
        "status": "running",
        "active_chats": list(REGISTERED_CHATS),
        "processed_vndms_events": len(PROCESSED_VNDMS_IDS),
        "processed_kttv_articles": len(PROCESSED_KTTV_URLS)
    })

# ROUTE INFOGRAPHIC HTML (ĐỘNG CHO CẢ VNDMS & KTTV)
@app.route('/infographic')
def show_infographic():
    # Ưu tiên lấy tin VNDMS mới nhất, nếu không có lấy tin KTTV
    vndms_data = fetch_vndms_popup_data()
    alert_item = None
    if vndms_data.get("status") == "success" and vndms_data.get("alerts"):
        alert_item = vndms_data["alerts"][0]

    if not alert_item:
        kttv_data = fetch_all_kttv_thanhhoa()
        if kttv_data.get("status") == "success" and kttv_data.get("alerts"):
            alert_item = kttv_data["alerts"][0]

    if not alert_item:
        alert_item = {
            "title": "ÁP THẤP NHIỆT ĐỚI TRÊN BIỂN ĐÔNG",
            "category": "Thiên tai Cấp 3",
            "location": "Khu vực Vịnh Bắc Bộ",
            "time": "07/08/2026 - 09:00",
            "content": "- Sáng nay (07/8), vùng áp thấp trên khu vực Vịnh Bắc Bộ đã mạnh lên thành áp thấp nhiệt đới...\n- Trong 3 giờ qua, áp thấp nhiệt đới hầu như ít di chuyển, sức gió mạnh nhất vùng gần tâm áp thấp nhiệt đới mạnh cấp 6 (39-49km/h), giật cấp 8.",
            "source_name": "HỆ THỐNG GIÁM SÁT THIÊN TAI (VNDMS)",
            "domain": "vndms.gov.vn"
        }

    theme = get_disaster_theme(alert_item["title"], alert_item["category"])
    return render_template_string(
        INFOGRAPHIC_TEMPLATE,
        alert=alert_item,
        theme=theme,
        updated_at=datetime.now().strftime("%H:%M %d/%m/%Y")
    )

# WEBHOOK TELEGRAM
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        REGISTERED_CHATS.add(chat_id)

        if text.startswith("/dong") or text.startswith("/dongset"):
            send_telegram_message(chat_id, "⚡ Đang quét dữ liệu dông sét iWeather...")
            data = get_iweather_storm_warning("Thanh Hóa")
            if data.get("has_warning"):
                msg = f"🚨 **CẢNH BÁO DÔNG SẾT THANH HÓA!**\n📍 Phát hiện {data['count']} khu vực có mây dông:"
                for idx, a in enumerate(data['alerts'], 1): msg += f"\n**{idx}.** {a['location']}"
            else:
                msg = f"✅ **AN TOÀN:** Chưa phát hiện ổ dông sét tại Thanh Hóa."
            send_telegram_message(chat_id, msg)

        elif text.startswith("/vndms") or text.startswith("/thientai"):
            send_telegram_message(chat_id, "📡 Đang quét dữ liệu thiên tai VNDMS...")
            vndms_data = fetch_vndms_popup_data()
            if vndms_data.get("alerts"):
                for alert in vndms_data["alerts"]:
                    send_telegram_message(chat_id, format_vndms_telegram(alert, vndms_data["updated_at"]))
            else:
                send_telegram_message(chat_id, "✅ **VNDMS:** Hiện không có sự kiện thiên tai diện rộng.")

        elif text.startswith("/kttv") or text.startswith("/thanhhoa") or text.startswith("/start"):
            send_telegram_message(chat_id, "📡 Đang cào dữ liệu mới nhất từ Đài KTTV Thanh Hóa...")
            kttv_data = fetch_all_kttv_thanhhoa()
            if kttv_data.get("alerts"):
                for alert in kttv_data["alerts"]:
                    send_telegram_message(chat_id, format_kttv_telegram(alert, kttv_data["updated_at"]))
            else:
                send_telegram_message(chat_id, "✅ Hiện chưa có bản tin thời tiết/thủy văn mới trên KTTV Thanh Hóa.")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
