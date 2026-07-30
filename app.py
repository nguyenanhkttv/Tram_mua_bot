import os
import requests
import asyncio
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

# ==================== CẤU HÌNH API IWEATHER ====================
# URL API cảnh báo dông storm thu thập từ DevTools
IWEATHER_WARNING_URL = "https://iweather.gov.vn/api/warningstorm?token=null" 
IWEATHER_RADAR_URL = "https://iweather.gov.vn/api/lastradar?token=null&mode=COM"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")

def check_iweather_storm_warning(region_keyword="Thanh Hóa"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX'
    }
    
    try:
        # Gọi API Cảnh báo dông sét
        res = requests.get(IWEATHER_WARNING_URL, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            
            # Giả định dữ liệu trả về dạng danh sách các vùng cảnh báo
            alerts = []
            if isinstance(data, list):
                for item in data:
                    # Kiểm tra nếu thông tin cảnh báo có chứa tên khu vực/tỉnh thành
                    item_str = str(item)
                    if region_keyword.lower() in item_str.lower():
                        alerts.append(item)
            elif isinstance(data, dict):
                # Trường hợp data trả về dạng dict chứa key 'data' hoặc 'warnings'
                warnings_list = data.get('data', []) or data.get('warnings', [])
                for item in warnings_list:
                    if region_keyword.lower() in str(item).lower():
                        alerts.append(item)
            
            return {
                "status": "success",
                "has_warning": len(alerts) > 0,
                "region": region_keyword,
                "total_alerts": len(alerts),
                "details": alerts,
                "raw_data_sample": data[:2] if isinstance(data, list) else data
            }
        else:
            return {"status": "error", "message": f"HTTP {res.status_code}"}
            
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== FLASK ROUTE & TELEGRAM BOT ====================
@app.route('/')
@app.route('/api/dongset')
def dongset_api():
    result = check_iweather_storm_warning(region_keyword="Thanh Hóa")
    return jsonify(result)

async def dongset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Đang quét dữ liệu dông sét từ iWeather (Tổng cục KTTV)...")
    result = check_iweather_storm_warning(region_keyword="Thanh Hóa")
    
    if result.get("status") == "success":
        if result.get("has_warning"):
            msg = f"⚠️ **CẢNH BÁO DÔNG SÉT / MẤY ĐỐI LƯU!**\n"
            msg += f"📍 **Khu vực:** {result['region']}\n"
            msg += f"🚨 **Số vùng cảnh báo phát hiện:** {result['total_alerts']}\n"
            msg += f"ℹ️ Thông tin chi tiết: Trực quan hóa tại bản đồ CMAX iWeather."
        else:
            msg = f"✅ **AN TOÀN:** Hiện chưa phát hiện mây dông/cảnh báo sét nguy hiểm tại khu vực {result['region']} trên hệ thống iWeather."
    else:
        msg = f"❌ Không thể truy xuất dữ liệu iWeather: {result.get('message')}"
        
    await update.message.reply_text(msg, parse_mode='Markdown')

def setup_telegram_bot():
    if TELEGRAM_BOT_TOKEN:
        try:
            tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            tg_app.add_handler(CommandHandler("start", dongset_command))
            tg_app.add_handler(CommandHandler("dongset", dongset_command))
            
            loop = asyncio.get_event_loop()
            loop.create_task(tg_app.initialize())
            loop.create_task(tg_app.start())
            loop.create_task(tg_app.updater.start_polling())
            print("🤖 Telegram Bot Cảnh báo Dông Sét đã sẵn sàng!")
        except Exception as e:
            print(f"❌ Lỗi Bot: {e}")

setup_telegram_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
