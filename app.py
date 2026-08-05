import os
import re
import io
import requests
import sqlite3
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, request, jsonify

# ==================== CẤU HÌNH HỆ THỐNG ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6Klgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# 3 Nguồn Dữ Liệu Cảnh Báo
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
URL_THOI_TIET = "http://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43"
URL_THUY_VAN = "http://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
URL_LU_QUET_API = "https://luquetsatlo.nchmf.gov.vn/Home/InitTrongDiemTheoSLLQ"

app = Flask(__name__)
REGISTERED_CHATS = set()
LAST_ALERT_COUNT = 0

# ==================== 1. DATABASE (SỬA TRIỆT ĐỂ LỖI RENDER) ====================
def init_db():
    """Tự động khởi tạo bảng SQLite ngay khi ứng dụng nạp"""
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            url TEXT PRIMARY KEY,
            title TEXT,
            sent_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

# CHẠY NGAY TẠI CẤP MODULE -> Đảm bảo Gunicorn/Render không bị thiếu table
init_db()

def is_news_sent(url):
    init_db() # Kiểm tra an toàn trước mỗi truy vấn
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_news WHERE url = ?', (url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_sent_news(url, title):
    init_db()
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT OR IGNORE INTO sent_news (url, title, sent_at) VALUES (?, ?, ?)', (url, title, now_str))
    conn.commit()
    conn.close()

# ==================== 2. HÀM GỬI THÔNG BÁO TELEGRAM ====================
def send_telegram_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"❌ Lỗi gửi tin nhắn: {e}")

def send_telegram_photo(chat_id, image_bytes, caption):
    try:
        files = {'photo': ('infographic.png', image_bytes, 'image/png')}
        payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        requests.post(f"{TELEGRAM_API_URL}/sendPhoto", data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"❌ Lỗi gửi ảnh Telegram: {e}")

def broadcast_message(text):
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

def broadcast_photo(image_bytes, caption):
    for chat_id in REGISTERED_CHATS:
        image_bytes.seek(0)
        send_telegram_photo(chat_id, image_bytes, caption)

# ==================== 3. ENGINE VẼ INFOGRAPHIC ĐỘNG NÂNG CAO ====================
def create_infographic(category_title, article_title, parsed_data, date_str):
    width = 900
    locations = parsed_data.get('locations', [])
    summary = parsed_data.get('summary', [])
    
    # Dynamic Height Adjustment: Tự co giãn theo lượng địa bàn & tóm tắt
    loc_rows = (len(locations) + 2) // 3
    added_height = (loc_rows * 38) + (len(summary) * 28)
    height = max(580, 420 + added_height)

    image = Image.new('RGB', (width, height), color='#0f172a')
    draw = ImageDraw.Draw(image)

    # Phối màu viền theo dạng cảnh báo
    if "LŨ QUÉT" in category_title.upper() or "SẠT LỞ" in category_title.upper():
        border_color = '#f59e0b' # Cam hổ phách
    elif "THỜI TIẾT" in category_title.upper() or "DỒNG SÉ" in category_title.upper():
        border_color = '#ef4444' # Đỏ
    else:
        border_color = '#0284c7' # Xanh biển

    # Viền ngoài & Header Banner
    draw.rectangle([15, 15, width-15, height-15], outline=border_color, width=4)
    draw.rectangle([15, 15, width-15, 90], fill=border_color)
    
    # Load Font hệ thống
    font_header = font_title = font_bold = font_sub = None
    font_paths = ["arialbd.ttf", "arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for path in font_paths:
        try:
            font_header = ImageFont.truetype(path, 22)
            font_title = ImageFont.truetype(path, 18)
            font_bold = ImageFont.truetype(path, 16)
            font_sub = ImageFont.truetype(path, 14)
            break
        except:
            continue
    if not font_header:
        font_header = font_title = font_bold = font_sub = ImageFont.load_default()

    # Header Title
    draw.text((30, 32), f"🚨 CẢNH BÁO TỰ ĐỘNG: {category_title.upper()}", fill='#ffffff', font=font_header)
    draw.text((width - 240, 38), f"🕒 {date_str}", fill='#ffffff', font=font_sub)

    # Wrap Tiêu Đề Bài Viết
    draw.text((30, 110), "NỘI DUNG CẢNH BÁO / DỰ BÁO:", fill='#38bdf8', font=font_sub)
    words = article_title.split()
    lines, current_line = [], ""
    for word in words:
        if len(current_line + " " + word) < 52:
            current_line += " " + word
        else:
            lines.append(current_line.strip())
            current_line = word
    if current_line:
        lines.append(current_line.strip())

    y_pos = 135
    for line in lines[:2]:
        draw.text((30, y_pos), line, fill='#ffffff', font=font_title)
        y_pos += 26

    # 2 Box Chỉ Số Metrics Cards
    y_pos += 12
    draw.rectangle([30, y_pos, 430, y_pos+65], fill='#1e293b', outline='#334155', width=2)
    draw.text((45, y_pos+10), "🌧️ THÔNG SỐ CẢNH BÁO", fill='#94a3b8', font=font_sub)
    draw.text((45, y_pos+32), parsed_data.get('rainfall', 'Theo dõi chi tiết'), fill='#38bdf8', font=font_bold)

    draw.rectangle([450, y_pos, 870, y_pos+65], fill='#1e293b', outline='#334155', width=2)
    draw.text((465, y_pos+10), "⚠️ CẤP ĐỘ RỦI RO THIÊN TAI", fill='#94a3b8', font=font_sub)
    draw.text((465, y_pos+32), parsed_data.get('risk_level', 'CẤP 1'), fill='#f43f5e', font=font_bold)

    # Danh sách thẻ địa bàn Badges (Tự sắp xếp dòng)
    y_pos += 85
    if locations:
        draw.text((30, y_pos), "📍 CÁC KHU VỰC / XÃ / PHƯỜNG CÓ NGUY CƠ CAO:", fill='#f59e0b', font=font_sub)
        y_pos += 25
        x_start, x_curr = 30, 30
        for loc in locations:
            badge_text = f"📍 {loc}"
            box_width = len(badge_text) * 10 + 20
            if x_curr + box_width > width - 40:
                x_curr = x_start
                y_pos += 36
            draw.rectangle([x_curr, y_pos, x_curr + box_width, y_pos + 28], fill='#334155', outline='#475569')
            draw.text((x_curr + 8, y_pos + 5), badge_text, fill='#e2e8f0', font=font_sub)
            x_curr += box_width + 10
        y_pos += 40

    # Tóm tắt diễn biến
    if summary:
        draw.text((30, y_pos), "📝 TÓM TẮT DIỄN BIẾN:", fill='#38bdf8', font=font_sub)
        y_pos += 25
        for s_line in summary[:3]:
            draw.text((40, y_pos), f"• {s_line[:85]}...", fill='#cbd5e1', font=font_sub)
            y_pos += 24

    # Footer
    draw.line([(30, height - 40), (width - 30, height - 40)], fill='#334155', width=1)
    draw.text((30, height - 30), "🌐 Trích xuất dữ liệu & thiết kế Infographic tự động", fill='#64748b', font=font_sub)

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

# ==================== DẠNG 1: DÔNG SÉT TỰ ĐỘNG (IWEATHER) ====================
def get_iweather_storm_warning():
    headers = {'User-Agent': 'Mozilla/5.0'}
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code == 200:
            raw_text = res.text
            pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
            matches = re.findall(pattern, raw_text, re.IGNORECASE)
            unique_locations = list(set([m.strip(' ",') for m in matches]))

            return {
                "has_warning": len(unique_locations) > 0,
                "count": len(unique_locations),
                "locations": unique_locations,
                "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
            }
    except Exception as e:
        print(f"❌ Lỗi iWeather: {e}")
    return {"has_warning": False, "count": 0, "locations": []}

# ==================== DẠNG 2: WEB ĐÀI KTTV THANH HÓA ====================
def scrape_kttv_thanh_hoa(url, category_name):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            article_link = None
            for a in soup.find_all('a', href=True):
                text = a.text.strip()
                if '/tin-tuc/' in a['href'] and len(text) > 15 and "XEM THÊM" not in text.upper():
                    article_link = a
                    break

            if article_link:
                title = article_link.text.strip()
                full_link = article_link['href']
                if not full_link.startswith('http'):
                    full_link = "http://kttv.thanhhoa.gov.vn" + full_link

                if not is_news_sent(full_link):
                    now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime('%H:%M %d/%m/%Y')
                    
                    # Bóc tách nội dung bài viết
                    detail_res = requests.get(full_link, headers=headers, timeout=10)
                    full_text = detail_res.text if detail_res.status_code == 200 else ""
                    
                    rain_match = re.search(r'(\d+\s*[-–]\s*\d+\s*mm|\d+\s*mm)', full_text, re.IGNORECASE)
                    rainfall = rain_match.group(1) if rain_match else "Có mưa vừa đến mưa to"
                    
                    # Trích xuất địa bàn có trong bài
                    districts = ["Mường Lát", "Quan Sơn", "Quan Hóa", "Bá Thước", "Lang Chánh", "Thường Xuân", "Ngọc Lặc", "Như Xuân", "Cẩm Thủy", "Thạch Thành"]
                    found_locs = [d for d in districts if re.search(r'\b' + re.escape(d) + r'\b', full_text, re.IGNORECASE)]
                    if not found_locs:
                        found_locs = ["Toàn tỉnh Thanh Hóa"]

                    parsed_data = {
                        "rainfall": rainfall,
                        "risk_level": "CẤP 1",
                        "locations": found_locs,
                        "summary": [title]
                    }
                    
                    img_bytes = create_infographic(category_name, title, parsed_data, now_vn)
                    caption = f"⚠️ **CẢNH BÁO MỚI TỪ ĐÀI KTTV THANH HÓA**\n\n📌 **{title}**\n\n🔗 [Xem bản tin gốc]({full_link})"
                    
                    broadcast_photo(img_bytes, caption)
                    save_sent_news(full_link, title)
                    print(f"✅ Đã gửi Infographic KTTV Thanh Hóa: {title}")
    except Exception as e:
        print(f"❌ Lỗi cào KTTV Thanh Hóa: {e}")

# ==================== DẠNG 3: WEB LŨ QUÉT SẠT LỞ CẤP XÃ (NCHMF) ====================
def scrape_lu_quet_sat_lo_cap_xa():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://luquetsatlo.nchmf.gov.vn/'
    }
    try:
        res = requests.post(URL_LU_QUET_API, headers=headers, timeout=12)
        if res.status_code == 200:
            data = res.json()
            active_communes = []
            features = data if isinstance(data, list) else data.get("features", [])
            
            thanh_hoa_districts = ["mường lát", "quan sơn", "quan hóa", "bá thước", "lang chánh", "thường xuân", "ngọc lặc", "như xuân", "như thanh", "cẩm thủy", "thạch thành"]

            for item in features:
                props = item.get("properties", item) if isinstance(item, dict) else {}
                province = str(props.get("TenTinh") or props.get("Province") or props.get("PROVINCE") or "").strip()
                district = str(props.get("TenHuyen") or props.get("District") or props.get("DISTRICT") or "").strip()
                commune = str(props.get("TenXa") or props.get("Commune") or props.get("COMMUNE") or props.get("NAME") or "").strip()
                
                is_thanh_hoa = "thanh hóa" in province.lower() or "thanh hoá" in province.lower()
                is_thuyen_thanh_hoa = any(d in district.lower() for d in thanh_hoa_districts)

                if (is_thanh_hoa or is_thuyen_thanh_hoa) and commune:
                    c_label = f"Xã {commune}" + (f" ({district})" if district else "")
                    if c_label not in active_communes:
                        active_communes.append(c_label)

            if active_communes:
                now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime('%H:%M %d/%m/%Y')
                alert_id = f"luquet_th_{len(active_communes)}_{datetime.now().strftime('%Y%m%d%H')}"
                
                if not is_news_sent(alert_id):
                    title = f"CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT TẠI {len(active_communes)} XÃ THUỘC THANH HÓA"
                    parsed_data = {
                        "rainfall": "Cảnh báo nguy cơ trong 06h tới",
                        "risk_level": "CẤP 1 - MỨC TRUNG BÌNH/CAO",
                        "locations": active_communes,
                        "summary": [f"Mô hình dự báo ghi nhận {len(active_communes)} xã tại Thanh Hóa có nguy cơ lũ quét và sạt lở đất."]
                    }
                    
                    img_bytes = create_infographic("CẢNH BÁO LŨ QUÉT & SẠT LỞ CẤP XÃ", title, parsed_data, now_vn)
                    
                    caption = f"🚨 **CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT (CẤP XÃ)**\n"
                    caption += f"🕒 *Thời gian cập nhật:* {now_vn}\n"
                    caption += f"📍 **Số xã nguy cơ:** {len(active_communes)} xã\n\n"
                    caption += "🔻 **DANH SÁCH CHI TIẾT CÁC XÃ:**\n"
                    for idx, xa in enumerate(active_communes, 1):
                        caption += f"  {idx}. {xa}\n"
                    caption += "\n🌐 [Xem bản đồ trực quan NCHMF](https://luquetsatlo.nchmf.gov.vn)"
                    
                    broadcast_photo(img_bytes, caption)
                    save_sent_news(alert_id, title)
                    print(f"✅ Đã quét động & gửi cảnh báo lũ quét cho {len(active_communes)} xã!")
    except Exception as e:
        print(f"❌ Lỗi cào Lũ quét cấp xã: {e}")

# ==================== 4. ROUTES SERVER & CONTROLLER ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT
    
    # 1. Chạy Dạng 1: Dông sét iWeather
    iweather_data = get_iweather_storm_warning()
    if iweather_data["has_warning"] and iweather_data["count"] != LAST_ALERT_COUNT:
        msg = f"⚡ **CẢNH BÁO DỒNG SÉ TỰ ĐỘNG TẠI THANH HÓA!**\n"
        msg += f"🕒 *Thời gian:* {iweather_data['updated_at']}\n"
        msg += f"📍 **Số khu vực phát hiện:** {iweather_data['count']}\n"
        for idx, loc in enumerate(iweather_data['locations'], 1):
            msg += f"\n**{idx}.** {loc}\n"
        msg += "\n🌐 Xem Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
        broadcast_message(msg)
        LAST_ALERT_COUNT = iweather_data["count"]
    elif not iweather_data["has_warning"]:
        LAST_ALERT_COUNT = 0

    # 2. Chạy Dạng 2: Web Đài KTTV Thanh Hóa (Thời tiết & Thủy văn)
    scrape_kttv_thanh_hoa(URL_THOI_TIET, "Thời Tiết Nguy Hiểm")
    scrape_kttv_thanh_hoa(URL_THUY_VAN, "Thủy Văn Đặc Biệt")

    # 3. Chạy Dạng 3: Web Lũ Quét Sạt Lở Cấp Xã (NCHMF)
    scrape_lu_quet_sat_lo_cap_xa()

    return jsonify({
        "status": "system_running",
        "registered_chats": list(REGISTERED_CHATS),
        "iweather_alert_count": LAST_ALERT_COUNT
    })

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        REGISTERED_CHATS.add(chat_id)
        if text.startswith("/start") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "⚡ Bot đã sẵn sàng! Đang lắng nghe và tự động gửi Infographic cho cả 3 dạng cảnh báo.")
    return "OK", 200

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
