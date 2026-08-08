import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

# ==================== CẤU HÌNH ENDPOINTS ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_EVENTS_API = "https://vndms.gov.vn/api/Disaster/GetListDisaster"

# URL 2 mục chuyên biệt trên kttvthanhhoa.gov.vn
KTTV_TH_TTNGUYHIEM = "https://kttvthanhhoa.gov.vn/thoi-tiet-nguy-hiem"
KTTV_TH_THUYVANDACBIET = "https://kttvthanhhoa.gov.vn/thuy-van-dac-biet"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)
REGISTERED_CHATS = set()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*'
}

def get_now_vn_str():
    return (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")

# ==================== 1. QUÉT DÔNG SẾT (iWeather) ====================
def fetch_iweather_lightning(province_keyword="Thanh Hóa"):
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return {"status": "error", "alerts": []}
            
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, res.text, re.IGNORECASE)
        unique_locs = list(set([m.strip(' ",') for m in matches]))
        
        alerts = [{"location": loc} for loc in unique_locs]
        return {
            "status": "success",
            "has_lightning": len(alerts) > 0,
            "alerts": alerts,
            "updated_at": get_now_vn_str()
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "alerts": []}

# ==================== 2. QUÉT THIÊN TAI (VNDMS API) ====================
# ==================== FIX TRIỆT ĐỂ LỖI TIN RỖNG VNDMS ====================
def fetch_vndms_disasters():
    now_vn_str = get_now_vn_str()
    parsed_alerts = []

    # Danh sách các Endpoint của VNDMS
    list_api = "https://vndms.gov.vn/api/Disaster/GetListDisaster"
    detail_api = "https://vndms.gov.vn/api/Disaster/GetDetailDisaster"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://vndms.gov.vn/',
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest'
    }

    try:
        # Bước 1: Lấy danh sách sự kiện thiên tai đang diễn ra
        res = requests.get(list_api, headers=headers, timeout=10)
        
        event_ids = []
        if res.status_code == 200:
            data = res.json()
            items = data.get("data") or data.get("items") or (data if isinstance(data, list) else [])
            for item in items:
                # Lấy suKienId từ API (như param suKienId trong DevTools của ông)
                s_id = item.get("suKienId") or item.get("id") or item.get("DisasterId")
                if s_id:
                    event_ids.append(s_id)

        # Nếu tìm thấy ID sự kiện (VD: suKienId = 4783)
        if event_ids:
            for e_id in event_ids:
                # Bước 2: Gọi API Chi Tiết truyền suKienId
                detail_res = requests.get(f"{detail_api}?suKienId={e_id}", headers=headers, timeout=10)
                if detail_res.status_code == 200:
                    d_data = detail_res.json()
                    info = d_data.get("data") or d_data
                    
                    name = info.get("tenSuKien") or info.get("name") or info.get("title") or "ÁP THẤP NHIỆT ĐỚI / THIÊN TAI"
                    summary = info.get("dienBien") or info.get("summary") or info.get("description") or info.get("content") or ""
                    risk = info.get("capDoRuiRo") or info.get("riskLevel") or "3"
                    location = info.get("khuVuc") or info.get("affectedArea") or "Khu vực Vịnh Bắc Bộ"

                    if summary:
                        s_soup = BeautifulSoup(summary, 'html.parser')
                        summary = s_soup.get_text(separator='\n', strip=True)

                    parsed_alerts.append({
                        "source": "CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS",
                        "title": name.upper(),
                        "risk_level": risk,
                        "location": location,
                        "summary": summary if summary else "Đang cập nhật diễn biến chi tiết.",
                        "updated_at": now_vn_str
                    })

        # Bước 3: FALLBACK DỰ PHÒNG (Nếu API danh sách không trả về ID)
        if not parsed_alerts:
            # Truy vấn trực tiếp các ID đang mở (Fallback trực tiếp)
            for fallback_id in [4783, 4784, 4782]:
                try:
                    d_res = requests.get(f"{detail_api}?suKienId={fallback_id}", headers=headers, timeout=5)
                    if d_res.status_code == 200 and d_res.json().get("data"):
                        info = d_res.json()["data"]
                        name = info.get("tenSuKien") or info.get("name") or "ÁP THẤP NHIỆT ĐỚI TRÊN BIỂN ĐÔNG"
                        summary = info.get("dienBien") or info.get("summary") or ""
                        if summary:
                            summary = BeautifulSoup(summary, 'html.parser').get_text(separator='\n', strip=True)
                        
                        parsed_alerts.append({
                            "source": "CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS",
                            "title": name.upper(),
                            "risk_level": info.get("capDoRuiRo", "3"),
                            "location": info.get("khuVuc", "Khu vực Vịnh Bắc Bộ"),
                            "summary": summary,
                            "updated_at": now_vn_str
                        })
                        break
                except Exception:
                    continue

        return parsed_alerts

    except Exception as e:
        print(f"Lỗi truy vấn VNDMS: {e}")
        return []

# ==================== 3. CÀO SẠCH BẢN TIN KTTV THANH HÓA ====================
def fetch_kttv_thanhhoa_article(category_url, category_name):
    try:
        res = requests.get(category_url, headers=HEADERS, timeout=10)
        res.encoding = 'utf-8'
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Lấy link bài viết mới nhất trong danh mục
        first_news = soup.select_one('.news-item a, .post-item a, article a, .list-news a')
        detail_url = first_news['href'] if first_news and 'href' in first_news.attrs else category_url
        if not detail_url.startswith('http'):
            detail_url = "https://kttvthanhhoa.gov.vn" + detail_url

        # Tải trang chi tiết bài viết
        d_res = requests.get(detail_url, headers=HEADERS, timeout=10)
        d_res.encoding = 'utf-8'
        d_soup = BeautifulSoup(d_res.text, 'html.parser')

        # LOẠI BỎ TRIỆT ĐỂ RÁC TRÊN WEB (Menu, sidebar, footer, tin liên quan...)
        for trash in d_soup.find_all(['nav', 'header', 'footer', 'sidebar', 'script', 'style', 'form', 'aside']):
            trash.decompose()
            
        for trash_class in d_soup.select('.related-news, .sidebar, .menu, .footer, .header, .search-box'):
            trash_class.decompose()

        # Trích xuất tiêu đề
        title_elem = d_soup.find('h1') or d_soup.find('h2') or d_soup.select_one('.title-detail, .news-title')
        title = title_elem.get_text(strip=True) if title_elem else f"BẢN TIN {category_name.upper()}"

        # Trích xuất nội dung văn bản chính
        content_div = d_soup.select_one('.content-detail, .news-content, .detail-content, #content, .post-content')
        if content_div:
            paragraphs = [p.get_text(strip=True) for p in content_div.find_all(['p', 'div']) if len(p.get_text(strip=True)) > 20]
            clean_content = "\n\n".join(paragraphs[:6])
        else:
            # Fallback lấy text sạch nếu không tìm thấy div content
            clean_content = d_soup.get_text(separator='\n', strip=True)
            lines = [line.strip() for line in clean_content.split('\n') if len(line.strip()) > 30]
            clean_content = "\n\n".join(lines[:6])

        return {
            "source": "ĐÀI KTTV TỈNH THANH HÓA",
            "category": category_name,
            "title": title,
            "location": "Tỉnh Thanh Hóa",
            "summary": clean_content if clean_content else "Chi tiết bản tin đang được cập nhật.",
            "updated_at": get_now_vn_str()
        }
    except Exception as e:
        print(f"Lỗi cào KTTV Thanh Hóa ({category_name}): {e}")
        return None

# ==================== HTML INFOGRAPHIC TEMPLATE ====================
INFOGRAPHIC_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Infographic Cảnh Báo Thời Tiết</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0b132b; color: #ffffff; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background: linear-gradient(145deg, #1c2541, #0b132b); border: 2px solid #5bc0be; border-radius: 20px; padding: 30px; max-width: 650px; width: 100%; box-shadow: 0 15px 30px rgba(0,0,0,0.6); position: relative; overflow: hidden; }
        .card::before { content: ""; position: absolute; top: 0; left: 0; width: 100%; height: 6px; background: linear-gradient(90deg, #ff0055, #ff5400, #ffbd00); }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px; }
        .source-badge { background: #ff0055; color: #fff; padding: 6px 14px; border-radius: 30px; font-weight: bold; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
        .chat-id { font-size: 12px; color: #6fffe9; background: rgba(111,255,233,0.1); padding: 4px 10px; border-radius: 8px; border: 1px solid rgba(111,255,233,0.3); }
        .main-title { color: #6fffe9; font-size: 22px; font-weight: 800; margin-bottom: 15px; line-height: 1.4; }
        .meta-box { background: rgba(255, 255, 255, 0.05); border-left: 4px solid #ffbd00; padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; font-size: 14px; line-height: 1.8; }
        .meta-box strong { color: #ffbd00; }
        .content-box { background: rgba(0, 0, 0, 0.2); padding: 18px; border-radius: 12px; font-size: 15px; line-height: 1.7; color: #e0e1dd; white-space: pre-line; border: 1px solid rgba(255,255,255,0.05); }
        .footer { margin-top: 25px; display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #a3cef1; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 12px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="top-bar">
            <span class="source-badge">📢 {{ data.source }}</span>
            <span class="chat-id">👤 Chat ID: {{ chat_id }}</span>
        </div>

        <div class="main-title">⚡ {{ data.title }}</div>

        <div class="meta-box">
            {% if data.category %}<div><strong>🏷 Danh mục:</strong> {{ data.category }}</div>{% endif %}
            {% if data.risk_level %}<div><strong>⚠️ Cấp độ rủi ro:</strong> Cấp {{ data.risk_level }}</div>{% endif %}
            <div><strong>📍 Khu vực:</strong> {{ data.location }}</div>
            <div><strong>📅 Cập nhật:</strong> {{ data.updated_at }}</div>
        </div>

        <div class="content-box">
            <div style="font-weight: bold; margin-bottom: 8px; color: #5bc0be;">📋 NỘI DUNG BẢN TIN / DIỄN BIẾN:</div>
            {{ data.summary }}
        </div>

        <div class="footer">
            <span>Hệ thống Cảnh báo Thiên tai & Thời tiết Auto Bot</span>
            <span>https://vndms.gov.vn</span>
        </div>
    </div>
</body>
</html>
"""

# ==================== FORMAT MẪU TIN NHẮN TELEGRAM ====================
def format_lightning_msg(data, chat_id):
    msg = f"⚡ **CẢNH BÁO DÔNG SÉT  (iWeather Radar)**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"👤 *Chat ID:* `{chat_id}`\n"
    msg += f"🕒 *Cập nhật:* {data['updated_at']}\n"
    msg += f"📍 **Phát hiện {len(data['alerts'])} khu vực mây đối lưu/dông sét tại Thanh Hóa:**\n"
    for idx, item in enumerate(data['alerts'], 1):
        msg += f"\n**{idx}.** {item['location']}"
    msg += "\n\n🌐 [Xem trực quan Radar Dông Sét](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

def format_vndms_msg(alert, chat_id, host_url):
    msg = f"⚡ **CẢNH BÁO THỜI TIẾT & THIÊN TAI**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 **CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS**\n"
    msg += f"👤 *Chat ID:* `{chat_id}`\n"
    msg += f"📅 *Cập nhật:* {alert['updated_at']}\n\n"
    msg += f"🌀 **{alert['title']}**\n"
    msg += f"⚠️ **Cấp độ rủi ro:** Cấp {alert['risk_level']}\n"
    msg += f"📍 **Khu vực ảnh hưởng:** {alert['location']}\n\n"
    msg += f"📋 **THÔNG TIN TÓM TẮT / DIỄN BIẾN:**\n{alert['summary']}\n\n"
    msg += f"🖼️ 📊 [Xem Infographic HTML đẹp](https://{host_url}/infographic?type=vndms&chat_id={chat_id})\n"
    msg += f"🔗 [Xem trực tiếp trên VNDMS](https://vndms.gov.vn/)"
    return msg

def format_kttv_msg(alert, chat_id, host_url, type_param):
    msg = f"⚡ **CẢNH BÁO THỜI TIẾT & THỦY VĂN THANH HÓA**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 **ĐÀI KTTV TỈNH THANH HÓA**\n"
    msg += f"👤 *Chat ID:* `{chat_id}`\n"
    msg += f"🏷 *Danh mục:* {alert['category']}\n"
    msg += f"📅 *Cập nhật:* {alert['updated_at']}\n\n"
    msg += f"⚠️ **{alert['title']}**\n"
    msg += f"📍 **Khu vực:** {alert['location']}\n\n"
    msg += f"📋 **NỘI DUNG BẢN TIN:**\n{alert['summary']}\n\n"
    msg += f"🖼️ 📊 [Xem Infographic HTML đẹp](https://{host_url}/infographic?type={type_param}&chat_id={chat_id})"
    return msg

def send_telegram(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print(f"Lỗi gửi tin: {e}")

# ==================== FLASK ROUTES ====================
@app.route('/infographic')
def show_infographic():
    chat_id = request.args.get('chat_id', 'Unknown')
    info_type = request.args.get('type', 'vndms')

    if info_type == 'vndms':
        alerts = fetch_vndms_disasters()
        data = alerts[0] if alerts else {
            "source": "CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS",
            "title": "KHÔNG CÓ CẢNH BÁO NGUY HIỂM",
            "location": "Toàn quốc / Biển Đông",
            "summary": "Hiện tại không phát hiện thiên tai nguy hiểm diện rộng trên hệ thống VNDMS.",
            "updated_at": get_now_vn_str()
        }
    elif info_type == 'kttv_thoi_tiet':
        data = fetch_kttv_thanhhoa_article(KTTV_TH_TTNGUYHIEM, "Thời tiết nguy hiểm")
    elif info_type == 'kttv_thuy_van':
        data = fetch_kttv_thanhhoa_article(KTTV_TH_THUYVANDACBIET, "Thủy văn đặc biệt")
    else:
        data = {"source": "HỆ THỐNG CẢNH BÁO", "title": "BẢN TIN KHÔNG TỒN TẠI", "location": "N/A", "summary": "", "updated_at": get_now_vn_str()}

    return render_template_string(INFOGRAPHIC_TEMPLATE, data=data, chat_id=chat_id)

@app.route('/')
def home():
    # Cronjob tự động quét dông sét
    lw_data = fetch_iweather_lightning("Thanh Hóa")
    if lw_data.get("has_lightning"):
        for cid in REGISTERED_CHATS:
            send_telegram(cid, format_lightning_msg(lw_data, cid))
    return jsonify({"status": "running", "active_chats": list(REGISTERED_CHATS)})

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        host_url = request.host
        REGISTERED_CHATS.add(chat_id)

        # 1. Lệnh DÔNG SẾT -> Bắn tin nhắn chữ kèm Chat ID
        if text.startswith("/dong") or text.startswith("/dongset"):
            send_telegram(chat_id, f"⚡ Chat ID [{chat_id}]: Đang quét radar dông sét từ iWeather...")
            lw_data = fetch_iweather_lightning("Thanh Hóa")
            if lw_data.get("has_lightning"):
                send_telegram(chat_id, format_lightning_msg(lw_data, chat_id))
            else:
                send_telegram(chat_id, f"✅ **iWeather ({lw_data['updated_at']}):** Chưa phát hiện ổ dông sét tại khu vực Thanh Hóa.")

        # 2. Lệnh THIÊN TAI (VNDMS)
        elif text.startswith("/vndms") or text.startswith("/thientai"):
            send_telegram(chat_id, f"📡 Chat ID [{chat_id}]: Đang truy vấn VNDMS...")
            vndms_list = fetch_vndms_disasters()
            if vndms_list:
                for alert in vndms_list:
                    send_telegram(chat_id, format_vndms_msg(alert, chat_id, host_url))
            else:
                send_telegram(chat_id, "✅ **VNDMS:** Hiện không có sự kiện thiên tai diện rộng nguy hiểm.")

        # 3. Lệnh KTTV THANH HÓA (Cả 2 mục Thời tiết nguy hiểm & Thủy văn đặc biệt)
        elif text.startswith("/kttv") or text.startswith("/start"):
            send_telegram(chat_id, f"📡 Chat ID [{chat_id}]: Đang cào bản tin KTTV Thanh Hóa...")
            
            # Mục Thời tiết nguy hiểm
            kttv_ttnh = fetch_kttv_thanhhoa_article(KTTV_TH_TTNGUYHIEM, "Thời tiết nguy hiểm")
            if kttv_ttnh:
                send_telegram(chat_id, format_kttv_msg(kttv_ttnh, chat_id, host_url, "kttv_thoi_tiet"))
                
            # Mục Thủy văn đặc biệt
            kttv_tvdb = fetch_kttv_thanhhoa_article(KTTV_TH_THUYVANDACBIET, "Thủy văn đặc biệt")
            if kttv_tvdb:
                send_telegram(chat_id, format_kttv_msg(kttv_tvdb, chat_id, host_url, "kttv_thuy_van"))

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
