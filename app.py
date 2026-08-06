import os
import urllib3
import requests
import hashlib
from datetime import datetime, timedelta
from flask import Flask, jsonify

# Tắt triệt để mọi cảnh báo SSL không an toàn trong Log
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CẤU HÌNH HỆ THỐNG ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAH1m9r7mwCjEQ1gmx6K1gmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
URL_THOI_TIET = "http://kttv.thanhhoa.gov.vn/tin-tuc/thoi-tiet-nguy-hiem/43"
URL_THUY_VAN = "http://kttv.thanhhoa.gov.vn/tin-tuc/thuy-van-dac-biet/46"
URL_LU_QUET_APIS = [
    "https://luquetsatlo.nchmf.gov.vn/Home/getDSCanhbaoSLLQ",
    "https://luquetsatlo.nchmf.gov.vn/Home/getThongTinXaCBTheoVungVe",
    "https://luquetsatlo.nchmf.gov.vn/Home/InitTrongDiemTheoSLLQ",
    "https://luquetsatlo.nchmf.gov.vn/Home/getThongTinXaCB"
]

app = Flask(__name__)
LAST_ALERT_COUNT = 0

# ==================== 1. DATABASE LƯU TRỮ VĨNH CỬU ====================
def init_db():
    """Khởi tạo SQLite lưu vĩnh cữu Tin đã gửi & Danh sách Chat ID đăng ký"""
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            url TEXT PRIMARY KEY,
            title TEXT,
            sent_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registered_chats (
            chat_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

init_db()

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
    now_vn = datetime.utcnow() + timedelta(hours=7)
    now_str = now_vn.strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT OR IGNORE INTO sent_news (url, title, sent_at) VALUES (?, ?, ?)', (url, title, now_str))
    conn.commit()
    conn.close()

def add_registered_chat(chat_id):
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO registered_chats (chat_id) VALUES (?)', (chat_id,))
    conn.commit()
    conn.close()

def get_registered_chats():
    conn = sqlite3.connect('alerts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM registered_chats')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ==================== 2. HÀM GỬI THÔNG BÁO TELEGRAM ====================
def send_telegram_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"❌ Lỗi gửi tin nhắn: {e}")

def send_telegram_photo(chat_id, image_bytes, caption):
    try:
        image_bytes.seek(0)
        files = {'photo': ('infographic.png', image_bytes.read(), 'image/png')}
        payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        requests.post(f"{TELEGRAM_API_URL}/sendPhoto", data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"❌ Lỗi gửi ảnh Telegram: {e}")

def broadcast_message(text):
    chats = get_registered_chats()
    for chat_id in chats:
        send_telegram_message(chat_id, text)

def broadcast_photo(image_bytes, caption):
    chats = get_registered_chats()
    for chat_id in chats:
        send_telegram_photo(chat_id, image_bytes, caption)

# ==================== 3. ENGINE VẼ INFOGRAPHIC CHỐNG LỖI RỖNG ====================
def create_infographic(category_title, article_title, parsed_data, date_str):
    width = 900
    locations = parsed_data.get('locations', [])
    summary = parsed_data.get('summary', [])
    
    # Co giãn chiều cao tự động
    loc_rows = (len(locations) + 1) // 2
    added_height = (loc_rows * 36) + (len(summary) * 28)
    height = max(580, 400 + added_height)

    image = Image.new('RGB', (width, height), color='#0f172a')
    draw = ImageDraw.Draw(image)

    # Viền theo loại cảnh báo
    if "LŨ QUÉT" in category_title.upper() or "SẠT LỞ" in category_title.upper():
        border_color = '#f59e0b'
    elif "THỜI TIẾT" in category_title.upper() or "DÔNG SÉT" in category_title.upper():
        border_color = '#ef4444'
    else:
        border_color = '#0284c7'

    draw.rectangle([15, 15, width-15, height-15], outline=border_color, width=4)
    draw.rectangle([15, 15, width-15, 90], fill=border_color)
    
    # Font hệ thống
    font_header = font_title = font_bold = font_sub = None
    font_paths = ["Roboto-Bold.ttf", "arialbd.ttf", "arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
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

    # Title Banner
    draw.text((30, 32), f"🚨 CẢNH BÁO TỰ ĐỘNG: {category_title.upper()}", fill='#ffffff', font=font_header)
    draw.text((width - 240, 38), f"🕒 {date_str}", fill='#ffffff', font=font_sub)

    # Tiêu đề bài viết
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

    # Box thông số
    y_pos += 12
    draw.rectangle([30, y_pos, 430, y_pos+65], fill='#1e293b', outline='#334155', width=2)
    draw.text((45, y_pos+10), "🌧️ THÔNG SỐ CẢNH BÁO", fill='#94a3b8', font=font_sub)
    draw.text((45, y_pos+32), parsed_data.get('rainfall', 'Theo dõi chi tiết'), fill='#38bdf8', font=font_bold)

    draw.rectangle([450, y_pos, 870, y_pos+65], fill='#1e293b', outline='#334155', width=2)
    draw.text((465, y_pos+10), "⚠️ CẤP ĐỘ RỦI RO THIÊN TAI", fill='#94a3b8', font=font_sub)
    draw.text((465, y_pos+32), parsed_data.get('risk_level', 'CẤP 1'), fill='#f43f5e', font=font_bold)

    # Danh sách địa bàn
    y_pos += 85
    if locations:
        draw.text((30, y_pos), "📍 CÁC KHU VỰC / XÃ / PHƯỜNG NGUY CƠ CAO:", fill='#f59e0b', font=font_sub)
        y_pos += 25
        x_start, x_curr = 30, 30
        for loc in locations:
            badge_text = f"• {loc}"
            box_width = len(badge_text) * 9 + 20
            if x_curr + box_width > width - 40:
                x_curr = x_start
                y_pos += 34
            draw.rectangle([x_curr, y_pos, x_curr + box_width, y_pos + 26], fill='#334155', outline='#475569')
            draw.text((x_curr + 8, y_pos + 4), badge_text, fill='#e2e8f0', font=font_sub)
            x_curr += box_width + 10
        y_pos += 40

    # Tóm tắt
    if summary:
        draw.text((30, y_pos), "📝 CHI TIẾT DỰ BÁO / HƯỚNG DẪN:", fill='#38bdf8', font=font_sub)
        y_pos += 25
        for s_line in summary[:4]:
            draw.text((40, y_pos), f"• {s_line}", fill='#cbd5e1', font=font_sub)
            y_pos += 24

    # Footer
    draw.line([(30, height - 40), (width - 30, height - 40)], fill='#334155', width=1)
    draw.text((30, height - 30), "🌐 Trích xuất dữ liệu & thiết kế Infographic tự động 24/7", fill='#64748b', font=font_sub)

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

# ==================== DẠNG 1: DÔNG SÉT (IWEATHER) ====================
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

# ==================== DẠNG 2: KTTV THANH HÓA (ĐÃ BẮT LỖI ẢNH SCAN) ====================
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
                    
                    # BÓC TÁCH BÀI VIẾT BẰNG BEAUTIFULSOUP (BẮT BÀI DẠNG ẢNH SCAN)
                    detail_res = requests.get(full_link, headers=headers, timeout=10)
                    detail_res.encoding = 'utf-8'
                    detail_soup = BeautifulSoup(detail_res.text, 'html.parser') if detail_res.status_code == 200 else None
                    
                    full_text = ""
                    has_images = False
                    
                    if detail_soup:
                        content_div = detail_soup.find('div', class_='detail-content') or detail_soup.find('div', class_='content') or detail_soup.body
                        if content_div:
                            full_text = content_div.get_text(strip=True)
                            has_images = len(content_div.find_all('img')) > 0
                    
                    # KIỂM TRA BẢN TIN DẠNG ẢNH SCAN
                    is_image_bulletin = (len(full_text) < 40) or (has_images and len(full_text) < 100)
                    
                    if is_image_bulletin:
                        rainfall = "Dạng FILE ẢNH SCAN"
                        found_locs = ["⚠️ VĂN BẢN ĐỒ HỌA / SCAN", "👉 Nhấn 'Xem bản tin gốc' để đọc"]
                        summary = [
                            "Đài KTTV phát hành bài viết dưới dạng FILE ẢNH SCAN.",
                            "Mời bấm link 'Xem bản tin gốc' bên dưới Telegram để mở ảnh trực tiếp."
                        ]
                    else:
                        rain_match = re.search(r'(\d+\s*[-–]\s*\d+\s*mm|\d+\s*mm)', full_text, re.IGNORECASE)
                        rainfall = rain_match.group(1) if rain_match else "Có mưa vừa đến mưa to"
                        
                        districts = ["Mường Lát", "Quan Sơn", "Quan Hóa", "Bá Thước", "Lang Chánh", "Thường Xuân", "Ngọc Lặc", "Như Xuân", "Cẩm Thủy", "Thạch Thành"]
                        found_locs = [d for d in districts if re.search(r'\b' + re.escape(d) + r'\b', full_text, re.IGNORECASE)]
                        if not found_locs:
                            found_locs = ["Toàn tỉnh Thanh Hóa"]
                            
                        # Cắt tóm tắt 2-3 câu ngắn gọn
                        clean_lines = [p.get_text(strip=True) for p in detail_soup.find_all('p') if len(p.get_text(strip=True)) > 20]
                        summary = clean_lines[:3] if clean_lines else [title]

                    parsed_data = {
                        "rainfall": rainfall,
                        "risk_level": "CẤP ĐỘ CẢNH BÁO",
                        "locations": found_locs,
                        "summary": summary
                    }
                    
                    img_bytes = create_infographic(category_name, title, parsed_data, now_vn)
                    caption = f"⚠️ **CẢNH BÁO MỚI TỪ ĐÀI KTTV THANH HÓA**\n\n📌 **{title}**\n\n🔗 [Xem bản tin gốc]({full_link})"
                    
                    broadcast_photo(img_bytes, caption)
                    save_sent_news(full_link, title)
                    print(f"✅ Đã gửi Infographic KTTV Thanh Hóa: {title}")
    except Exception as e:
        print(f"❌ Lỗi cào KTTV Thanh Hóa: {e}")

# ==================== DẠNG 3: LŨ QUÉT - CHỐNG LỖI SCOPE BIẾN ====================
LAST_ALERTED_COMMUNES = []

def scrape_lu_quet_sat_lo_cap_xa():
    global LAST_ALERTED_COMMUNES
    
    active_communes = []
    
    # Header giả lập trình duyệt máy tính từ Việt Nam
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://luquetsatlo.nchmf.gov.vn/',
        'X-Requested-With': 'XMLHttpRequest',
        'Connection': 'keep-alive'
    }
    
    api_configs = [
        ("https://luquetsatlo.nchmf.gov.vn/Home/getDSCanhbao", "POST"),
        ("https://luquetsatlo.nchmf.gov.vn/Home/getDSCanhbao", "GET"),
        ("https://luquetsatlo.nchmf.gov.vn/Home/getDSCanhbaoSLLQ", "POST"),
        ("https://luquetsatlo.nchmf.gov.vn/Home/getThongTinXaCB", "POST")
    ]

    for api_url, method in api_configs:
        try:
            if method == "POST":
                res = requests.post(api_url, headers=headers, timeout=12, verify=False)
            else:
                res = requests.get(api_url, headers=headers, timeout=12, verify=False)

            # IN LOG ĐỂ KIỂM TRA PHẢN HỒI TỪ NCHMF
            print(f"🌐 [NCHMF Test] URL: {api_url} | Method: {method} | Status: {res.status_code} | Size: {len(res.text)} bytes")

            if res.status_code == 200:
                data = res.json()
                items = data if isinstance(data, list) else data.get("features", data.get("data", []))
                
                if isinstance(items, list) and len(items) > 0:
                    for item in items:
                        props = item.get("properties", item) if isinstance(item, dict) else {}
                        
                        tinh_id = str(props.get("tinh_id") or props.get("id_tinh") or props.get("province_id") or "").strip()
                        province_name = str(props.get("tentinh") or props.get("ten_tinh") or "").lower()

                        # Lọc mã 33 (Thanh Hóa)
                        if tinh_id == "33" or "thanh hóa" in province_name or "thanh hoá" in province_name:
                            commune = str(
                                props.get("commune_name_2cap") or 
                                props.get("xaname_2cap") or 
                                props.get("ten_xa") or 
                                props.get("TenXa") or ""
                            ).strip()

                            if commune:
                                commune_clean = commune[3:].strip() if commune.lower().startswith("xã ") else commune
                                label = f"Xã {commune_clean}"
                                if label not in active_communes:
                                    active_communes.append(label)

                    if len(active_communes) > 0:
                        print(f"🎯 Đã tìm thấy {len(active_communes)} xã thuộc Thanh Hóa từ {api_url}")
                        break
        except Exception as e:
            print(f"⚠️ Thử API {api_url} ({method}) lỗi: {e}")

    # Broadcast tin lên Telegram nếu phát hiện xã nguy cơ
    if len(active_communes) > 0:
        try:
            sorted_communes = sorted(active_communes)
            communes_str = ",".join(sorted_communes)
            communes_hash = hashlib.md5(communes_str.encode('utf-8')).hexdigest()[:10]
            
            alert_id = f"luquet_hash_{communes_hash}"
            
            if not is_news_sent(alert_id):
                now_vn_dt = datetime.utcnow() + timedelta(hours=7)
                now_vn_str = now_vn_dt.strftime('%H:%M %d/%m/%Y')
                
                new_communes = [xa for xa in active_communes if xa not in LAST_ALERTED_COMMUNES]
                
                title = f"CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT TẠI {len(active_communes)} XÃ"
                parsed_data = {
                    "rainfall": "Nguy cơ cao trong 06h tới",
                    "risk_level": "CẤP 1 - TRUNG BÌNH / CAO",
                    "locations": active_communes,
                    "summary": [
                        f"NCHMF ghi nhận {len(active_communes)} xã tại Thanh Hóa nằm trong vùng nguy cơ.",
                        f"Bổ sung {len(new_communes)} xã mới." if new_communes else "Danh sách cập nhật thời gian thực."
                    ]
                }
                
                img_bytes = create_infographic("BẢN TIN LŨ QUÉT & SẠT LỞ CẤP XÃ", title, parsed_data, now_vn_str)
                
                caption = f"🚨 **CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT (CẬP NHẬT MỚI)**\n"
                caption += f"🕒 *Mốc thời gian:* **{now_vn_str}**\n"
                caption += f"📍 *Tổng số địa bàn nguy cơ:* **{len(active_communes)} xã**\n\n"
                
                if new_communes and len(LAST_ALERTED_COMMUNES) > 0:
                    caption += "🔥 **XÃ MỚI BỔ SUNG NGUY CƠ:**\n"
                    for xa in new_communes:
                        caption += f"  🆕 **{xa}**\n"
                    caption += "\n"

                caption += "🔻 **TOÀN BỘ DANH SÁCH XÃ NGUY CƠ:**\n"
                for idx, xa in enumerate(active_communes, 1):
                    is_new_mark = " 🆕" if xa in new_communes and len(LAST_ALERTED_COMMUNES) > 0 else ""
                    caption += f"  {idx}. {xa}{is_new_mark}\n"
                    
                caption += "\n🌐 [Bản đồ trực quan NCHMF](https://luquetsatlo.nchmf.gov.vn)"
                
                broadcast_photo(img_bytes, caption)
                save_sent_news(alert_id, f"Lũ quét {len(active_communes)} xã")
                
                LAST_ALERTED_COMMUNES = active_communes.copy()
                print(f"✅ Đã phát Infographic thành công tới các nhóm Telegram!")
        except Exception as e:
            print(f"⚠️ Lỗi phát infographic lũ quét: {e}")

    return len(active_communes), active_communes
# ==================== 4. ROUTES SERVER & CONTROLLER ====================
@app.route('/')
def home():
    # 1. Gọi hàm cào dữ liệu Lũ quét
    lu_quet_count, lu_quet_list = scrape_lu_quet_sat_lo_cap_xa()
    
    # 2. Lấy thời gian hiện tại
    now_vn_str = (datetime.utcnow() + timedelta(hours=7)).strftime('%H:%M:%S %d/%m/%Y')
    
    # 3. Lấy trực tiếp danh sách Telegram Chat ID đã đăng ký trong SQLite
    chats_list = get_registered_chats()
    
    return jsonify({
        "bot_info": {
            "name": "Trạm Mưa Báo Báo Bot",
            "target_region": "Tỉnh Thanh Hóa (Mã Tỉnh 33)",
            "status": "ĐANG HOẠT ĐỘNG 24/7 🟢"
        },
        "system_time_vn": now_vn_str,
        "telegram_subscribers": {
            "count": len(chats_list),
            "registered_chat_ids": chats_list  # <-- IN TRỰC TIẾP DANH SÁCH ID TELEGRAM RA ĐÂY
        },
        "warning_sources": {
            "nchmf_lu_quet_sat_lo": {
                "active_communes_count": lu_quet_count,
                "communes_list": lu_quet_list if lu_quet_list else ["Hiện tại NCHMF chưa ghi nhận xã nào ở Thanh Hóa có nguy cơ trong 6h tới"]
            },
            "iweather_dong_set": "Tự động quét theo chu kỳ radar",
            "kttv_thanh_hoa": "Tự động quét bản tin cảnh báo"
        }
    })
    
    # 1. Chạy Dạng 1: Dông sét iWeather
    iweather_data = get_iweather_storm_warning()
    if iweather_data["has_warning"] and iweather_data["count"] != LAST_ALERT_COUNT:
        msg = f"⚡ **CẢNH BÁO DÔNG SÉT TỰ ĐỘNG TẠI THANH HÓA!**\n"
        msg += f"🕒 *Thời gian:* {iweather_data['updated_at']}\n"
        msg += f"📍 **Số khu vực phát hiện:** {iweather_data['count']}\n"
        for idx, loc in enumerate(iweather_data['locations'], 1):
            msg += f"\n**{idx}.** {loc}\n"
        msg += "\n🌐 Xem Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
        broadcast_message(msg)
        LAST_ALERT_COUNT = iweather_data["count"]
    elif not iweather_data["has_warning"]:
        LAST_ALERT_COUNT = 0

   # 2. Chạy Dạng 2: KTTV Thanh Hóa
    scrape_kttv_thanh_hoa(URL_THOI_TIET, "Thời Tiết Nguy Hiểm")
    scrape_kttv_thanh_hoa(URL_THUY_VAN, "Thủy Văn Đặc Biệt")

    # 3. Chạy Dạng 3: Lũ quét NCHMF (Xử lý an toàn)
    try:
        lu_quet_count, lu_quet_list = scrape_lu_quet_sat_lo_cap_xa()
    except Exception as e:
        print(f"❌ Lỗi chạy Lũ quét: {e}")
        lu_quet_count, lu_quet_list = 0, []

    now_vn_str = (datetime.utcnow() + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({
        "status": "system_running",
        "time_vn": now_vn_str,
        "lu_quet_count": lu_quet_count,
        "lu_quet_communes": lu_quet_list,
        "registered_chats_count": len(get_registered_chats())
    })
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        
        # Tự động lưu Chat ID vào Database SQLite
        add_registered_chat(chat_id)
        
        if text.startswith("/start") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "⚡ **Bot đã kích hoạt!** Đã lưu Chat ID của bạn. Bot sẽ tự động gửi Infographic cảnh báo dông sét, KTTV và Sạt lở/Lũ quét đúng mốc giờ tròn.")
    return "OK", 200

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
