import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# ==================== CẤU HÌNH ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
VNDMS_WARNING_URL = "https://vndms.gov.vn/EventDisaster/WarningEvent"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_DEFAULT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

app = Flask(__name__)

# Lưu danh sách Chat ID nhận thông báo tự động (lưu tạm RAM)
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()  # Lưu ID các cảnh báo VNDMS đã gửi để tránh lặp lại

# ==================== 1. HÀM BÓC TÁCH IWEATHER (DÔNG SÉT) ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        **HEADERS_DEFAULT,
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX',
        'Accept': 'application/json, text/plain, */*'
    }
    
    now_vn = datetime.utcnow() + timedelta(hours=7)
    
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được iWeather (HTTP {res.status_code})"}

        raw_text = res.text
        matched_alerts = []
        
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, raw_text, re.IGNORECASE)
        unique_locations = list(set([m.strip(' ",') for m in matches]))

        for loc in unique_locations:
            matched_alerts.append({
                "location": loc,
                "intensity": "Mây dông / Sét phát triển",
                "time": now_vn.strftime('%H:%M %d/%m/%Y')
            })

        return {
            "status": "success",
            "province": province_keyword,
            "has_warning": len(matched_alerts) > 0,
            "count": len(matched_alerts),
            "alerts": matched_alerts,
            "updated_at": now_vn.strftime("%H:%M:%S %d/%m/%Y")
        }

    except Exception as e:
        return {"status": "error", "message": f"Lỗi iWeather: {str(e)}"}

# ==================== 2. HÀM BÓC TÁCH VNDMS (THỜI TIẾT NGUY HIỂM) ====================
def get_vndms_warning():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(VNDMS_WARNING_URL, headers=HEADERS_DEFAULT, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được VNDMS (HTTP {res.status_code})"}

        data = res.json()
        alerts = []

        if isinstance(data, list) and len(data) > 0:
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
        return {"status": "error", "message": f"Lỗi VNDMS: {str(e)}"}

# ==================== HÀM FORMAT MESSAGE (MỖI NGUỒN 1 KIỂU) ====================

# Kiểu Message 1: Cảnh báo Dông sét (iWeather) - Style Radar/Mây đối lưu
def format_iweather_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"⚡ **[RADAR DÔNG SÉT - IWEATHER]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n✅ **AN TOÀN:** Hiện chưa phát hiện mây đối lưu hay nguy cơ dông sét tại Thanh Hóa."
    
    header = "⚠️ **[CẢNH BÁO TỰ ĐỘNG: DÔNG SÉT TỈNH THANH HOÁ]**" if is_auto else "⚡ **[CẢNH BÁO MÂY DÔNG & SÉT - IWEATHER]**"
    msg = f"{header}\n"
    msg += f"🕒 *Thời gian quét:* `{data['updated_at']}`\n"
    msg += f"📡 *Tổng số vùng phát hiện:* **{data['count']} khu vực**\n"
    msg += "───────────────────\n"
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🌩️ **{idx}.** {alert['location']}\n"
    
    msg += "───────────────────\n"
    msg += "🌐 [Mở bản đồ Radar CMAX](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

# Kiểu Message 2: Cảnh báo Thời tiết nguy hiểm (VNDMS) - Style Thẻ Thiên tai/Chính thức
def format_vndms_message(data, is_auto=False):
    if not data.get("has_warning"):
        return f"🏛️ **[GIÁM SÁT THIÊN TAI - VNDMS]**\n🕒 *Cập nhật:* {data['updated_at']}\n\n🟢 **KHÔNG CÓ CẢNH BÁO NÓNG:** Hệ thống chưa ghi nhận sự kiện thiên tai khẩn cấp nào."

    header = "🚨 **[CẢNH BÁO KHẨN CẤP TỪ VNDMS]**" if is_auto else "🏛️ **[CẢNH BÁO THỜI TIẾT NGUY HIỂM - VNDMS]**"
    msg = f"{header}\n"
    msg += f"🕒 *Cập nhật:* `{data['updated_at']}`\n"
    msg += f"📋 *Số bản tin hiện tại:* **{data['count']} tin**\n\n"
    
    for idx, alert in enumerate(data['alerts'], 1):
        msg += f"🔻 **BẢN TIN {idx}: {alert['title'].upper()}**\n"
        msg += f"⏱ **Bắt đầu:** {alert['start_time']}\n"
        msg += f"⚠️ **Cấp độ rủi ro:** `{alert['risk_level']}`\n"
        msg += f"📝 **Nội dung:** {alert['description']}\n"
        msg += "▫️▫️▫️▫️▫️▫️▫️▫️▫️\n"
        
    msg += "🌐 *Nguồn:* Cục QLĐĐ & PCTT (vndms.gov.vn)"
    return msg

# ==================== HÀM GỬI TIN NHẮN TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn Telegram: {e}")

def broadcast_alert(text):
    for chat_id in REGISTERED_CHATS:
        send_telegram_message(chat_id, text)

# ==================== WEB ROUTES ====================
@app.route('/')
def home():
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS
    
    # --- 1. KIỂM TRA IWEATHER (DÔNG SÉT) ---
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            msg_iweather = format_iweather_message(iweather_data, is_auto=True)
            broadcast_alert(msg_iweather)
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    # --- 2. KIỂM TRA VNDMS (THỜI TIẾT NGUY HIỂM) ---
    vndms_data = get_vndms_warning()
    if vndms_data.get("status") == "success" and vndms_data.get("has_warning"):
        new_alerts = []
        for alert in vndms_data['alerts']:
            if alert['id'] not in SENT_VNDMS_IDS:
                SENT_VNDMS_IDS.add(alert['id'])
                new_alerts.append(alert)
        
        # Nếu có cảnh báo VNDMS mới chưa từng gửi -> Bắn tự động
        if new_alerts:
            vndms_data_copy = dict(vndms_data)
            vndms_data_copy['alerts'] = new_alerts
            vndms_data_copy['count'] = len(new_alerts)
            msg_vndms = format_vndms_message(vndms_data_copy, is_auto=True)
            broadcast_alert(msg_vndms)

    return jsonify({
        "status": "running", 
        "active_chats": list(REGISTERED_CHATS),
        "last_iweather_count": LAST_IWEATHER_COUNT,
        "sent_vndms_ids": list(SENT_VNDMS_IDS)
    })

# Webhook nhận lệnh từ Telegram
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # Tự động đăng ký Chat ID
        REGISTERED_CHATS.add(chat_id)

        # Xử lý các câu lệnh
        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/canhbao") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "🔍 *Đang truy vấn dữ liệu từ iWeather & VNDMS...*")
            
            # --- BƯỚC 1: CẢNH BÁO DÔNG SÉT (IWEATHER) TRƯỚC ---
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            if iweather_data.get("status") == "success":
                msg_iweather = format_iweather_message(iweather_data, is_auto=False)
            else:
                msg_iweather = f"❌ Lỗi iWeather: {iweather_data.get('message')}"
            
            send_telegram_message(chat_id, msg_iweather)

            # --- BƯỚC 2: CẢNH BÁO THỜI TIẾT NGUY HIỂM (VNDMS) SAU ---
            vndms_data = get_vndms_warning()
            if vndms_data.get("status") == "success":
                msg_vndms = format_vndms_message(vndms_data, is_auto=False)
            else:
                msg_vndms = f"❌ Lỗi VNDMS: {vndms_data.get('message')}"

            send_telegram_message(chat_id, msg_vndms)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
