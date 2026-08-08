import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# ==================== CẤU HÌNH ENDPOINTS ====================
VNDMS_EVENTS_API = "https://vndms.gov.vn/api/Disaster/GetListDisaster"
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

REGISTERED_CHATS = set()
PROCESSED_EVENT_IDS = set()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*'
}

# ==================== 1. BÓC TÁCH THIÊN TAI (VNDMS) ====================
def fetch_vndms_disasters():
    now_vn = datetime.utcnow() + timedelta(hours=7)
    alerts = []
    try:
        res = requests.get(VNDMS_EVENTS_API, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            disaster_list = data.get("data", []) if isinstance(data, dict) else data
            if isinstance(disaster_list, list):
                for item in disaster_list:
                    alerts.append({
                        "id": str(item.get("id") or item.get("code", "")),
                        "title": (item.get("name") or item.get("title") or "CẢNH BÁO THIÊN TAI").upper(),
                        "time": item.get("startDate") or now_vn.strftime('%H:%M %d/%m/%Y'),
                        "risk_level": item.get("riskLevel") or item.get("level") or "Chưa xác định",
                        "location": item.get("affectedArea") or item.get("location") or "Toàn quốc / Biển Đông",
                        "summary": (item.get("summary") or item.get("description") or "Đang cập nhật diễn biến...").strip()
                    })
        return {"status": "success", "alerts": alerts, "updated_at": now_vn.strftime("%H:%M %d/%m/%Y")}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== 2. BÓC TÁCH DÔNG SẾT RADAR (iWeather) ====================
def fetch_iweather_lightning(province_keyword="Thanh Hóa"):
    now_vn = datetime.utcnow() + timedelta(hours=7)
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return {"status": "error", "alerts": []}
            
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, res.text, re.IGNORECASE)
        unique_locs = list(set([m.strip(' ",') for m in matches]))
        
        alerts = [{"location": loc, "type": "Dông sét / Mây đối lưu"} for loc in unique_locs]
        return {
            "status": "success",
            "has_lightning": len(alerts) > 0,
            "alerts": alerts,
            "updated_at": now_vn.strftime("%H:%M %d/%m/%Y")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== FORMAT MẪU TIN NHẮN ====================
def format_vndms_msg(alert, updated_at):
    msg = f"⚡ **CẢNH BÁO THỜI TIẾT & THIÊN TAI**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"📢 **CẢNH BÁO THIÊN TAI HỆ THỐNG VNDMS**\n📅 *Cập nhật:* {updated_at}\n\n"
    msg += f"🌀 **{alert['title']}**\n⚠️ **Cấp độ rủi ro:** Cấp {alert['risk_level']}\n"
    msg += f"📍 **Khu vực ảnh hưởng:** {alert['location']}\n\n"
    msg += f"📋 **THÔNG TIN TÓM TẮT / DIỄN BIẾN:**\n- {alert['summary']}\n\n"
    msg += f"🔗 [Xem trực tiếp trên VNDMS](https://vndms.gov.vn/)"
    return msg

def format_lightning_msg(data):
    msg = f"⚡ **CẢNH BÁO DÔNG SẾT KỊP THỜI (iWeather Radar)**\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"🕒 *Cập nhật:* {data['updated_at']}\n"
    msg += f"📍 **Phát hiện {len(data['alerts'])} khu vực mây đối lưu/dông sét tại Thanh Hóa:**\n"
    for idx, item in enumerate(data['alerts'], 1):
        msg += f"\n**{idx}.** {item['location']}"
    msg += "\n\n🌐 [Xem trực quan Radar Dông Sét](https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX)"
    return msg

# ==================== BOT / WEB ROUTES ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print(f"Error: {e}")

@app.route('/')
def home():
    global PROCESSED_EVENT_IDS
    # Quét VNDMS
    vndms_data = fetch_vndms_disasters()
    if vndms_data.get("status") == "success":
        for alert in vndms_data.get("alerts", []):
            event_key = alert["id"] if alert["id"] else alert["title"]
            if event_key not in PROCESSED_EVENT_IDS:
                msg = format_vndms_msg(alert, vndms_data["updated_at"])
                for cid in REGISTERED_CHATS: send_telegram_message(cid, msg)
                PROCESSED_EVENT_IDS.add(event_key)

    return jsonify({"status": "running", "active_chats": list(REGISTERED_CHATS)})

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        REGISTERED_CHATS.add(chat_id)

        # Trả lời người dùng theo lệnh yêu cầu
        if text.startswith("/start") or text.startswith("/thientai"):
            # Lấy bản tin VNDMS
            vndms_data = fetch_vndms_disasters()
            if vndms_data.get("alerts"):
                for alert in vndms_data["alerts"]:
                    send_telegram_message(chat_id, format_vndms_msg(alert, vndms_data["updated_at"]))
            else:
                send_telegram_message(chat_id, "✅ **VNDMS:** Hiện không có sự kiện thiên tai diện rộng nguy hiểm.")

        elif text.startswith("/dongset") or text.startswith("/dong"):
            # Lấy bản tin Dông sét iWeather
            iweather_data = fetch_iweather_lightning("Thanh Hóa")
            if iweather_data.get("has_lightning"):
                send_telegram_message(chat_id, format_lightning_msg(iweather_data))
            else:
                send_telegram_message(chat_id, f"✅ **iWeather ({iweather_data['updated_at']}):** Chưa phát hiện ổ dông/sét tại khu vực Thanh Hóa.")

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
