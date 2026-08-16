import os
import re
import json
import requests
import urllib3
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Tắt cảnh báo SSL Certificate khi gọi NCHMF / Vrain
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CẤU HÌNH ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"

# NGUỒN VRAIN: Lượng mưa chuyên dùng & KTTV
VRAIN_DETAILS_URL = "https://vrain.vn/api/v2/home/33/details"
KTTV_DETAILS_URL = "https://kttv.vrain.vn/api/v2/home/14/details"

# NGUỒN 4: Lũ quét & Sạt lở đất (Cục Khí tượng Thủy văn)
NCHMF_LANDSLIDE_URL = "https://luquetsatlo.nchmf.gov.vn/LayerMapBox/getThongTinXaCBTheoVungVe"
# Đã thêm ngoặc vuông [...] bọc bên ngoài Feature
POLYGON_THANH_HOA = [{
    "id": "bbox_thanh_hoa",
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[104.3, 20.8], [106.2, 20.8], [106.2, 19.2], [104.3, 19.2], [104.3, 20.8]]]
    }
}]

# API Giám sát Trạm Thủy Văn Tự Động Tỉnh Thanh Hóa
IOT_STATION_URL = os.environ.get("IOT_STATION_URL", "http://iot.vientnmt.com:8888/api/DataAPI/ReadDeviceUser")
IOT_TOKENKEY = os.environ.get("IOT_TOKENKEY", "rRh2Tws7G5ba7HCNLjc73REyXSixwmIPK2tE8t5Nr...")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# Bộ nhớ hệ thống
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()
SENT_LANDSLIDE_KEYS = set()
STATION_PREVIOUS_STATUS = {}
SENT_RAIN_ALERTS = set()  # Lưu các khóa trạm mưa to đã báo để tránh spam

# ==================== LOGIC LẤY & LỌC DỮ LIỆU MƯA TO (VRAIN & KTTV) ====================
def get_hydro_time_range():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    
    # Nếu thời gian hiện tại trước 19:00 (ví dụ 17:56)
    if now_vn.hour < 19:
        start_dt = (now_vn - timedelta(days=1)).replace(hour=19, minute=0, second=0)
        end_dt = now_vn.replace(hour=18, minute=59, second=59)
    else:
        # Nếu đã qua hoặc bằng 19:00 hôm nay
        start_dt = now_vn.replace(hour=19, minute=0, second=0)
        end_dt = (now_vn + timedelta(days=1)).replace(hour=18, minute=59, second=59)
        
    return start_dt, end_dt, now_vn.strftime("%H:%M:%S %d/%m/%Y")

# ==================== CỘNG DỒN DỮ LIỆU TỪ 19:00 HÔM TRƯỚC TỚI 18:59 HÔM NAY ====================
def calculate_kttv_rain_19h_to_18h59(start_dt, end_dt):
    total_rain_by_station = {}
    
    dates_to_fetch = [start_dt.strftime("%Y-%m-%d")]
    if start_dt.date() != end_dt.date():
        dates_to_fetch.append(end_dt.strftime("%Y-%m-%d"))

    for date_str in dates_to_fetch:
        try:
            params = {"groupID": 14, "from": date_str, "to": date_str}
            res = requests.get(KTTV_DETAILS_URL, params=params, headers=HEADERS_DEFAULT, timeout=12)
            if res.status_code == 200:
                data = res.json()
                
                details = data.get("details", {}) if isinstance(data, dict) else {}
                stats = details.get("stats", []) if isinstance(details, dict) else []
                
                for st_group in stats:
                    stations = st_group.get("stations", [])
                    for st in stations:
                        city_id = st.get("cityID")
                        city_name = str(st.get("cityName", ""))
                        
                        # Bắt buộc lọc địa bàn Thanh Hóa
                        if not (city_id in [27, "27"] or "Thanh Hóa" in city_name or "Thanh Hoá" in city_name):
                            continue
                            
                        st_name = st.get("stationName") or st.get("area") or st.get("name") or "Trạm không tên"
                        st_key = st_name.strip().lower()

                        if st_key not in total_rain_by_station:
                            total_rain_by_station[st_key] = {"name": st_name, "rain": 0.0, "location": st.get("stationLocation", "Thanh Hóa")}

                        time_points = st.get("timePoints", []) or st.get("intervals", []) or []
                        for tp in time_points:
                            try:
                                val = float(tp.get("val") or tp.get("depth") or tp.get("value") or 0)
                                total_rain_by_station[st_key]["rain"] += val
                            except (ValueError, TypeError):
                                pass

                        if not time_points:
                            try:
                                sum_depth = float(st.get("sumDepth") or st.get("depth") or 0)
                                total_rain_by_station[st_key]["rain"] = max(total_rain_by_station[st_key]["rain"], sum_depth)
                            except (ValueError, TypeError):
                                pass
        except Exception as e:
            print(f"❌ Lỗi tính toán KTTV {date_str}: {e}")

    return total_rain_by_station

# ==================== LẤY VÀ ĐỒNG NHẤT DỮ LIỆU TỔNG HỢP ====================
def fetch_heavy_rain_stations():
    start_dt, end_dt, updated_at = get_hydro_time_range()
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    alerts = []
    seen_stations = set()

    # 1. QUÉT NGUỒN KTTV (Cộng dồn chuẩn 19:00:00 ➔ 18:59:59)
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

    # 2. QUÉT NGUỒN VRAIN THANH HÓA
    try:
        params_vrain = {"groupID": None, "from": start_str[:16], "to": end_str[:16]}
        res = requests.get(VRAIN_DETAILS_URL, params=params_vrain, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code == 200:
            data = res.json()
            stats = data.get("stats", []) if isinstance(data, dict) else []
            for st_group in stats:
                stations = st_group.get("stations", [])
                for st in stations:
                    depth_raw = st.get("depth") or st.get("sumDepth") or 0
                    try:
                        rain = float(depth_raw)
                    except (ValueError, TypeError):
                        rain = 0.0

                    if rain >= 51.0:
                        name = st.get("name") or st.get("area") or "Trạm không tên"
                        st_key = name.strip().lower()
                        
                        if st_key not in seen_stations:
                            seen_stations.add(st_key)
                            alerts.append({
                                "key": f"vrain_{st_key}_{start_str[:10]}",
                                "source": "vrain.vn",
                                "name": name,
                                "location": st.get("area", "Thanh Hóa"),
                                "rain": round(rain, 1)
                            })
    except Exception as e:
        print(f"❌ Lỗi Vrain Details: {e}")

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
    msg += f"📊 <i>Số trạm vượt ngưỡng (≥ 51mm):</i> <b>{data['count']} trạm</b>\n"
    msg += "───────────────────\n"

    for idx, alert in enumerate(data['alerts'], 1):
        level = "🔴 RẤT TO (≥ 100mm)" if alert['rain'] >= 100 else "🟠 TO (51 - 100mm)"
        msg += f"📍 <b>{idx}. Trạm: {alert['name']}</b> ({alert['location']})\n"
        msg += f" 🏢 <i>Nguồn:</i> {alert['source']}\n"
        msg += f" 🌧️ <i>Mưa tích lũy:</i> <b>{alert['rain']} mm</b> ({level})\n\n"

    return msg

# ==================== LUỒNG QUÉT TỰ ĐỘNG MỖI 15 PHÚT ====================
def start_15m_rain_scanner():
    global SENT_RAIN_ALERTS
    while True:
        try:
            rain_data = fetch_heavy_rain_stations()
            if rain_data.get("has_warning"):
                new_alerts = [a for a in rain_data['alerts'] if a['key'] not in SENT_RAIN_ALERTS]
                if new_alerts:
                    for a in new_alerts:
                        SENT_RAIN_ALERTS.add(a['key'])
                    
                    r_copy = dict(rain_data)
                    r_copy['alerts'] = new_alerts
                    r_copy['count'] = len(new_alerts)
                    
                    msg_text = format_rain_alert_msg(r_copy)
                    for chat_id in REGISTERED_CHATS:
                        requests.post(
                            f"{TELEGRAM_API_URL}/sendMessage",
                            json={"chat_id": chat_id, "text": msg_text, "parse_mode": "HTML"},
                            timeout=10
                        )
        except Exception as e:
            print(f"❌ Lỗi luồng 15m scanner: {e}")
            
        time.sleep(900)

threading.Thread(target=start_15m_rain_scanner, daemon=True).start()

# ==================== LOGIC GIÁM SÁT TRẠM THỦY VĂN TỰ ĐỘNG ====================
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
        return f"⚡ <b>[RADAR DÔNG SÉT - IWEATHER]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    
    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]</b>" if is_auto else "⚡ <b>[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]</b>"
    msg = f"{header}\n🕒 <i>Thời gian quét:</i> <code>{data['updated_at']}</code>\n📡 <i>Tổng số vùng:</i> <b>{data['count']} khu vực</b>\n───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1): 
        msg += f"🌩️ <b>{idx}.</b> {alert['location']}\n"
    msg += "───────────────────\n🌐 <a href='https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'>Mở bản đồ Radar CMAX</a>"
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
        return f"🏛️ <b>[GIÁM SÁT THIÊN TAI - VNDMS]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n🟢 <b>KHÔNG CÓ CẢNH BÁO NÓNG:</b> Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."
    
    header = "🚨 <b>[CẢNH BÁO KHẨN CẤP TỪ VNDMS]</b>" if is_auto else "🏛️ <b>[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]</b>"
    msg = f"{header}\n🕒 <i>Cập nhật:</i> <code>{data['updated_at']}</code>\n📋 <i>Số bản tin:</i> <b>{data['count']} tin</b>\n\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 <b>BẢN TIN {idx}: {alert['title'].upper()}</b>\n⏱ <b>Bắt đầu:</b> {alert['start_time']}\n⚠️ <b>Cấp độ rủi ro:</b> <code>{alert['risk_level']}</code>\n📝 <b>Nội dung:</b> {alert['description']}\n▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
    msg += "🌐 <i>Nguồn: Cục QLĐĐ & PCTT (vndms.gov.vn)</i>"
    return msg

# ==================== LOGIC NGUỒN 4: LŨ QUÉT & SẠT LỞ (NCHMF) ====================
NCHMF_CANHBAO_URL = "https://luquetsatlo.nchmf.gov.vn/LayerMapBox/getDSCanhbaoSLLQ"

def get_nchmf_landslide_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    now_str = now_vn.strftime("%H:%M:%S %d/%m/%Y")
    
    # Định dạng tham số date chuẩn theo Payload API: YYYY-MM-DD HH:00:00
    date_param = now_vn.strftime("%Y-%m-%d %H:00:00")
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest'
    }
    
    # Form Data chuẩn từ DevTools
    payload = {
        "sogiodubao": "6",
        "date": date_param
    }
    
    try:
        # Gọi API getDSCanhbaoSLLQ với verify=False
        res = requests.post(NCHMF_CANHBAO_URL, data=payload, headers=headers, verify=False, timeout=12)
        
        if res.status_code != 200:
            return {
                "status": "error",
                "message": f"HTTP {res.status_code}",
                "has_warning": False,
                "count": 0,
                "alerts": [],
                "updated_at": now_str
            }
            
        data = res.json()
        alerts = []
        
        if isinstance(data, list):
            for item in data:
                ten_tinh = str(item.get("ten_tinh", "") or item.get("Tinh", ""))
                
                # Lọc riêng tỉnh Thanh Hóa
                if "Thanh Hóa" in ten_tinh:
                    xa_2cap = item.get("xaname_2cap") or item.get("ten_xa") or "Chưa rõ"
                    xa_hc = item.get("ten_xa", "")
                    lu_quet = item.get("lu_quet") or item.get("CapCB_LQ") or "Mức trung bình"
                    sat_lo = item.get("sat_lo") or item.get("CapCB_SL") or "Mức trung bình"
                    
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
            "updated_at": now_str
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "has_warning": False,
            "count": 0,
            "alerts": [],
            "updated_at": now_str
        }
            
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
            "updated_at": now_str
        }
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Lỗi kết nối ({str(e)})",
            "has_warning": False,
            "count": 0,
            "alerts": [],
            "updated_at": now_str
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")}

def format_nchmf_message(data, is_auto=False):
    if data.get("status") == "error":
        return f"⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ]</b>\n🕒 <i>Cập nhật:</i> {data.get('updated_at')}\n\n❌ <b>LỖI KẾT NỐI:</b> Không thể lấy dữ liệu từ Cục KTTV (<code>{data.get('message')}</code>)."

    if not data.get("has_warning"):
        return f"⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ - NCHMF]</b>\n🕒 <i>Cập nhật:</i> {data['updated_at']}\n\n✅ <b>AN TOÀN:</b> Hiện không có xã/khu vực nào tại Thanh Hóa nằm trong danh sách cảnh báo nguy cơ lũ quét hay sạt lở đất."

    header = "⚠️ <b>[CẢNH BÁO TỰ ĐỘNG: LŨ QUÉT & SẠT LỞ THANH HÓA]</b>" if is_auto else "⛰️ <b>[CẢNH BÁO LŨ QUÉT & SẠT LỞ - THANH HÓA]</b>"
    msg = f"{header}\n🕒 <i>Thời gian:</i> <code>{data['updated_at']}</code>\n📍 <i>Tổng số vùng cảnh báo:</i> <b>{data['count']} xã/khu vực</b>\n───────────────────\n"
    
    for idx, item in enumerate(data['alerts'], 1):
        vung = item['xa_2cap']
        if item['xa_hc'] and item['xa_hc'] != item['xa_2cap']:
            vung += f" ({item['xa_hc']})"
        
        msg += f"📍 <b>{idx}. Địa bàn:</b> {vung}\n"
        msg += f" 🌀 Lũ quét: <b>{item['lu_quet']}</b>\n"
        msg += f" ⛰️ Sạt lở đất: <b>{item['sat_lo']}</b>\n\n"
        
    msg += "🌐 <i>Nguồn: Cục Khí tượng Thủy văn (luquetsatlo.nchmf.gov.vn)</i>"
    return msg

# ==================== HÀM GỬI THÔNG BÁO TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    try: 
        res = requests.post(url, json={
            "chat_id": chat_id, 
            "text": text, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": True
        }, timeout=10)
        if res.status_code != 200:
            print(f"❌ Telegram Error {res.status_code}: {res.text}")
    except Exception as e: 
        print(f"❌ Lỗi gửi Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS: 
        send_telegram_message(chat_id, text)

# ==================== CHẠY LỆNH NGUỒN RIÊNG Ở LUỒNG PHỤ (THREADING) ====================
def process_user_command(chat_id, text_raw):
    cmd = text_raw.split()[0].split("@")[0].lower() if text_raw else ""

    # 1. LỆNH TRA CỨU TRẠM
    if cmd in ["/tram", "/thietbi"]:
        st_data = get_station_status()
        send_telegram_message(chat_id, format_station_message(st_data))

    # 2. LỆNH TRA CỨU MƯA TO (VRAIN & KTTV)
    if cmd in ["/muato", "/mua"]:
        rain_data = fetch_heavy_rain_stations()
        if rain_data.get("has_warning"):
            msg = format_rain_alert_msg(rain_data)
        else:
            msg = f"🌧️ <b>[GIÁM SÁT MƯA THANH HÓA]</b>\n📅 <i>Khung tính:</i> <code>{rain_data['time_range']}</code>\n\n✅ <b>AN TOÀN:</b> Chưa có trạm nào vượt ngưỡng 51mm trong khung 19:00 ➔ 18:59."
        
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}
        )
    # 3. LỆNH TRA CỨU DÔNG SÉT
    elif cmd == "/dong":
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))

    # 4. LỆNH TRA CỨU CẢNH BÁO THIÊN TAI (VNDMS)
    elif cmd in ["/thientai", "/canhbao"]:
        vndms_data = get_vndms_warning()
        send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))

    # 5. LỆNH TRA CỨU LŨ QUÉT & SẠT LỞ (NCHMF)
    elif cmd in ["/luquet", "/satlo"]:
        send_telegram_message(chat_id, "⛰️ <i>Đang kiểm tra dữ liệu lũ quét & sạt lở từ Cục KTTV...</i>")
        landslide_data = get_nchmf_landslide_warning()
        send_telegram_message(chat_id, format_nchmf_message(landslide_data, is_auto=False))

    # 6. LỆNH TỔNG HỢP GỘP 1 TIN NHẮN
    elif cmd in ["/start", "/tong", "/thoitiet"]:
        st_data = get_station_status()
        rain_data = get_vrain_heavy_rain_warning()
        iweather_data = get_iweather_storm_warning("Thanh Hóa")
        vndms_data = get_vndms_warning()
        landslide_data = get_nchmf_landslide_warning()

        now_str = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
        msg = f"📊 <b>[BÁO CÁO TỔNG HỢP THỜI TIẾT & HỆ THỐNG]</b>\n"
        msg += f"🕒 <i>Thời gian:</i> <code>{now_str}</code>\n"
        msg += "═══════════════════\n\n"

        if st_data.get("status") == "success":
            stations = st_data.get("stations", [])
            online = sum(1 for s in stations if s["status"])
            msg += f"📡 <b>TRẠM TỰ ĐỘNG:</b> 🟢 {online}/{len(stations)} trạm kết nối\n"
        else:
            msg += f"📡 <b>TRẠM TỰ ĐỘNG:</b> ❌ Lỗi kết nối API\n"

        if rain_data.get("has_warning"):
            msg += f"🌧️ <b>LƯỢNG MƯA TO (≥ 51mm):</b> ⚠️ Có {rain_data['count']} trạm vượt ngưỡng\n"
        else:
            msg += "🌧️ <b>LƯỢNG MƯA TO (≥ 51mm):</b> 🟢 BÌNH THƯỜNG\n"

        if iweather_data.get("has_warning"):
            msg += f"🌩️ <b>DÔNG SÉT (iWeather):</b> ⚠️ Có {iweather_data['count']} vùng phát triển\n"
        else:
            msg += "🌩️ <b>DÔNG SÉT (iWeather):</b> 🟢 An toàn\n"

        if vndms_data.get("has_warning"):
            msg += f"🏛️ <b>THIÊN TAI (VNDMS):</b> 🚨 Có {vndms_data['count']} bản tin khẩn\n"
        else:
            msg += "🏛️ <b>THIÊN TAI (VNDMS):</b> 🟢 Không có cảnh báo\n"

        if landslide_data.get("has_warning"):
            msg += f"⛰️ <b>LŨ QUÉT & SẠT LỞ:</b> ⚠️ Có {landslide_data['count']} xã/vùng nguy cơ\n"
        else:
            msg += "⛰️ <b>LŨ QUÉT & SẠT LỞ:</b> 🟢 An toàn\n"

        msg += "\n💡 <i>Gõ từng lệnh riêng để xem chi tiết:</i>\n"
        msg += "<code>/tram</code> | <code>/mua</code> | <code>/dong</code> | <code>/thientai</code> | <code>/luquet</code>"

        send_telegram_message(chat_id, msg)

# ==================== ROUTE CRON TỰ ĐỘNG ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS, STATION_PREVIOUS_STATUS, SENT_LANDSLIDE_KEYS, SENT_RAIN_ALERTS
    
    # 1. Giám sát Trạm
    station_data = get_station_status()
    if station_data.get("status") == "success":
        for station in station_data.get("stations", []):
            st_id = station["id"]
            st_name = station["name"]
            is_online = station["status"]
            
            if st_id in STATION_PREVIOUS_STATUS:
                prev_online = STATION_PREVIOUS_STATUS[st_id]
                if prev_online and not is_online:
                    alert_msg = (f"🚨 <b>[CẢNH BÁO MẤT KẾT NỐI TRẠM]</b>\n"
                                 f"⚠️ Trạm: <b>{st_name}</b>\n"
                                 f"❌ Trạng thái: <b>ĐÃ MẤT KẾT NỐI</b>\n"
                                 f"🕒 Thời gian phát hiện: <code>{station_data['updated_at']}</code>")
                    broadcast_alert(alert_msg)
                elif not prev_online and is_online:
                    recovery_msg = (f"✅ <b>[THÔNG BÁO TRẠM KẾT NỐI LẠI]</b>\n"
                                    f"📡 Trạm: <b>{st_name}</b>\n"
                                    f"🟢 Trạng thái: <b>ĐÃ KẾT NỐI LẠI</b>\n"
                                    f"🕒 Thời gian: <code>{station_data['updated_at']}</code>")
                    broadcast_alert(recovery_msg)
            STATION_PREVIOUS_STATUS[st_id] = is_online

    # 2. Giám sát Mưa to (Vrain & KTTV Thanh Hóa)
    rain_data = fetch_heavy_rain_stations()
    if rain_data.get("has_warning"):
        new_rain_alerts = [a for a in rain_data['alerts'] if a['key'] not in SENT_RAIN_ALERTS]
        if new_rain_alerts:
            for a in new_rain_alerts:
                SENT_RAIN_ALERTS.add(a['key'])
            r_copy = dict(rain_data)
            r_copy['alerts'] = new_rain_alerts
            r_copy['count'] = len(new_rain_alerts)
            broadcast_alert(format_rain_alert_msg(r_copy))

    # 3. iWeather Dông sét
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
            for a in new_alerts: 
                SENT_VNDMS_IDS.add(a['id'])
            v_copy = dict(vndms_data)
            v_copy['alerts'] = new_alerts
            v_copy['count'] = len(new_alerts)
            broadcast_alert(format_vndms_message(v_copy, is_auto=True))

    # 5. NCHMF Lũ quét & Sạt lở
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

    return jsonify({"status": "running", "registered_chats": list(REGISTERED_CHATS)})

# ==================== TELEGRAM WEBHOOK ====================
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text_raw = message.get("text", "").strip()

        REGISTERED_CHATS.add(chat_id)

        # Chạy xử lý lệnh ở Thread riêng
        threading.Thread(target=process_user_command, args=(chat_id, text_raw)).start()

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
