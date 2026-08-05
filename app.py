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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Các nguồn dữ liệu
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
URL_THOI_TIET = "http://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43"
URL_THUY_VAN = "http://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
URL_LU_QUET_API = "https://luquetsatlo.nchmf.gov.vn/Home/InitTrongDiemTheoSLLQ"

app = Flask(__name__)

REGISTERED_CHATS = set()
LAST_ALERT_COUNT = 0

# ==================== 1. KHỞI TẠO DATABASE ====================
def init_db():
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

def is_news_sent(url):
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_news WHERE url = ?', (url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_sent_news(url, title):
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT OR IGNORE INTO sent_news (url, title, sent_at) VALUES (?, ?, ?)', (url, title, now_str))
    conn.commit()
    conn.close()

# ==================== 2. HÀM TẠO INFOGRAPHIC TỰ ĐỘNG ====================
def create_infographic(category_title, article_title, details_list, date_str):
    width, height = 900, 600
    image = Image.new('RGB', (width, height), color='#0f172a')
    draw = ImageDraw.Draw(image)

    # Đổi màu viền theo loại cảnh báo
    if "LŨ QUÉT" in category_title.upper() or "SẠT LỞ" in category_title.upper():
        border_color = '#d97706'  # Cam cảnh báo Lũ Quét
    elif "THỜI TIẾT" in category_title.upper():
        border_color = '#ef4444'  # Đỏ Thời Tiết Nguy Hiểm
    else:
        border_color = '#3b82f6'  # Xanh Thủy Văn

    # Viền ngoài & Header Banner
    draw.rectangle([15, 15, width-15, height-15], outline=border_color, width=4)
    draw.rectangle([15, 15, width-15, 90], fill=border_color)
    
    # Load Font
    font_header = font_title = font_sub = None
    font_paths = ["arialbd.ttf", "arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for path in font_paths:
        try:
            font_header = ImageFont.truetype(path, 25)
            font_title = ImageFont.truetype(path, 19)
            font_sub = ImageFont.truetype(path, 15)
            break
        except:
            continue
            
    if not font_header:
        font_header = font_title = font_sub = ImageFont.load_default()

    # Chữ Header
    draw.text((30, 30), f"🚨 CẢNH BÁO TỰ ĐỘNG: {category_title.upper()}", fill='#ffffff', font=font_header)
    draw.text((30, 105), f"🕒 Thời gian quét: {date_str}", fill='#94a3b8', font=font_sub)

    # Tiêu đề
    draw.text((30, 135), "NỘI DUNG CẢNH BÁO:", fill='#f59e0b', font=font_sub)
    
    words = article_title.split()
    lines, current_line = [], ""
    for word in words:
        if len(current_line + " " + word) < 48:
            current_line += " " + word
        else:
            lines.append(current_line.strip())
            current_line = word
    if current_line:
        lines.append(current_line.strip())

    y_pos = 160
    for line in lines[:3]:
        draw.text((30, y_pos), line, fill='#ffffff', font=font_title)
        y_pos += 30

    # Vẽ danh sách các XÃ thuộc THANH HÓA bị cảnh báo
    if details_list:
        y_pos += 15
        draw.text((30, y_pos), "📍 CÁC XÃ/PHƯỜNG TẠI THANH HÓA CÓ NGUY CƠ CAO:", fill='#38bdf8', font=font_sub)
        y_pos += 25
        for item in details_list[:8]:  # Hiển thị tối đa 8 xã trên Infographic
            draw.text((45, y_pos), f"• {item}", fill='#e2e8f0', font=font_sub)
            y_pos += 24
            
        if len(details_list) > 8:
            draw.text((45, y_pos), f"...và {len(details_list) - 8} khu vực khác tại Thanh Hóa.", fill='#94a3b8', font=font_sub)

    # Chân trang (Footer)
    draw.line([(30, height - 55), (width - 30, height - 55)], fill='#334155', width=2)
    draw.text((30, height - 40), "🌐 Nguồn: luquetsatlo.nchmf.gov.vn & kttv.thanhhoa.gov.vn", fill='#64748b', font=font_sub)

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

# ==================== 3. QUÉT LŨ QUÉT & SẠT LỞ (CHỈ LẤY CÁC XÃ THANH HÓA) ====================
def scrape_lu_quet_sat_lo():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://luquetsatlo.nchmf.gov.vn/'
    }
    try:
        res = requests.post(URL_LU_QUET_API, headers=headers, timeout=12)
        if res.status_code == 200:
            data = res.json()
            
            thanh_hoa_communes = []
            
            # Lọc dữ liệu: CHỈ LẤY TỈNH THANH HÓA
            for item in data:
                # Tìm tên tỉnh từ các trường trả về của API
                province_name = str(item.get("TenTinh") or item.get("ProvinceName") or item.get("Tinh") or "")
                
                if "thanh hóa" in province_name.lower() or "thanh hoá" in province_name.lower():
                    xa = item.get("TenXa") or item.get("CommuneName") or item.get("Xa") or ""
                    huyen = item.get("TenHuyen") or item.get("DistrictName") or item.get("Huyen") or ""
                    cap_do = item.get("CapDoCanhBao") or item.get("Level") or "Nguy cơ"
                    
                    if xa and huyen:
                        thanh_hoa_communes.append(f"Xã {xa} ({huyen}) - [{cap_do}]")
                    elif xa:
                        thanh_hoa_communes.append(f"Xã {xa} - [{cap_do}]")

            # Loại bỏ xã trùng lặp (nếu có)
            thanh_hoa_communes = list(set(thanh_hoa_communes))

            if thanh_hoa_communes:
                now_vn = (datetime.utcnow() + timedelta(hours=7)).strftime('%H:%M %d/%m/%Y')
                
                # Tạo Mã Định Danh theo Giờ + Số lượng xã để không gửi trùng lặp
                alert_id = f"luquet_thanhhoa_{len(thanh_hoa_communes)}_{datetime.now().strftime('%Y%m%d%H')}"
                
                if not is_news_sent(alert_id):
                    title = f"Phát hiện {len(thanh_hoa_communes)} Xã/Phường tại Tỉnh Thanh Hóa có nguy cơ Lũ quét & Sạt lở đất trong 06 giờ tới!"
                    
                    # 1. Vẽ Infographic liệt kê danh sách Xã Thanh Hóa
                    img_bytes = create_infographic("CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT (THANH HÓA)", title, thanh_hoa_communes, now_vn)
                    
                    # 2. Tạo Caption gửi Telegram
                    caption = f"⚠️ **CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT - TỈNH THANH HÓA**\n"
                    caption += f"🕒 *Thời gian:* {now_vn}\n"
                    caption += f"📍 **Tổng số xã có nguy cơ:** {len(thanh_hoa_communes)} xã\n"
                    caption += f"🌐 [Xem bản đồ trực quan trên NCHMF](https://luquetsatlo.nchmf.gov.vn)"
                    
                    # 3. Gửi Ảnh + Lưu Database
                    broadcast_photo(img_bytes, caption)
                    save_sent_news(alert_id, title)
                    print(f"✅ Đã phát hiện và gửi Infographic cho {len(thanh_hoa_communes)} xã ở Thanh Hóa!")
    except Exception as e:
        print(f"❌ Lỗi cào Lũ quét & Sạt lở Thanh Hóa: {e}")

# ==================== 4. QUÉT CÁC BẢN TIN KTTV THANH HÓA ====================
def scrape_and_auto_alert(url, category_name):
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
                    
                    img_bytes = create_infographic(category_name, title, [], now_vn)
                    caption = f"⚠️ **CẢNH BÁO MỚI TỪ ĐÀI KTTV THANH HÓA**\n\n📌 **{title}**\n\n🔗 [Xem bản tin gốc]({full_link})"
                    
                    broadcast_photo(img_bytes, caption)
                    save_sent_news(full_link, title)
                    print(f"✅ Đã gửi Infographic bài: {title}")
    except Exception as e:
        print(f"❌ Lỗi cào {category_name}: {e}")

# ==================== 5. QUÉT DÔNG SÉT IWEATHER ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {'User-Agent': 'Mozilla/5.0'}
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error"}

        raw_text = res.text
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, raw_text, re.IGNORECASE)
        unique_locations = list(set([m.strip(' ",') for m in matches]))

        matched_alerts = []
        for loc in unique_locations:
            matched_alerts.append({"location": loc, "time": now_vn.strftime('%H:%M %d/%m/%Y')})

        return {
            "status": "success",
            "has_warning": len(matched_alerts) > 0,
            "count": len(matched_alerts),
            "alerts": matched_alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== 6. HÀM GỬI TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn: {e}")

def send_telegram_photo(chat_id, image_bytes, caption):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
    files = {'photo': ('infographic.png', image_bytes, 'image/png')}
    payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"Lỗi gửi ảnh: {e}")

def broadcast_message(text):
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

def broadcast_photo(image_bytes, caption):
    for chat_id in REGISTERED_CHATS:
        image_bytes.seek(0)
        send_telegram_photo(chat_id, image_bytes, caption)

# ==================== 7. ROUTES TỰ ĐỘNG CHẠY ====================
@app.route('/')
def home():
    global LAST_ALERT_COUNT
    
    # 1. Quét Lũ quét & Sạt lở CHỈ DÀNH RIÊNG CHO CÁC XÃ THANH HÓA
    scrape_lu_quet_sat_lo()

    # 2. Quét 2 chuyên mục tin tức KTTV Thanh Hóa (Thời tiết nguy hiểm & Thủy văn đặc biệt)[cite: 1]
    scrape_and_auto_alert(URL_THOI_TIET, "Thời Tiết Nguy Hiểm")
    scrape_and_auto_alert(URL_THUY_VAN, "Thủy Văn Đặc Biệt")
    
    # 3. Quét Dông sét iWeather tại Thanh Hóa
    data = get_iweather_storm_warning("Thanh Hóa")
    if data.get("status") == "success":
        current_count = data.get("count", 0)
        if data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚡ **CẢNH BÁO DỒNG SÉ TỰ ĐỘNG TẠI THANH HÓA!**\n"
            msg += f"🕒 *Thời gian:* {data['updated_at']}\n"
            msg += f"📍 **Số khu vực phát hiện:** {current_count}\n"
            for idx, alert in enumerate(data['alerts'], 1):
                msg += f"\n**{idx}.** {alert['location']}\n"
            msg += "\n🌐 Xem Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            
            broadcast_message(msg)
            LAST_ALERT_COUNT = current_count
        elif not data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    return jsonify({
        "status": "system_running", 
        "registered_chats": list(REGISTERED_CHATS),
        "iweather_count": LAST_ALERT_COUNT
    })

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")

        REGISTERED_CHATS.add(chat_id)

        if text.startswith("/start") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "⚡ Bot đã kích hoạt! Hệ thống sẽ tự động gửi Infographic cảnh báo Lũ quét, Sạt lở các xã tại Thanh Hóa ngay khi có bản tin mới.")

    return "OK", 200

# Gọi init_db() ở đây để Gunicorn luôn tạo bảng DB khi khởi động
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
