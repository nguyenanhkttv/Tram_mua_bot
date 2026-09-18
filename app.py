import os
import re
import json
import time
import requests
import urllib3
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CẤU HÌNH API ====================
VRAIN_DATA_URL = "https://data.vrain.vn/public/current/27.json"
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"
NCHMF_CANHBAO_URL = "https://luquetsatlo.nchmf.gov.vn/LayerMapBox/getDSCanhbaoSLLQ"

IOT_STATION_URL = os.environ.get("IOT_STATION_URL", "http://iot.vientnmt.com:8888/api/DataAPI/ReadDeviceUser")
IOT_TOKENKEY = os.environ.get("IOT_TOKENKEY", "rRh2Tws7G5ba7HCNLjc73REyXSixwmIPK2tE8t5Nr...")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

MAX_MSG_LEN = 3500
app = Flask(__name__)

# ==================== QUẢN LÝ CHAT ID & TRẠNG THÁI ====================
CHAT_FILE = "chats.json"

def load_chats():
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"❌ Lỗi đọc chat ID: {e}")
    return set()

def save_chats():
    try:
        with open(CHAT_FILE, "w") as f:
            json.dump(list(REGISTERED_CHATS), f)
    except Exception as e:
        print(f"❌ Lỗi lưu chat ID: {e}")

REGISTERED_CHATS = load_chats()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()
SENT_LANDSLIDE_STATES = {}
STATION_PREVIOUS_STATUS = {}
SENT_VRAIN_STAGES = {}

# ==================== HÀM GỬI THÔNG BÁO TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    if len(text) <= MAX_MSG_LEN:
        try: 
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
        except Exception as e: 
            print(f"❌ Lỗi gửi Telegram: {e}")
        return

    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > MAX_MSG_LEN:
            try:
                requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
            except Exception as e:
                print(f"❌ Lỗi gửi chunk Telegram: {e}")
            chunk = line + "\n"
        else:
            chunk += line + "\n"
            
    if chunk.strip():
        try:
            requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
        except Exception as e:
            print(f"❌ Lỗi gửi chunk cuối Telegram: {e}")

def broadcast_alert(text):
    chats = load_chats().union(REGISTERED_CHATS)
    for chat_id in chats: 
        send_telegram_message(chat_id, text)

# ==================== 1. NGUỒN MƯA VRAIN (LỌC ≥ 30MM) ====================
def check_rain_alert_level(st_key, rain, stage_dict):
    if rain < 30.0:
        return False, "", "", 0

    state = stage_dict.get(st_key, {"stage": 0, "last_rain": 0.0})
    prev_stage = state["stage"]
    prev_rain = state["last_rain"]

    should_alert = False
    alert_tag = ""
    icon = "🌧️"

    if rain >= 100.0:
        current_stage = 3
        icon = "🚨"
        if prev_stage < 3:
            should_alert = True
            alert_tag = "[ĐẠT NGƯỠNG RẤT NGUY HIỂM ≥ 100MM]"
            stage_dict[st_key] = {"stage": 3, "last_rain": rain}
        elif rain > prev_rain:
            should_alert = True
            alert_tag = f"[MƯA TĂNG TỪ {prev_rain}MM ➔ {rain}MM (≥ 100MM)]"
            stage_dict[st_key] = {"stage": 3, "last_rain": rain}

    elif rain >= 50.0:
        current_stage = 2
        icon = "⚠️"
        if prev_stage < 2:
            should_alert = True
            alert_tag = "[CẢNH BÁO LẦN 2: MƯA TĂNG LÊN ≥ 50MM]"
            stage_dict[st_key] = {"stage": 2, "last_rain": rain}
        elif prev_stage == 2 and (rain - prev_rain) >= 15.0:
            should_alert = True
            alert_tag = f"[MƯA TĂNG TỪ {prev_rain}MM ➔ {rain}MM]"
            stage_dict[st_key] = {"stage": 2, "last_rain": rain}

    elif rain >= 30.0:
        current_stage = 1
        icon = "🌧️"
        if prev_stage < 1:
            should_alert = True
            alert_tag = "[CẢNH BÁO LẦN 1: ĐẠT NGƯỠNG ≥ 30MM]"
            stage_dict[st_key] = {"stage": 1, "last_rain": rain}

    return should_alert, alert_tag, icon, current_stage

def fetch_vrain_rain_stations(min_rain=30.0):
    now_vn = datetime.utcnow() + timedelta(hours=7)
    updated_at = now_vn.strftime("%H:%M:%S %d/%m/%Y")
    time_range_text = f"Tích lũy ngày (tính từ 00:00 {now_vn.strftime('%d/%m')})"
    
    headers = {**HEADERS_DEFAULT}
    alerts = []
    seen_stations = set()

    try:
        res = requests.get(VRAIN_DATA_URL, headers=headers, timeout=12)
        if res.status_code == 200:
            raw_data = res.json()
            items_list = raw_data if isinstance(raw_data, list) else (raw_data.get("data") or raw_data.get("stations") or [])

            for item in items_list:
                if not isinstance(item, dict): continue
                st_obj = item.get("station", {}) if isinstance(item.get("station"), dict) else item
                name = str(st_obj.get("name") or st_obj.get("stationName") or item.get("name") or "").strip()
                
                if not name or name.lower() in ["none", "null", "trạm không tên"]:
                    continue

                rain_val = item.get("sumDepth") if item.get("sumDepth") is not None else (st_obj.get("sumDepth") or item.get("depth"))
                try:
                    rain_total = float(rain_val) if rain_val is not None else 0.0
                except (ValueError, TypeError):
                    rain_total = 0.0

                if rain_total >= min_rain:
                    st_key = name.lower()
                    if st_key not in seen_stations:
                        seen_stations.add(st_key)
                        area_info = st_obj.get("area") or item.get("area")
                        loc = area_info.get("name") if isinstance(area_info, dict) else str(area_info or "Thanh Hóa")
                        
                        alerts.append({
                            "key": st_key, "name": name, "location": str(loc).strip(), "rain": round(rain_total, 1)
                        })
    except Exception as e:
        print(f"❌ Lỗi fetch Vrain: {e}")

    alerts.sort(key=lambda x: x["rain"], reverse=True)
    return {"has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "time_range": time_range_text, "updated_at": updated_at}

def format_vrain_message(data, is_auto=False):
    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: MƯA VRAIN CẬP NHẬT THAY ĐỔI]</b>" if is_auto else "🌧️ <b>[CẢNH BÁO MƯA VRAIN.VN THANH HÓA]</b>"
    msg = f"{header}\n🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n📅 <i>Khung giờ:</i> <code>{data['time_range']}</code>\n📊 <i>Số trạm thay đổi:</i> <b>{data['count']} trạm</b>\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1):
        icon = alert.get("icon", "🌧️")
        tag = f"\n   └ <i>Trạng thái: {alert['tag']}</i>" if "tag" in alert else ""
        msg += f"{icon} <b>{idx}. Trạm: {alert['name']}</b> ({alert['location']})\n   └ Lượng mưa: <b>{alert['rain']} mm</b>{tag}\n\n"
    msg += "🌐 <i>Nguồn: vrain.vn (Tỉnh 27)</i>"
    return msg

def run_rain_check_logic():
    global SENT_VRAIN_STAGES
    try:
        vrain_data = fetch_vrain_rain_stations(min_rain=30.0)
        if vrain_data.get("has_warning"):
            alerts_to_send = []
            for a in vrain_data['alerts']:
                st_key = a['key']
                rain = a['rain']
                should_alert, alert_tag, icon, level = check_rain_alert_level(st_key, rain, SENT_VRAIN_STAGES)
                if should_alert:
                    a_copy = dict(a)
                    a_copy['tag'] = alert_tag
                    a_copy['icon'] = icon
                    alerts_to_send.append(a_copy)

            if alerts_to_send:
                v_copy = dict(vrain_data)
                v_copy['alerts'] = alerts_to_send
                v_copy['count'] = len(alerts_to_send)
                broadcast_alert(format_vrain_message(v_copy, is_auto=True))
    except Exception as e:
        print(f"❌ Lỗi quét Vrain: {e}")

# ==================== 2. NGUỒN TRẠM THỦY VĂN TỰ ĐỘNG (IOT) ====================
def get_station_status():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    headers = {**HEADERS_DEFAULT, 'Content-Type': 'application/json'}
    payload = {"tokenkey": IOT_TOKENKEY}
    try:
        res = requests.post(IOT_STATION_URL, json=payload, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"HTTP {res.status_code}"}
        data = res.json()
        devices = data.get("list_devices", [])
        station_list = [{"id": str(dev.get("Device_id", "")), "name": dev.get("Device_name", "Không rõ tên"), "status": bool(dev.get("Status", False)), "area": dev.get("area", "Khác")} for dev in devices]
        return {"status": "success", "has_data": len(station_list) > 0, "stations": station_list, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def format_station_message(data):
    if data.get("status") != "success": return f"❌ Lỗi kết nối API trạm: <code>{data.get('message')}</code>"
    stations = data.get("stations", [])
    online_count = sum(1 for s in stations if s["status"])
    msg = f"📡 <b>[TRẠNG THÁI HỆ THỐNG TRẠM THỦY VĂN TỰ ĐỘNG]</b>\n🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n🟢 Kết nối: <b>{online_count}</b> | 🔴 Mất kết nối: <b>{len(stations) - online_count}</b>\n───────────────────\n"
    for s in stations:
        icon = "🟢" if s["status"] else "🔴"
        msg += f"{icon} <b>{s['name']}</b> - <i>{'Đang kết nối' if s['status'] else 'MẤT KẾT NỐI'}</i>\n"
    return msg

def run_station_check_logic():
    global STATION_PREVIOUS_STATUS
    station_data = get_station_status()
    if station_data.get("status") == "success":
        for station in station_data.get("stations", []):
            st_id = station["id"]
            st_name = station["name"]
            is_online = station["status"]
            if st_id in STATION_PREVIOUS_STATUS:
                prev_online = STATION_PREVIOUS_STATUS[st_id]
                if prev_online and not is_online:
                    broadcast_alert(f"🚨 <b>[CẢNH BÁO THIẾT BỊ]</b>\n⚠️ Trạm: <b>{st_name}</b> MẤT KẾT NỐI!")
                elif not prev_online and is_online:
                    broadcast_alert(f"✅ <b>[THÔNG BÁO THIẾT BỊ]</b>\nTrạm: <b>{st_name}</b> ĐÃ KẾT NỐI LẠI!")
            STATION_PREVIOUS_STATUS[st_id] = is_online

# ==================== 3. NGUỒN RADAR DÔNG SÉT (IWEATHER) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {**HEADERS_DEFAULT, 'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX', 'Accept': 'application/json'}
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}"}
        matches = re.findall(r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)', res.text, re.IGNORECASE)
        unique_locs = list(set([m.strip(' ",') for m in matches]))
        alerts = [{"location": loc, "time": now_vn.strftime('%H:%M %d/%m/%Y')} for loc in unique_locs]
        return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def format_iweather_message(data, is_auto=False):
    if not data.get("has_warning"): return f"⚡ <b>[RADAR DÔNG SÉT - IWEATHER]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Không phát hiện mây dông/sét tại Thanh Hóa."
    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT THANH HOÁ]</b>" if is_auto else "⚡ <b>[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]</b>"
    msg = f"{header}\n🕒 <i>Quét lúc:</i> <code>{data['updated_at']}</code>\n📡 <i>Số vùng:</i> <b>{data['count']} khu vực</b>\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1): msg += f"🌩️ <b>{idx}.</b> {alert['location']}\n"
    return msg

def run_iweather_check_logic():
    global LAST_IWEATHER_COUNT
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            broadcast_alert(format_iweather_message(iweather_data, is_auto=True))
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

# ==================== 4. NGUỒN CẢNH BÁO THIÊN TAI (VNDMS) ====================
def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    alerts = []
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                for item in data:
                    alerts.append({
                        "id": str(item.get("Id") or item.get("Code") or hash(str(item))),
                        "title": item.get("DisasterName") or item.get("Name") or "CẢNH BÁO THIÊN TAI",
                        "risk_level": item.get("RiskLevel") or item.get("Risk") or "Cấp độ 3",
                        "start_time": item.get("StartDate") or item.get("Time") or "Đang diễn ra",
                        "description": item.get("Description") or item.get("Note") or item.get("Content") or "Vùng ảnh hưởng: Thanh Hóa"
                    })
    except Exception as e: print(f"❌ Lỗi VNDMS: {e}")
    return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}

def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"): return f"🏛️ <b>[GIÁM SÁT THIÊN TAI - VNDMS]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n🟢 Chưa ghi nhận sự kiện thiên tai khẩn cấp."
    header = "🚨 <b>[CẢNH BÁO KHẨN CẤP TỪ VNDMS]</b>" if is_auto else "🏛️ <b>[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]</b>"
    msg = f"{header}\n🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n📋 <i>Số bản tin:</i> <b>{data['count']} tin</b>\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 <b>BẢN TIN {idx}: {alert['title'].upper()}</b>\n⏱ <b>Bắt đầu:</b> {alert['start_time']}\n⚠️ <b>Cấp độ rủi ro:</b> <code>{alert['risk_level']}</code>\n📝 <b>Nội dung:</b> {alert['description']}\n▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    return msg

def run_vndms_check_logic():
    global SENT_VNDMS_IDS
    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = [a for a in vndms_data['alerts'] if a['id'] not in SENT_VNDMS_IDS]
        if new_alerts:
            for a in new_alerts: SENT_VNDMS_IDS.add(a['id'])
            v_copy = dict(vndms_data)
            v_copy['alerts'] = new_alerts
            v_copy['count'] = len(new_alerts)
            broadcast_alert(format_vndms_message(v_copy, is_auto=True))

# ==================== 5. NGUỒN CẢNH BÁO LŨ QUÉT & SẠT LỞ (NCHMF) ====================
def get_nchmf_landslide_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    now_str = now_vn.strftime("%H:%M:%S %d/%m/%Y")
    date_param = now_vn.strftime("%Y-%m-%d %H:00:00")
    headers = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'User-Agent': HEADERS_DEFAULT['User-Agent'], 'X-Requested-With': 'XMLHttpRequest'}
    payload = {"sogiodubao": "6", "date": date_param}
    SEVERITY_ORDER = {"Rất cao": 3, "Cao": 2, "Trung bình": 1, "Mức rất cao": 3, "Mức cao": 2, "Mức trung bình": 1}

    try:
        res = requests.post(f"{NCHMF_CANHBAO_URL}?_t={int(time.time())}", data=payload, headers=headers, verify=False, timeout=12)
        if res.status_code != 200: return {"status": "error", "message": f"HTTP {res.status_code}", "has_warning": False, "count": 0, "alerts": [], "updated_at": now_str}
        data = res.json()
        items_list = data if isinstance(data, list) else (data.get("data") or data.get("result") or [])
        dict_2cap = {}

        for item in items_list:
            if not isinstance(item, dict): continue
            prov_ref = str(item.get("province_ref") or item.get("provinceId") or item.get("cityID") or "")
            ten_tinh = str(item.get("provinceName_2cap") or item.get("provinceName") or item.get("ten_tinh") or "").lower()
            if prov_ref in ["27", "33"] or "thanh" in ten_tinh:
                xa_2cap = str(item.get("commune_name_2cap") or item.get("ten_xa_2cap") or "Chưa rõ").strip()
                huyen = str(item.get("district_name") or item.get("ten_huyen") or "").strip()
                key_2cap = str(item.get("commune_id_2cap") or f"{huyen}_{xa_2cap}").strip()
                lq_raw = item.get("nguycoluquet") or item.get("lu_quet") or "Trung bình"
                sl_raw = item.get("nguycosatlo") or item.get("sat_lo") or "Trung bình"
                max_sev = max(SEVERITY_ORDER.get(lq_raw, 1), SEVERITY_ORDER.get(sl_raw, 1))
                icon = "🟣" if max_sev >= 3 else ("🔴" if max_sev == 2 else "🟠")

                if key_2cap not in dict_2cap:
                    dict_2cap[key_2cap] = {"key": key_2cap, "huyen": huyen, "xa_2cap": xa_2cap, "lu_quet": str(lq_raw), "sat_lo": str(sl_raw), "max_sev": max_sev, "icon": icon}

        alerts = list(dict_2cap.values())
        return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_str}
    except Exception as e: return {"status": "error", "message": str(e), "has_warning": False, "count": 0, "alerts": [], "updated_at": now_str}

def format_nchmf_message(data, is_auto=False):
    if not data.get("has_warning"): return f"⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Không có nguy cơ tại Thanh Hóa."
    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: LŨ QUÉT & SẠT LỞ THANH HÓA]</b>" if is_auto else "⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ]</b>"
    msg = f"{header}\n🕒 <i>Thời gian:</i> <code>{data['updated_at']}</code>\n📍 <i>Tổng số vùng:</i> <b>{data['count']} xã/thị trấn</b>\n───────────────────\n"
    for idx, item in enumerate(data['alerts'], 1):
        msg += f"{item['icon']} <b>{idx}. Địa bàn: {item['xa_2cap']}</b> ({item['huyen']})\n   └ Lũ quét: <i>{item['lu_quet']}</i> | Sạt lở: <i>{item['sat_lo']}</i>\n\n"
    return msg

def run_landslide_check_logic():
    global SENT_LANDSLIDE_STATES
    landslide_data = get_nchmf_landslide_warning()
    if landslide_data.get("status") == "success" and landslide_data.get("has_warning"):
        new_or_updated_alerts = []
        for a in landslide_data['alerts']:
            key = a['key']
            sev = a['max_sev']
            if key not in SENT_LANDSLIDE_STATES or sev > SENT_LANDSLIDE_STATES[key]:
                SENT_LANDSLIDE_STATES[key] = sev
                new_or_updated_alerts.append(a)

        if new_or_updated_alerts:
            l_copy = dict(landslide_data)
            l_copy['alerts'] = new_or_updated_alerts
            l_copy['count'] = len(new_or_updated_alerts)
            broadcast_alert(format_nchmf_message(l_copy, is_auto=True))

# ==================== TỔNG HỢP TOÀN BỘ QUÉT ĐỊNH KỲ ====================
def run_all_scans():
    run_rain_check_logic()       # 1. Quét mưa Vrain (≥30mm)
    run_station_check_logic()    # 2. Quét trạng thái trạm IoT
    run_iweather_check_logic()   # 3. Quét dông sét iWeather
    run_vndms_check_logic()      # 4. Quét thiên tai VNDMS
    run_landslide_check_logic()  # 5. Quét lũ quét sạt lở NCHMF

# ==================== LỆNH TELEGRAM MANUAL ====================
def process_user_command(chat_id, text_raw):
    cmd = text_raw.split()[0].split("@")[0].lower() if text_raw else ""

    if cmd in ["/tram", "/thietbi"]:
        st_data = get_station_status()
        send_telegram_message(chat_id, format_station_message(st_data))
    elif cmd == "/vrain":
        vrain_data = fetch_vrain_rain_stations(min_rain=30.0)
        send_telegram_message(chat_id, format_vrain_message(vrain_data, is_auto=False) if vrain_data.get("has_warning") else "🌧️ <b>[GIÁM SÁT MƯA VRAIN THANH HÓA]</b>\n\n✅ Chưa có trạm nào đạt ngưỡng ≥ 30mm.")
    elif cmd == "/dong":
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))
    elif cmd in ["/thientai", "/canhbao"]:
        vndms_data = get_vndms_warning()
        send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))
    elif cmd in ["/luquet", "/satlo"]:
        landslide_data = get_nchmf_landslide_warning()
        send_telegram_message(chat_id, format_nchmf_message(landslide_data, is_auto=False))
    elif cmd in ["/start", "/tong", "/thoitiet"]:
        st_data = get_station_status()
        vrain_data = fetch_vrain_rain_stations(min_rain=30.0)
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        vndms_data = get_vndms_warning()
        landslide_data = get_nchmf_landslide_warning()

        now_str = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
        msg = f"📊 <b>[BÁO CÁO TỔNG HỢP THỜI TIẾT & HỆ THỐNG]</b>\n"
        msg += f"🕒 <i>Thời gian:</i> <code>{now_str}</code>\n═══════════════════\n\n"
        
        online = sum(1 for s in st_data.get("stations", []) if s["status"]) if st_data.get("status") == "success" else 0
        total_st = len(st_data.get("stations", [])) if st_data.get("status") == "success" else 0
        
        msg += f"📡 <b>TRẠM TỰ ĐỘNG:</b> 🟢 {online}/{total_st} trạm kết nối\n"
        msg += f"🌧️ <b>MƯA VRAIN (≥ 30mm):</b> " + (f"⚠️ Có {vrain_data['count']} trạm\n" if vrain_data.get("has_warning") else "🟢 Bình thường\n")
        msg += f"🌩️ <b>DÔNG SÉT (iWeather):</b> " + (f"⚠️ Có {iweather_data['count']} vùng phát triển\n" if iweather_data.get("has_warning") else "🟢 An toàn\n")
        msg += f"🏛️ <b>THIÊN TAI (VNDMS):</b> " + (f"🚨 Có {vndms_data['count']} bản tin khẩn\n" if vndms_data.get("has_warning") else "🟢 Không có cảnh báo\n")
        msg += f"⛰️ <b>LŨ QUÉT & SẠT LỞ:</b> " + (f"⚠️ Có {landslide_data['count']} xã/vùng nguy cơ\n" if landslide_data.get("has_warning") else "🟢 An toàn\n")

        msg += "\n💡 <i>Gõ lệnh riêng để xem chi tiết:</i>\n<code>/vrain</code> | <code>/tram</code> | <code>/dong</code> | <code>/thientai</code> | <code>/luquet</code>"
        send_telegram_message(chat_id, msg)

# ==================== ROUTE HTTP PING & WEBHOOK ====================
@app.route('/')
def home():
    run_all_scans()
    return jsonify({"status": "running", "registered_chats": list(REGISTERED_CHATS)}), 200

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json(silent=True)
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text_raw = message.get("text", "").strip()

        REGISTERED_CHATS.add(chat_id)
        save_chats()

        if text_raw:
            threading.Thread(target=process_user_command, args=(chat_id, text_raw), daemon=True).start()

    return "OK", 200

# ==================== QUÉT TỰ ĐỘNG MỖI 10 PHÚT CHẠY NGẦM ====================
def scheduled_task_loop():
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://tram-mua-bot.onrender.com")
    while True:
        try:
            time.sleep(600)  # Đủ 10 phút (600 giây)
            run_all_scans()  # Gọi trực tiếp quét tất cả 5 nguồn dữ liệu
            res = requests.get(app_url, timeout=10) # Self-ping duy trì server Render
            print(f"⏰ [10-Min Scan Success] Status Code: {res.status_code}")
        except Exception as e:
            print(f"❌ Lỗi luồng tự động quét 10 phút: {e}")

threading.Thread(target=scheduled_task_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
