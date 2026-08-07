import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# ==================== CẤU HÌNH ====================
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

# Lưu danh sách Chat ID nhận thông báo tự động (lưu tạm RAM)
REGISTERED_CHATS = set()
LAST_ALERT_COUNT = 0

# ==================== HÀM BÓC TÁCH DỮ LIỆU IWEATHER ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX',
        'Accept': 'application/json, text/plain, */*'
    }
    
    # Tính giờ Việt Nam (UTC+7)
    now_vn = datetime.utcnow() + timedelta(hours=7)
    
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được iWeather (HTTP {res.status_code})"}

        raw_text = res.text
        matched_alerts = []
        
        # Regex tìm tất cả các cụm địa danh kết thúc bằng "Tỉnh Thanh Hóa" hoặc "Thanh Hoá"
        pattern = r'([^"\[\]\\]+?Tỉnh Thanh Hoá|[^"\[\]\\]+?Tỉnh Thanh Hóa)'
        matches = re.findall(pattern, raw_text, re.IGNORECASE)

        # Lọc bỏ trùng lặp và làm sạch chuỗi
        unique_locations = list(set([m.strip(' ",') for m in matches]))

        for loc in unique_locations:
            matched_alerts.append({
                "location": loc,
                "intensity": "Mây dông / Sét phát triển",
                "message": "Phát hiện vùng mây đối lưu nguy hiểm gây dông sét",
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
        return {"status": "error", "message": f"Lỗi xử lý dữ liệu: {str(e)}"}

# ==================== HÀM GỬI TIN NHẮN TELEGRAM ====================
def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
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
    global LAST_ALERT_COUNT
    # Khi cron-job.org ping vào đây, bot tự động kiểm tra cảnh báo
    data = get_iweather_storm_warning("Thanh Hóa")
    
    if data.get("status") == "success":
        current_count = data.get("count", 0)
        
        # Nếu phát hiện dông sét VÀ số lượng vùng dông tăng/thay đổi -> BẮN TỰ ĐỘNG
        if data.get("has_warning") and current_count != LAST_ALERT_COUNT:
            msg = f"⚠️ **CẢNH BÁO TỰ ĐỘNG: PHÁT HIỆN DÔNG SÉT TẠI THANH HÓA!**\n"
            msg += f"🕒 *Thời gian quét:* {data['updated_at']}\n"
            msg += f"📍 **Số khu vực phát hiện:** {current_count}\n"
            for idx, alert in enumerate(data['alerts'], 1):
                msg += f"\n**{idx}.** {alert['location']}\n"
            
            msg += "\n🌐 Xem trực quan Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
            broadcast_alert(msg)
            LAST_ALERT_COUNT = current_count
        elif not data.get("has_warning"):
            LAST_ALERT_COUNT = 0

    return jsonify({
        "status": "running", 
        "active_chats": list(REGISTERED_CHATS),
        "last_alert_count": LAST_ALERT_COUNT
    })

# Webhook nhận lệnh từ Telegram
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # Tự động đăng ký Chat ID người dùng vào danh sách nhận tin tự động
        REGISTERED_CHATS.add(chat_id)

        if text.startswith("/start") or text.startswith("/dong") or text.startswith("/dongset"):
            send_telegram_message(chat_id, "⚡ Đang quét dữ liệu mây đối lưu & dông sét từ iWeather...")
            
            data = get_iweather_storm_warning("Thanh Hóa")
            
            if data.get("status") == "success":
                if data.get("has_warning"):
                    msg = f"🚨 **CẢNH BÁO DÔNG SÉT TẠI THANH HÓA!**\n"
                    msg += f"🕒 *Cập nhật:* {data['updated_at']}\n"
                    msg += f"📍 **Phát hiện {data['count']} khu vực có mây dông:**\n"
                    for idx, alert in enumerate(data['alerts'], 1):
                        msg += f"\n**{idx}.** {alert['location']}"
                    
                    msg += "\n\n🌐 **Bản đồ Radar:** https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
                else:
                    msg = f"✅ **AN TOÀN ({data['updated_at']}):** Hiện chưa phát hiện mây dông hay cảnh báo sét tại khu vực Thanh Hóa."
            else:
                msg = f"❌ Lỗi: {data.get('message')}"

            send_telegram_message(chat_id, msg)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
