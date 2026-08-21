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
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"
VRAIN_DETAILS_URL = "https://vrain.vn/api/v2/home/33/details"
KTTV_DETAILS_URL = "https://kttv.vrain.vn/api/v2/home/14/details"
NCHMF_CANHBAO_URL = "https://luquetsatlo.nchmf.gov.vn/LayerMapBox/getDSCanhbaoSLLQ"

IOT_STATION_URL = os.environ.get("IOT_STATION_URL", "http://iot.vientnmt.com:8888/api/DataAPI/ReadDeviceUser")
IOT_TOKENKEY = os.environ.get("IOT_TOKENKEY", "rRh2Tws7G5ba7HCNLjc73REyXSixwmIPK2tE8t5Nr...")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# ==================== LƯU TRỮ CHAT ID VÀ TRẠNG THÁI GỬI ====================
CHAT_FILE = "chats.json"

def load_chats():
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
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
SENT_LANDSLIDE_KEYS = set()
STATION_PREVIOUS_STATUS = {}

# Lưu mức cảnh báo mưa đã gửi cho từng trạm theo ngày (Key -> Mức: 51 hoặc 100) để xử lý báo mới và báo tiếp khi vượt 100mm
SENT_RAIN_LEVELS = {}

# ==================== LOGIC MƯA TO CHUẨN THỦY VĂN (19:00 ➔ 18:59) ====================
def get_hydro_time_range():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    if now_vn.hour < 19:
        start_dt = (now_vn - timedelta(days=1)).replace(hour=19, minute=0, second=0)
        end_dt = now_vn.replace(hour=18, minute=59, second=59)
    else:
        start_dt = now_vn.replace(hour=19, minute=0, second=0)
        end_dt = (now_vn + timedelta(days=1)).replace(hour=18, minute=59, second=59)
    return start_dt, end_dt, now_vn.strftime("%H:%M:%S %d/%m/%Y")

def calculate_kttv_rain_19h_to_18h59(start_dt, end_dt):
    total_rain_by_station = {}
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        params = {"groupID": 14, "from": start_str, "to": end_str}
        res = requests.get(KTTV_DETAILS_URL, params=params, headers=HEADERS_DEFAULT, timeout=15)
        if res.status_code == 200:
            data = res.json()
            details = data.get("details", data) if isinstance(data, dict) else {}
            stats = details.get("stats", details.get("data", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            
            for st_group in stats:
                stations = st_group.get("stations", [st_group] if isinstance(st_group, dict) and ("stationName" in st_group or "name" in st_group) else [])
                for st in stations:
                    if not isinstance(st, dict):
                        continue
                    
                    city_id = str(st.get("cityID", ""))
                    city_name = str(st.get("cityName", "") or st.get("province", "") or st.get("stationLocation", ""))
                    
                    is_thanh_hoa = (
                        city_id in ["27", "38"] or 
                        "thanh hó" in city_name.lower() or 
                        "thanh hoa" in city_name.lower()
                    )
                    if not is_thanh_hoa:
                        continue
                        
                    st_name = st.get("stationName") or st.get("area") or st.get("name") or "Trạm không tên"
                    st_key = st_name.strip().lower()

                    if st_key not in total_rain_by_station:
                        total_rain_by_station[st_key] = {"name": st_name, "rain": 0.0, "location": st.get("stationLocation", "Thanh Hóa")}

                    rain_val = 0.0
                    try:
                        rain_val = float(st.get("sumDepth") or st.get("depth") or st.get("value") or 0)
                    except (ValueError, TypeError):
                        pass

                    time_points = st.get("timePoints", []) or st.get("intervals", []) or []
                    tp_sum = 0.0
                    for tp in time_points:
                        try:
                            tp_sum += float(tp.get("val") or tp.get("depth") or tp.get("value") or 0)
                        except (ValueError, TypeError):
                            pass
                            
                    total_rain_by_station[st_key]["rain"] = max(rain_val, tp_sum)
    except Exception as e:
        print(f"❌ Lỗi KTTV: {e}")

    return total_rain_by_station

def fetch_heavy_rain_stations():
    start_dt, end_dt, updated_at = get_hydro_time_range()
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    alerts = []
    seen_stations = set()

    # 1. Quét từ KTTV
    kttv_data = calculate_kttv_rain_19h_to_18h59(start_dt, end_dt)
    for st_key, st_info in kttv_data.items():
        if st_info["rain"] >= 51.0:
            seen_stations.add(st_key)
            alerts.append({
                "key": f"kttv_{st_key}_{start_str[:10]}",
                "source": "kttv.vrain.vn",
                "name": st_info["name"],
                "location": st_info["location"],
                "rain": round(st_info["rain"], 1)
            })

    # 2. Quét từ Vrain Details
    for group_id in [None, 33, 14]:
        try:
            params_vrain = {"groupID": group_id, "from": start_str, "to": end_str}
            res = requests.get(VRAIN_DETAILS_URL, params=params_vrain, headers=HEADERS_DEFAULT, timeout=12)
            if res.status_code == 200:
                data = res.json()
                details = data.get("details", data) if isinstance(data, dict) else {}
                stats = details.get("stats", details.get("data", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                
                for st_group in stats:
                    stations = st_group.get("stations", [st_group] if isinstance(st_group, dict) and ("name" in st_group or "stationName" in st_group) else [])
                    for st in stations:
                        if not isinstance(st, dict):
                            continue
                        
                        city_id = str(st.get("cityID", ""))
                        city_name = str(st.get("cityName", "") or st.get("province", "") or st.get("area", "") or st.get("stationLocation", ""))
                        
                        is_thanh_hoa = (
                            city_id in ["27", "38"] or 
                            "thanh hó" in city_name.lower() or 
                            "thanh hoa" in city_name.lower()
                        )
                        if not is_thanh_hoa:
                            continue

                        depth_raw = st.get("sumDepth") or st.get("depth") or st.get("value") or 0
                        try:
                            rain = float(depth_raw)
                        except (ValueError, TypeError):
                            rain = 0.0

                        time_points = st.get("timePoints", []) or st.get("intervals", []) or []
                        tp_sum = 0.0
                        for tp in time_points:
                            try:
                                tp_sum += float(tp.get("val") or tp.get("depth") or tp.get("value") or 0)
                            except (ValueError, TypeError):
                                pass
                                
                        rain = max(rain, tp_sum)

                        if rain >= 51.0:
                            name = st.get("name") or st.get("stationName") or st.get("area") or "Trạm không tên"
                            st_key = name.strip().lower()
                            if st_key not in seen_stations:
                                seen_stations.add(st_key)
                                alerts.append({
                                    "key": f"vrain_{st_key}_{start_str[:10]}",
                                    "source": "vrain.vn",
                                    "name": name,
                                    "location": st.get("stationLocation") or st.get("area") or "Thanh Hóa",
                                    "rain": round(rain, 1)
                                })
        except Exception as e:
            print(f"❌ Lỗi Vrain Details group {group_id}: {e}")

    return {
        "has_warning": len(alerts) > 0,
        "count": len(alerts),
        "alerts": alerts,
        "time_range": f"{start_dt.strftime('%H:%M %d/%m')} ➔ {end_dt.strftime('%H:%M %d/%m')}",
        "updated_at": updated_at
    }

def format_rain_alert_msg(data):
    msg = f"🚨 <b>[CẢNH BÁO MƯA TO THANH HÓA (19:00 ➔ 18:59)]</b>\n"
    msg += f"🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n"
    msg += f"📅 <i>Khung giờ tính:</i> <code>{data['time_range']}</code>\n"
    msg += f"📊 <i>Số trạm đạt mốc cảnh báo:</i> <b>{data['count']} trạm</b>\n"
    msg += "───────────────────\n"

    for idx, alert in enumerate(data['alerts'], 1):
        level = "🔴 RẤT TO (≥ 100mm) - BÁO TIẾP" if alert['rain'] >= 100 else "🟠 TO (51 - 100mm)"
        msg += f"📍 <b>{idx}. Trạm: {alert['name']}</b> ({alert['location']})\n"
        msg += f" 🏢 <i>Nguồn:</i> {alert['source']}\n"
        msg += f" 🌧️ <i>Mưa tích lũy:</i> <b>{alert['rain']} mm</b> ({level})\n\n"

    return msg

get_vrain_heavy_rain_warning = fetch_heavy_rain_stations

# ==================== LUỒNG QUÉT MƯA TỰ ĐỘNG 5 PHÚT ====================
def start_5m_rain_scanner():
    global SENT_RAIN_LEVELS
    while True:
        try:
            rain_data = fetch_heavy_rain_stations()
            if rain_data.get("has_warning"):
                triggered_alerts = []
                for a in rain_data['alerts']:
                    key = a['key']
                    rain = a['rain']
                    
                    # Xác định mức mốc: 100 nếu mưa >= 100, 51 nếu mưa >= 51
                    target_level = 100 if rain >= 100 else (51 if rain >= 51 else 0)
                    prev_level = SENT_RAIN_LEVELS.get(key, 0)
                    
                    # Nếu chưa báo hoặc trạm tăng mức từ 51 lên vượt 100 thì tiếp tục phát cảnh báo nâng cấp
                    if target_level > prev_level:
                        SENT_RAIN_LEVELS[key] = target_level
                        triggered_alerts.append(a)
                
                if triggered_alerts:
                    r_copy = dict(rain_data)
                    r_copy['alerts'] = triggered_alerts
                    r_copy['count'] = len(triggered_alerts)
                    
                    msg_text = format_rain_alert_msg(r_copy)
                    for chat_id in REGISTERED_CHATS:
                        requests.post(
                            f"{TELEGRAM_API_URL}/sendMessage",
                            json={"chat_id": chat_id, "text": msg_text, "parse_mode": "HTML"},
                            timeout=10
                        )
        except Exception as e:
            print(f"❌ Lỗi luồng 5m rain scanner: {e}")
            
        time.sleep(300)

threading.Thread(target=start_5m_rain_scanner, daemon=True).start()

# ==================== CÁC HÀM XỬ LÝ NGUỒN KHÁC (TRẠM, IWEATHER, VNDMS, NCHMF) ====================
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
        return f"❌ <b>[GIÁM SÁT TRẠM]</b> Lỗi kết nối API trạm: <code>{data.get('message')}</code>"
    
    stations = data.get("stations", [])
    online_count = sum(1 for s in stations if s["status"])
    offline_count = len(stations) - online_count
    
    msg = f"📡 <b>[TRẠNG THÁI HỆ THỐNG TRẠM THỦY VĂN TỰ ĐỘNG THANH HÓA]</b>\n"
    msg += f"🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n"
    msg += f"🟢 Đang kết nối: <b>{online_count}</b> | 🔴 Mất kết nối: <b>{offline_count}</b>\n"
    msg += "───────────────────\n"
    for s in stations:
        icon = "🟢" if s["status"] else "🔴"
        status_text = "Đang kết nối" if s["status"] else "MẤT KẾT NỐI"
        msg += f"{icon} <b>{s['name']}</b>\n└ Trạng thái: <i>{status_text}</i>\n"
        
    return msg

def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {**HEADERS_DEFAULT, 'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX', 'Accept': 'application/json'}
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
        return f"⚡ <b>[RADAR DÔNG SÉT - IWEATHER]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    
    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]</b>" if is_auto else "⚡ <b>[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]</b>"
    msg = f"{header}\n🕒 <i>Thời gian quét:</i> <code>{data['updated_at']}</code>\n📡 <i>Tổng số vùng:</i> <b>{data['count']} khu vực</b>\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1): 
        msg += f"🌩️ <b>{idx}.</b> {alert['location']}\n"
    msg += "───────────────────\n🌐 <a href='https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'>Mở bản đồ Radar CMAX</a>"
    return msg

def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    alerts = []
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                for item in data:
                    title = item.get("DisasterName") or item.get("Name") or "CẢNH BÁO THIÊN TAI NGUY HIỂM"
                    risk_level = item.get("RiskLevel") or item.get("Risk") or "Cấp độ 3"
                    start_time = item.get("StartDate") or item.get("Time") or "Đang diễn ra"
                    description = item.get("Description") or item.get("Note") or item.get("Content") or f"Vùng ảnh hưởng: {item.get('Area', 'Biển Đông / Thanh Hóa')}"
                    
                    alerts.append({
                        "id": str(item.get("Id") or item.get("Code") or hash(str(item))),
                        "title": title,
                        "risk_level": risk_level,
                        "start_time": start_time,
                        "description": description
                    })
    except Exception as e:
        print(f"❌ Lỗi VNDMS: {e}")

    return {
        "status": "success", 
        "has_warning": len(alerts) > 0, 
        "count": len(alerts), 
        "alerts": alerts, 
        "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
    }

def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"🏛️ <b>[GIÁM SÁT THIÊN TAI - VNDMS]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n🟢 <b>KHÔNG CÓ CẢNH BÁO NÓNG:</b> Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."
    
    header = "🚨 <b>[CẢNH BÁO KHẨN CẤP TỪ VNDMS]</b>" if is_auto else "🏛️ <b>[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]</b>"
    msg = f"{header}\n🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n📋 <i>Số bản tin:</i> <b>{data['count']} tin</b>\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 <b>BẢN TIN {idx}: {alert['title'].upper()}</b>\n⏱ <b>Bắt đầu:</b> {alert['start_time']}\n⚠️ <b>Cấp độ rủi ro:</b> <code>{alert['risk_level']}</code>\n📝 <b>Nội dung:</b> {alert['description']}\n▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    msg += "🌐 <i>Nguồn: Cục QLĐĐ & PCTT (vndms.gov.vn)</i>"
    return msg

def get_nchmf_landslide_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    now_str = now_vn.strftime("%H:%M:%S %d/%m/%Y")
    date_param = now_vn.strftime("%Y-%m-%d 00:00:00")
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': HEADERS_DEFAULT['User-Agent'],
        'X-Requested-With': 'XMLHttpRequest'
    }
    payload = {"sogiodubao": "6", "date": date_param}
    
    try:
        res = requests.post(NCHMF_CANHBAO_URL, data=payload, headers=headers, verify=False, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"HTTP {res.status_code}", "has_warning": False, "count": 0, "alerts": [], "updated_at": now_str}
            
        data = res.json()
        alerts = []
        
        items_list = []
        if isinstance(data, list):
            items_list = data
        elif isinstance(data, dict):
            for k in ["data", "result", "results", "list", "content", "ds"]:
                if isinstance(data.get(k), list):
                    items_list = data.get(k)
                    break
            if not items_list:
                items_list = [v for v in data.values() if isinstance(v, dict)]

        for item in items_list:
    if not isinstance(item, dict):
        continue
    
    ten_tinh = str(item.get("ten_tinh") or item.get("Tinh") or item.get("provinceName") or "")
    
    if "thanh" in ten_tinh.lower() and ("hoá" in ten_tinh.lower() or "hóa" in ten_tinh.lower()):
        # Lấy tên địa bàn/xã linh hoạt theo đúng key trả về của NCHMF
        xa_2cap = (
            item.get("ten_xa_2cap") or 
            item.get("xaname_2cap") or 
            item.get("ten_xa") or 
            item.get("communeName") or 
            item.get("ten_huyen") or 
            "Chưa rõ"
        )
        xa_hc = item.get("ten_xa", "")
        
        lu_quet = item.get("lu_quet") or item.get("CapCB_LQ") or item.get("warningLevelLQ") or "Mức trung bình"
        sat_lo = item.get("sat_lo") or item.get("CapCB_SL") or item.get("warningLevelSL") or "Mức trung bình"
        
        # Dùng ID thực tế của xã/huyện hoặc kết hợp tên xã + index để làm unique key
        xa_id = item.get("xaid_2cap") or item.get("ma_xa") or item.get("id") or xa_2cap
        alert_key = f"{xa_id}_{xa_2cap}_{lu_quet}_{sat_lo}"
        
        if not any(a['key'] == alert_key for a in alerts):
            alerts.append({
                "key": alert_key,
                "xa_2cap": xa_2cap,
                "xa_hc": xa_hc,
                "lu_quet": lu_quet,
                "sat_lo": sat_lo
            })

        return {"status": "success", "has_warning": len(alerts) > 0, "count": len(alerts), "alerts": alerts, "updated_at": now_str}
    except Exception as e:
        return {"status": "error", "message": str(e), "has_warning": False, "count": 0, "alerts": [], "updated_at": now_str}

def format_nchmf_message(data, is_auto=False):
    if data.get("status") == "error":
        return f"⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ]</b>\n🕒 <i>Cập nhật:</i> {data.get('updated_at')}\n\n❌ <b>LỖI KẾT NỐI:</b> <code>{data.get('message')}</code>"

    if not data.get("has_warning"):
        return f"⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ - NCHMF]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Không có xã/khu vực nào tại Thanh Hóa nằm trong danh sách cảnh báo."

    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: LŨ QUÉT & SẠT LỞ THANH HÓA]</b>" if is_auto else "⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ - THANH HÓA]</b>"
    msg = f"{header}\n🕒 <i>Thời gian:</i> <code>{data['updated_at']}</code>\n📍 <i>Tổng số vùng cảnh báo:</i> <b>{data['count']} xã/khu vực</b>\n───────────────────\n"
    for idx, item in enumerate(data['alerts'], 1):
        vung = item['xa_2cap']
        if item['xa_hc'] and item['xa_hc'] != item['xa_2cap']:
            vung += f" ({item['xa_hc']})"
        msg += f"📍 <b>{idx}. Địa bàn:</b> {vung}\n 🌀 Lũ quét: <b>{item['lu_quet']}</b>\n ⛰️ Sạt lở đất: <b>{item['sat_lo']}</b>\n\n"
        
    msg += "🌐 <i>Nguồn: Cục Khí tượng Thủy văn (luquetsatlo.nchmf.gov.vn)</i>"
    return msg

# ==================== HÀM GỬI THÔNG BÁO ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    try: 
        res = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
        if res.status_code != 200:
            print(f"❌ Telegram Error {res.status_code}: {res.text}")
    except Exception as e: 
        print(f"❌ Lỗi gửi Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS: 
        send_telegram_message(chat_id, text)

# ==================== XỬ LÝ LỆNH NGƯỜI DÙNG ====================
def process_user_command(chat_id, text_raw):
    cmd = text_raw.split()[0].split("@")[0].lower() if text_raw else ""

    if cmd in ["/tram", "/thietbi"]:
        st_data = get_station_status()
        send_telegram_message(chat_id, format_station_message(st_data))

    elif cmd in ["/muato", "/mua"]:
        rain_data = fetch_heavy_rain_stations()
        if rain_data.get("has_warning"):
            msg = format_rain_alert_msg(rain_data)
        else:
            msg = f"🌧️ <b>[GIÁM SÁT MƯA THANH HÓA]</b>\n📅 <i>Khung tính:</i> <code>{rain_data['time_range']}</code>\n\n✅ <b>AN TOÀN:</b> Chưa có trạm nào vượt ngưỡng 51mm."
        send_telegram_message(chat_id, msg)

    elif cmd == "/dong":
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))

    elif cmd in ["/thientai", "/canhbao"]:
        vndms_data = get_vndms_warning()
        send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))

    elif cmd in ["/luquet", "/satlo"]:
        send_telegram_message(chat_id, "⛰️ <i>Đang kiểm tra dữ liệu lũ quét & sạt lở từ Cục KTTV...</i>")
        landslide_data = get_nchmf_landslide_warning()
        send_telegram_message(chat_id, format_nchmf_message(landslide_data, is_auto=False))

    elif cmd in ["/start", "/tong", "/thoitiet"]:
        st_data = get_station_status()
        rain_data = fetch_heavy_rain_stations()
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        vndms_data = get_vndms_warning()
        landslide_data = get_nchmf_landslide_warning()

        now_str = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
        msg = f"📊 <b>[BÁO CÁO TỔNG HỢP THỜI TIẾT & HỆ THỐNG]</b>\n"
        msg += f"🕒 <i>Thời gian:</i> <code>{now_str}</code>\n═══════════════════\n\n"

        if st_data.get("status") == "success":
            stations = st_data.get("stations", [])
            online = sum(1 for s in stations if s["status"])
            msg += f"📡 <b>TRẠM TỰ ĐỘNG:</b> 🟢 {online}/{len(stations)} trạm kết nối\n"
        else:
            msg += f"📡 <b>TRẠM TỰ ĐỘNG:</b> ❌ Lỗi kết nối API\n"

        msg += f"🌧️ <b>LƯỢNG MƯA TO (≥ 51mm):</b> " + (f"⚠️ Có {rain_data['count']} trạm vượt ngưỡng\n" if rain_data.get("has_warning") else "🟢 BÌNH THƯỜNG\n")
        msg += f"🌩️ <b>DÔNG SÉT (iWeather):</b> " + (f"⚠️ Có {iweather_data['count']} vùng phát triển\n" if iweather_data.get("has_warning") else "🟢 An toàn\n")
        msg += f"🏛️ <b>THIÊN TAI (VNDMS):</b> " + (f"🚨 Có {vndms_data['count']} bản tin khẩn\n" if vndms_data.get("has_warning") else "🟢 Không có cảnh báo\n")
        msg += f"⛰️ <b>LŨ QUÉT & SẠT LỞ:</b> " + (f"⚠️ Có {landslide_data['count']} xã/vùng nguy cơ\n" if landslide_data.get("has_warning") else "🟢 An toàn\n")

        msg += "\n💡 <i>Gõ lệnh riêng để xem chi tiết:</i>\n<code>/tram</code> | <code>/mua</code> | <code>/dong</code> | <code>/thientai</code> | <code>/luquet</code>"
        send_telegram_message(chat_id, msg)

# ==================== ROUTE QUÉT ĐỊNH KỲ (CRON) MỖI 5 PHÚT ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS, STATION_PREVIOUS_STATUS, SENT_LANDSLIDE_KEYS, SENT_RAIN_LEVELS
    
    # 1. Trạm
    station_data = get_station_status()
    if station_data.get("status") == "success":
        for station in station_data.get("stations", []):
            st_id = station["id"]
            st_name = station["name"]
            is_online = station["status"]
            if st_id in STATION_PREVIOUS_STATUS:
                prev_online = STATION_PREVIOUS_STATUS[st_id]
                if prev_online and not is_online:
                    broadcast_alert(f"🚨 <b>[CẢNH BÁO MẤT KẾT NỐI TRẠM]</b>\n⚠️ Trạm: <b>{st_name}</b>\n❌ Trạng thái: <b>ĐÃ MẤT KẾT NỐI</b>\n🕒 Thời gian: <code>{station_data['updated_at']}</code>")
                elif not prev_online and is_online:
                    broadcast_alert(f"✅ <b>[THÔNG BÁO TRẠM KẾT NỐI LẠI]</b>\n📡 Trạm: <b>{st_name}</b>\n🟢 Trạng thái: <b>ĐÃ KẾT NỐI LẠI</b>\n🕒 Thời gian: <code>{station_data['updated_at']}</code>")
            STATION_PREVIOUS_STATUS[st_id] = is_online

    # 2. Mưa (Kiểm tra và báo tiếp khi vượt mốc 100mm, cập nhật trạm mới liên tục)
    rain_data = fetch_heavy_rain_stations()
    if rain_data.get("has_warning"):
        triggered_alerts = []
        for a in rain_data['alerts']:
            key = a['key']
            rain = a['rain']
            target_level = 100 if rain >= 100 else (51 if rain >= 51 else 0)
            prev_level = SENT_RAIN_LEVELS.get(key, 0)
            
            if target_level > prev_level:
                SENT_RAIN_LEVELS[key] = target_level
                triggered_alerts.append(a)
                
        if triggered_alerts:
            r_copy = dict(rain_data)
            r_copy['alerts'] = triggered_alerts
            r_copy['count'] = len(triggered_alerts)
            broadcast_alert(format_rain_alert_msg(r_copy))

    # 3. Dông sét
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            broadcast_alert(format_iweather_message(iweather_data, is_auto=True))
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    # 4. VNDMS
    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = [a for a in vndms_data['alerts'] if a['id'] not in SENT_VNDMS_IDS]
        if new_alerts:
            for a in new_alerts: SENT_VNDMS_IDS.add(a['id'])
            v_copy = dict(vndms_data)
            v_copy['alerts'] = new_alerts
            v_copy['count'] = len(new_alerts)
            broadcast_alert(format_vndms_message(v_copy, is_auto=True))

    # 5. Sạt lở
    landslide_data = get_nchmf_landslide_warning()
    if landslide_data.get("status") == "success" and landslide_data.get("has_warning"):
        new_landslide_alerts = [a for a in landslide_data['alerts'] if a['key'] not in SENT_LANDSLIDE_KEYS]
        if new_landslide_alerts:
            for a in new_landslide_alerts: SENT_LANDSLIDE_KEYS.add(a['key'])
            l_copy = dict(landslide_data)
            l_copy['alerts'] = new_landslide_alerts
            l_copy['count'] = len(new_landslide_alerts)
            broadcast_alert(format_nchmf_message(l_copy, is_auto=True))

    return jsonify({"status": "running", "registered_chats": list(REGISTERED_CHATS)}), 200

# ==================== WEBHOOK TELEGRAM ====================
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

# ==================== GIỮ RENDER LUÔN THỨC (SELF-PING) ====================
def keep_alive():
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://tram-mua-bot.onrender.com")
    while True:
        try:
            time.sleep(600)
            res = requests.get(app_url, timeout=10)
            print(f"⏰ Self-ping status: {res.status_code}")
        except Exception as e:
            print(f"❌ Lỗi Self-ping: {e}")

threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
