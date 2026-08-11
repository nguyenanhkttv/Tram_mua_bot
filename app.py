import os
import re
import json
import requests
import urllib3
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Tắt cảnh báo SSL Certificate khi gọi NCHMF
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CẤU HÌNH ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"

# NGUỒN 4: Lũ quét & Sạt lở đất (Cục Khí tượng Thủy văn)
NCHMF_LANDSLIDE_URL = "https://luquetsatlo.nchmf.gov.vn/LayerMapBox/getThongTinXaCBTheoVungVe"
POLYGON_THANH_HOA = {
    "id": "bbox_thanh_hoa",
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[104.3, 20.8], [106.2, 20.8], [106.2, 19.2], [104.3, 19.2], [104.3, 20.8]]]
    }
}

# API Giám sát Trạm Mạng Nước (cần điền đúng URL endpoint của API ReadDeviceUser)
IOT_STATION_URL = os.environ.get("IOT_STATION_URL", "http://iot.vientnmt.com:8888/api/DataAPI/ReadDeviceUser")
IOT_TOKENKEY = os.environ.get("IOT_TOKENKEY", "rRh2Tws7G5ba7HCNLjc73REyXSixwmIPK2tE8t5Nr...")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# Bộ nhớ lưu danh sách Chat ID nhận tin nhắn tự động
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()

# Bộ nhớ chống spam cho Nguồn 4 (Lũ quét - Sạt lở)
SENT_LANDSLIDE_KEYS = set()

# Bộ nhớ lưu trạng thái trạm trước đó để so sánh thay đổi: { "device_id": True/False }
STATION_PREVIOUS_STATUS = {}

# ==================== LOGIC GIÁM SÁT TRẠM MẠNG NƯỚC (IOT) ====================
def get_station_status():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    headers = {
        **HEADERS_DEFAULT,
        'Content-Type': 'application/json'
    }
    payload = {
        "tokenkey": IOT_TOKENKEY
    }
    try:
        res = requests.post(IOT_STATION_URL, json=payload, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"HTTP {res.status_code}"}
        
        data = res.json()
        devices = data.get("list_devices", [])
        
        station_list = []
        for dev in devices:
            station_list.append({
                "id": str(dev.get("Device_id", "")),
                "name": dev.get("Device_name", "Không rõ tên"),
                "status": bool(dev.get("Status", False)),
                "area": dev.get("area", "Khác")
            })
            
        return {
            "status": "success",
            "has_data": len(station_list) > 0,
            "stations": station_list,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

def format_station_message(data):
    if data.get("status") != "success":
        return f"❌ **[GIÁM SÁT TRẠM]** Lỗi kết nối API trạm: `{data.get('message')}`"
    
    stations = data.get("stations", [])
    online_count = sum(1 for s in stations if s["status"])
    offline_count = len(stations) - online_count
    
    msg = f"📡 **[TRẠNG THÁI HỆ THỐNG TRẠM THỦY VĂN TỰ ĐỘNG THANH HÓA]**\n"
    msg += f"🕒 *Cập nhật:* `{data['updated_at']}`\n"
    msg += f"🟢 Đang kết nối: **{online_count}** | 🔴 Mất kết nối: **{offline_count}**\n"
    msg += "───────────────────\n"
    
    for s in stations:
        icon = "🟢" if s["status"] else "🔴"
        status_text = "Đang kết nối" if s["status"] else "MẤT KẾT NỐI"
        msg += f"{icon} **{s['name']}**\n└ Trạng thái: *{status_text}*\n"
        
    return msg

# ==================== LOGIC QUÉT RADAR DÔNG SÉT (IWEATHER) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        **HEADERS_DEFAULT, 
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX', 
        'Accept': 'application/json'
    }
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200: 
            return {"status": "error", "message": f"HTTP {res.status_code}"}
        
        matches = re.findall(r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)', res.text, re.IGNORECASE)
        unique_locs = list(set([m.strip(' ",') for m in matches]))
        alerts = [{"location": loc, "intensity": "Mây dông/Sét phát triển", "time": now_vn.strftime('%H:%M %d/%m/%Y')} for loc in unique_locs]
        
        return {
            "status": "success", 
            "has_warning": len(alerts) > 0, 
            "count": len(alerts), 
            "alerts": alerts, 
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e: 
        return {"status": "error", "message": str(e)}

def format_iweather_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"⚡ **[RADAR DÔNG SÉT - IWEATHER]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n✅ **AN TOÀN:** Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    
    header = "⚠️ **[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]**" if is_auto else "⚡ **[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]**"
    msg = f"{header}\n🕒 *Thời gian quét:* `{data['updated_at']}`\n📡 *Tổng số vùng:* **{data['count']} khu vực**\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1): 
        msg += f"🌩️ **{idx}.** {alert['location']}\n"
    msg += "───────────────────\n🌐 [Mở bản đồ Radar CMAX](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

# ==================== LOGIC GIÁM SÁT THIÊN TAI (VNDMS) ====================
def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code != 200: 
            return {"status": "error", "message": f"HTTP {res.status_code}"}
        
        data = res.json()
        alerts = []
        if isinstance(data, list):
            for item in data:
                alerts.append({
                    "id": item.get("Id") or item.get("Code") or str(hash(str(item))),
                    "title": item.get("DisasterName") or item.get("Name") or "CẢNH BÁO THIÊN TAI NGUY HIỂM",
                    "risk_level": item.get("RiskLevel", "Đang cập nhật"),
                    "start_time": item.get("StartDate", "Chưa xác định"),
                    "description": item.get("Description") or item.get("Note") or "Chưa có thông tin chi tiết."
                })
        return {
            "status": "success", 
            "has_warning": len(alerts) > 0, 
            "count": len(alerts), 
            "alerts": alerts, 
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e: 
        return {"status": "error", "message": str(e)}

def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"🏛️ **[GIÁM SÁT THIÊN TAI - VNDMS]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n🟢 **KHÔNG CÓ CẢNH BÁO NÓNG:** Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."
    
    header = "🚨 **[CẢNH BÁO KHẨN CẤP TỪ VNDMS]**" if is_auto else "🏛️ **[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]**"
    msg = f"{header}\n🕒 *Cập nhật:* `{data['updated_at']}`\n📋 *Số bản tin:* **{data['count']} tin**\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 **BẢN TIN {idx}: {alert['title'].upper()}**\n⏱ **Bắt đầu:** {alert['start_time']}\n⚠️ **Cấp độ rủi ro:** `{alert['risk_level']}`\n📝 **Nội dung:** {alert['description']}\n▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    msg += "🌐 *Nguồn:* Cục QLĐĐ & PCTT (vndms.gov.vn)"
    return msg

# ==================== LOGIC NGUỒN 4: LŨ QUÉT & SẠT LỞ (NCHMF) ====================
def get_nchmf_landslide_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'X-Requested-With': 'XMLHttpRequest'
    }
    payload = {
        "datadulieu": json.dumps(POLYGON_THANH_HOA),
        "dataidxa": ""
    }
    try:
        # verify=False để vượt lỗi chứng chỉ SSL
        res = requests.post(NCHMF_LANDSLIDE_URL, data=payload, headers=headers, verify=False, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"HTTP {res.status_code}"}
            
        data = res.json()
        alerts = []
        if isinstance(data, list):
            for item in data:
                if "Thanh Hóa" in str(item.get("ten_tinh", "")):
                    xa_2cap = item.get("xaname_2cap") or item.get("ten_xa") or "Chưa rõ"
                    xa_hc = item.get("ten_xa", "")
                    lu_quet = item.get("lu_quet") or "Mức trung bình"
                    sat_lo = item.get("sat_lo") or "Mức trung bình"
                    
                    alerts.append({
                        "key": f"{item.get('xaid_2cap', xa_2cap)}_{lu_quet}_{sat_lo}",
                        "xa_2cap": xa_2cap,
                        "xa_hc": xa_hc,
                        "lu_quet": lu_quet,
                        "sat_lo": sat_lo
                    })

        return {
            "status": "success",
            "has_warning": len(alerts) > 0,
            "count": len(alerts),
            "alerts": alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

def format_nchmf_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"⛰️ **[CẢNH BÁO LŨ QUÉT & SẠT LỞ ĐẤT]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n✅ **AN TOÀN:** Hiện không có xã nào ở Thanh Hóa phát sinh cảnh báo nguy cơ lũ quét hay sạt lở."

    header = "⚠️ **[CẢNH BÁO TỰ ĐỘNG: LŨ QUÉT & SẠT LỞ THANH HÓA]**" if is_auto else "⛰️ **[CẢNH BÁO LŨ QUÉT & SẠT LỞ - THANH HÓA]**"
    msg = f"{header}\n🕒 *Thời gian:* `{data['updated_at']}`\n📍 *Tổng số vùng cảnh báo:* **{data['count']} xã/khu vực**\n───────────────────\n"
    
    for idx, item in enumerate(data['alerts'], 1):
        vung = item['xa_2cap']
        if item['xa_hc'] and item['xa_hc'] != item['xa_2cap']:
            vung += f" ({item['xa_hc']})"
        
        msg += f"📍 **{idx}. Địa bàn:** {vung}\n"
        msg += f" 🌀 Lũ quét: **{item['lu_quet']}**\n"
        msg += f" ⛰️ Sạt lở đất: **{item['sat_lo']}**\n\n"
        
    msg += "🌐 *Nguồn:* Cục Khí tượng Thủy văn (luquetsatlo.nchmf.gov.vn)"
    return msg

# ==================== HÀM GỬI THÔNG BÁO TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=5)
    except Exception as e: 
        print(f"Lỗi gửi Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS: 
        send_telegram_message(chat_id, text)

# ==================== ROUTE CHẠY TỰ ĐỘNG KHÔNG DỪNG (CRON/PING) ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS, STATION_PREVIOUS_STATUS, SENT_LANDSLIDE_KEYS
    
    # 1. Quét Cảnh báo Mất kết nối Trạm IoT
    station_data = get_station_status()
    if station_data.get("status") == "success":
        for station in station_data.get("stations", []):
            st_id = station["id"]
            st_name = station["name"]
            is_online = station["status"]
            
            if st_id in STATION_PREVIOUS_STATUS:
                prev_online = STATION_PREVIOUS_STATUS[st_id]
                if prev_online and not is_online:
                    alert_msg = (f"🚨 **[CẢNH BÁO MẤT KẾT NỐI TRẠM]**\n"
                                 f"⚠️ Trạm: **{st_name}**\n"
                                 f"❌ Trạng thái: **ĐÃ MẤT KẾT NỐI**\n"
                                 f"🕒 Thời gian phát hiện: `{station_data['updated_at']}`")
                    broadcast_alert(alert_msg)
                elif not prev_online and is_online:
                    recovery_msg = (f"✅ **[THÔNG BÁO TRẠM KẾT NỐI LẠI]**\n"
                                    f"📡 Trạm: **{st_name}**\n"
                                    f"🟢 Trạng thái: **ĐÃ KẾT NỐI LẠI**\n"
                                    f"🕒 Thời gian: `{station_data['updated_at']}`")
                    broadcast_alert(recovery_msg)
            
            STATION_PREVIOUS_STATUS[st_id] = is_online

    # 2. Quét Dông sét iWeather
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            broadcast_alert(format_iweather_message(iweather_data, is_auto=True))
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    # 3. Quét Cảnh báo VNDMS
    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = [a for a in vndms_data['alerts'] if a['id'] not in SENT_VNDMS_IDS]
        if new_alerts:
            for a in new_alerts: 
                SENT_VNDMS_IDS.add(a['id'])
            v_copy = dict(vndms_data)
            v_copy['alerts'] = new_alerts
            v_copy['count'] = len(new_alerts)
            broadcast_alert(format_vndms_message(v_copy, is_auto=True))

    # 4. Quét Lũ quét & Sạt lở đất (NCHMF)
    landslide_data = get_nchmf_landslide_warning()
    if landslide_data.get("status") == "success" and landslide_data.get("has_warning"):
        new_landslide_alerts = [a for a in landslide_data['alerts'] if a['key'] not in SENT_LANDSLIDE_KEYS]
        if new_landslide_alerts:
            for a in new_landslide_alerts:
                SENT_LANDSLIDE_KEYS.add(a['key'])
            l_copy = dict(landslide_data)
            l_copy['alerts'] = new_landslide_alerts
            l_copy['count'] = len(new_landslide_alerts)
            broadcast_alert(format_nchmf_message(l_copy, is_auto=True))

    return jsonify({
        "status": "running", 
        "registered_chats": list(REGISTERED_CHATS),
        "tracked_stations": len(STATION_PREVIOUS_STATUS)
    })

# ==================== TELEGRAM WEBHOOK (NHẬN LỆNH TỪ USER) ====================
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip().lower()

        # Tự động ghi nhớ Chat ID người dùng để gửi cảnh báo tự động khi có biến
        REGISTERED_CHATS.add(chat_id)

        # 1. CHỈ TRA CỨU TRẠM MẠNG NƯỚC
        if text.startswith("/tram") or text.startswith("/thietbi"):
            st_data = get_station_status()
            send_telegram_message(chat_id, format_station_message(st_data))

        # 2. CHỈ TRA CỨU RADAR DÔNG SÉT (IWEATHER)
        elif text.startswith("/dong"):
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))

        # 3. CHỈ TRA CỨU CẢNH BÁO THIÊN TAI (VNDMS)
        elif text.startswith("/thientai") or text.startswith("/canhbao"):
            vndms_data = get_vndms_warning()
            send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))

        # 4. CHỈ TRA CỨU LŨ QUÉT & SẠT LỞ (NCHMF)
        elif text.startswith("/luquet") or text.startswith("/satlo"):
            landslide_data = get_nchmf_landslide_warning()
            send_telegram_message(chat_id, format_nchmf_message(landslide_data, is_auto=False))

        # 5. LỆNH TỔNG HỢP GỘP TRONG 1 TIN NHẮN DUY NHẤT (/start HOẶC /tong)
        elif text.startswith("/start") or text.startswith("/tong") or text.startswith("/thoitiet"):
            # Lấy dữ liệu cả 4 nguồn
            st_data = get_station_status()
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            vndms_data = get_vndms_warning()
            landslide_data = get_nchmf_landslide_warning()

            # --- TỔNG HỢP VÀO 1 TIN NHẮN ---
            msg = "📊 **[BÁO CÁO TỔNG HỢP THỜI TIẾT & HỆ THỐNG]**\n"
            msg += f"🕒 *Thời gian:* `{datetime.utcnow() + timedelta(hours=7):%H:%M %d/%m/%Y}`\n"
            msg += "═══════════════════\n\n"

            # 1. Tóm tắt Trạm
            if st_data.get("status") == "success":
                stations = st_data.get("stations", [])
                online = sum(1 for s in stations if s["status"])
                msg += f"📡 **TRẠM TỰ ĐỘNG:** 🟢 {online}/{len(stations)} trạm kết nối\n"
            else:
                msg += f"📡 **TRẠM TỰ ĐỘNG:** ❌ Lỗi kết nối API\n"

            # 2. Tóm tắt Dông sét
            if iweather_data.get("has_warning"):
                msg += f"🌩️ **DÔNG SÉT (iWeather):** ⚠️ Có {iweather_data['count']} vùng phát triển\n"
            else:
                msg += "🌩️ **DÔNG SÉT (iWeather):** 🟢 An toàn\n"

            # 3. Tóm tắt Thiên tai
            if vndms_data.get("has_warning"):
                msg += f"🏛️ **THIÊN TAI (VNDMS):** 🚨 Có {vndms_data['count']} bản tin khẩn\n"
            else:
                msg += "🏛️ **THIÊN TAI (VNDMS):** 🟢 Không có cảnh báo\n"

            # 4. Tóm tắt Lũ quét & Sạt lở
            if landslide_data.get("has_warning"):
                msg += f"⛰️ **LŨ QUÉT & SẠT LỞ:** ⚠️ Có {landslide_data['count']} xã/vùng nguy cơ\n"
            else:
                msg += "⛰️ **LŨ QUÉT & SẠT LỞ:** 🟢 An toàn\n"

            msg += "\n💡 *Gõ từng lệnh riêng để xem chi tiết:*\n"
            msg += "`/tram` | `/dong` | `/thientai` | `/luquet`"

            # Gửi đúng 1 tin nhắn tổng hợp duy nhất
            send_telegram_message(chat_id, msg)

    return "OK", 200
