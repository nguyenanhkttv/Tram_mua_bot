import os
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# API iWeather Dông Sét
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")

app = Flask(__name__)

# ==================== HÀM QUÉT DÔNG SÉT IWEATHER ====================
def get_iweather_storm_warning(province_keyword="Thanh Hóa"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX',
        'Accept': 'application/json, text/plain, */*'
    }
    
    try:
        res = requests.get(IWEATHER_STORM_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return {"status": "error", "message": f"Không kết nối được iWeather (HTTP {res.status_code})"}

        data = res.json()
        matched_alerts = []

        items = data if isinstance(data, list) else data.get('data', []) or data.get('features', [])
        
        for item in items:
            item_str = str(item)
            if province_keyword.lower() in item_str.lower():
                if isinstance(item, dict):
                    props = item.get('properties', item)
                    info = {
                        "location": props.get('location', props.get('name', 'Thanh Hóa')),
                        "intensity": props.get('dBZ', props.get('intensity', 'Đang phát triển')),
                        "message": props.get('message', props.get('description', '')),
                        "time": props.get('time', props.get('updated_at', datetime.now().strftime('%H:%M %d/%m/%Y')))
                    }
                    matched_alerts.append(info)
                else:
                    matched_alerts.append({"raw": str(item)})

        return {
            "status": "success",
            "province": province_keyword,
            "has_warning": len(matched_alerts) > 0,
            "count": len(matched_alerts),
            "alerts": matched_alerts,
            "updated_at": datetime.now().strftime("%H:%M:%S %d/%m/%Y")
        }

    except Exception as e:
        return {"status": "error", "message": f"Lỗi xử lý dữ liệu: {str(e)}"}

# ==================== FLASK ROUTING ====================
@app.route('/')
@app.route('/api/dongset')
def dongset_api():
    return jsonify(get_iweather_storm_warning("Thanh Hóa"))

# ==================== TELEGRAM BOT COMMANDS ====================
async def dongset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Đang quét mây đối lưu & dông sét từ iWeather...")
    data = get_iweather_storm_warning("Thanh Hóa")

    if data.get("status") == "success":
        if data.get("has_warning"):
            msg = f"🚨 **CẢNH BÁO DÔNG SÉT CẤP BÁCH - THANH HÓA!**\n"
            msg += f"🕒 *Thời gian:* {data['updated_at']}\n"
            msg += f"📍 **Số vùng dông phát hiện:** {data['count']}\n"
            for idx, alert in enumerate(data['alerts'], 1):
                msg += f"\n**Vùng {idx}:** {alert.get('location', 'Thanh Hóa')}\n"
                if alert.get('message'): msg += f"• *Nội dung:* {alert['message']}\n"
                if alert.get('intensity'): msg += f"• *Cường độ (dBZ):* {alert['intensity']}\n"
            msg += "\n🌐 Xem Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
        else:
            msg = f"✅ **AN TOÀN ({data['updated_at']}):** Hiện chưa phát hiện mây dông hay cảnh báo sét tại khu vực Thanh Hóa."
    else:
        msg = f"❌ Lỗi: {data.get('message')}"

    await update.message.reply_text(msg, parse_mode='Markdown')

# ==================== KÍCH HOẠT BOT TELEGRAM ====================
def start_telegram_bot():
    try:
        tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", dongset_command))
        tg_app.add_handler(CommandHandler("dongset", dongset_command))
        tg_app.add_handler(CommandHandler("dong", dongset_command))
        print("🤖 Bot Telegram đã khởi chạy thành công!")
        tg_app.run_polling(drop_pending_updates=True, close_loop=False)
    except Exception as e:
        print(f"❌ Lỗi Bot Telegram: {e}")

# Kích hoạt Thread ngay khi file app.py được load
bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
bot_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
