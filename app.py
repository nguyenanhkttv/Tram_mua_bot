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

# Bộ nhớ lưu danh sách Chat ID nhận tin nhắn tự động
REGISTERED_CHATS = set()
LAST_IWEATHER_COUNT = 0
SENT_VNDMS_IDS = set()

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
    global LAST_IWEATHER_COUNT, SENT_VNDMS_IDS
    
    # Quét Dông sét iWeather
    iweather_data = get_iweather_storm_warning("Thanh Hóa")
    if iweather_data.get("status") == "success":
        current_count = iweather_data.get("count", 0)
        if iweather_data.get("has_warning") and current_count != LAST_IWEATHER_COUNT:
            broadcast_alert(format_iweather_message(iweather_data, is_auto=True))
            LAST_IWEATHER_COUNT = current_count
        elif not iweather_data.get("has_warning"):
            LAST_IWEATHER_COUNT = 0

    # Quét Cảnh báo VNDMS
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

    return jsonify({"status": "running", "registered_chats": list(REGISTERED_CHATS)})

# ==================== TELEGRAM WEBHOOK (NHẬN LỆNH TỪ USER) ====================
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # Tự động ghi nhớ Chat ID người dùng
        REGISTERED_CHATS.add(chat_id)

        # Xử lý khi nhận các câu lệnh tra cứu
        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/canhbao") or text.startswith("/thoitiet"):
            send_telegram_message(chat_id, "🔍 *Đang quét dữ liệu iWeather & VNDMS...*")
            
            # 1. Trả về thông tin Dông Sét
            iweather_data = get_iweather_storm_warning("Thanh Hóa")
            send_telegram_message(chat_id, format_iweather_message(iweather_data, is_auto=False))
            
            # 2. Trả về thông tin Cảnh báo VNDMS
            vndms_data = get_vndms_warning()
            send_telegram_message(chat_id, format_vndms_message(vndms_data, is_auto=False))

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
