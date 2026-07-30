import os
import re
import traceback
import asyncio
from datetime import datetime
from urllib.parse import urljoin
from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==================== CẤU HÌNH HỆ THỐNG ====================
BASE_URL = "http://222.255.11.82"
LOGIN_URL = f"{BASE_URL}/Login.aspx"
DEFAULT_URL = f"{BASE_URL}/Default.aspx"
REPORT_URL = f"{BASE_URL}/Modules/MuaTudong/BaoCaoChiTietDuLieu.aspx"

# API iWeather Dông Sét
IWEATHER_STORM_URL = "https://iweather.gov.vn/product/warningstorm?token=null"

USERNAME = "admin"
PASSWORD = "ttdl@2021"

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
            return {"status": "error", "message": f"Không thể kết nối iWeather (Mã lỗi {res.status_code})"}

        data = res.json()
        matched_alerts = []

        # Xử lý bóc tách JSON trả về từ iWeather
        items = data if isinstance(data, list) else data.get('data', []) or data.get('features', [])
        
        for item in items:
            item_str = str(item)
            # Kiểm tra xem vùng cảnh báo có chứa tên tỉnh Thanh Hóa hay không
            if province_keyword.lower() in item_str.lower():
                # Trích xuất thông tin chi tiết nếu cấu trúc là GeoJSON hoặc Dict
                if isinstance(item, dict):
                    props = item.get('properties', item)
                    info = {
                        "location": props.get('location', props.get('name', 'Khu vực Thanh Hóa')),
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
            "total_national_alerts": len(items)
        }

    except Exception as e:
        return {"status": "error", "message": f"Lỗi xử lý dữ liệu: {str(e)}"}

# ==================== HÀM CÀO DỮ LIỆU TRẠM MƯA (222.255.11.82) ====================
def get_station_data():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    })

    active_list, lost_1h_3h, lost_over_3h = [], [], []

    try:
        res_get = session.get(LOGIN_URL, timeout=15)
        soup_login = BeautifulSoup(res_get.text, 'html.parser')

        form = soup_login.find('form')
        target_action = urljoin(LOGIN_URL, form.get('action')) if form and form.get('action') else LOGIN_URL

        payload = {inp.get('name'): inp.get('value', '') for inp in soup_login.find_all('input') if inp.get('name')}
        payload['txtUserName'] = USERNAME
        payload['txtPWD'] = PASSWORD
        payload['btnSubmit'] = 'Đăng Nhập'

        session.headers.update({'Referer': LOGIN_URL, 'Content-Type': 'application/x-www-form-urlencoded'})
        res_post = session.post(target_action, data=payload, timeout=15, allow_redirects=True)

        session.headers.update({'Referer': res_post.url})
        session.get(DEFAULT_URL, timeout=15)

        session.headers.update({'Referer': DEFAULT_URL})
        res_report = session.get(REPORT_URL, timeout=15)
        soup_report = BeautifulSoup(res_report.text, 'html.parser')

        tables = soup_report.find_all('table')
        target_table = None
        for t in tables:
            if len(t.find_all('tr')) >= 2:
                target_table = t
                break

        if not target_table:
            return {"status": "error", "message": "Không tìm thấy bảng dữ liệu trạm trên trang web."}

        rows = target_table.find_all('tr')
        now = datetime.now()

        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cols) < 2: continue
            station_name = cols[1] if len(cols) > 1 else cols[0]
            last_time_str = cols[2] if len(cols) > 2 else cols[-1]

            if any(k in station_name.lower() for k in ["tên", "trạm", "stt"]): continue

            try:
                match = re.search(r'\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(:\d{2})?', last_time_str)
                if match:
                    time_str = match.group(0)
                    fmt = "%d/%m/%Y %H:%M:%S" if time_str.count(':') == 2 else "%d/%m/%Y %H:%M"
                    last_time = datetime.strptime(time_str, fmt)
                    diff_minutes = (now - last_time).total_seconds() / 60
                    item = {"name": station_name, "last_time": time_str}

                    if diff_minutes <= 60: active_list.append(item)
                    elif 60 < diff_minutes <= 180:
                        item["lost_minutes"] = int(diff_minutes)
                        lost_1h_3h.append(item)
                    else:
                        item["lost_minutes"] = int(diff_minutes)
                        lost_over_3h.append(item)
                else:
                    lost_over_3h.append({"name": station_name, "last_time": last_time_str or "Mất kết nối"})
            except Exception:
                lost_over_3h.append({"name": station_name, "last_time": str(last_time_str)})

        return {
            "status": "success",
            "updated_at": now.strftime("%H:%M:%S %d/%m/%Y"),
            "summary": {"active_count": len(active_list), "lost_1h_3h_count": len(lost_1h_3h), "lost_over_3h_count": len(lost_over_3h)},
            "active": active_list, "lost_1h_3h": lost_1h_3h, "lost_over_3h": lost_over_3h
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== FLASK API ROUTING ====================
@app.route('/')
@app.route('/api/report')
def report_api():
    return jsonify(get_station_data())

@app.route('/api/dongset')
def dongset_api():
    return jsonify(get_iweather_storm_warning("Thanh Hóa"))

# ==================== TELEGRAM BOT COMMANDS ====================
async def baocao_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang cào dữ liệu trạm mưa...")
    data = get_station_data()
    if data.get("status") != "success":
        await update.message.reply_text(f"⚠️ **BÁO LỖI**: {data.get('message')}")
        return

    msg = f"📊 **BÁO CÁO TRẠM MƯA TỰ ĐỘNG** ({data['updated_at']})\n\n"
    msg += f"✅ **Đang hoạt động ({data['summary']['active_count']} trạm)**\n"
    msg += f"⚠️ **Mất kết nối 1h - 3h ({data['summary']['lost_1h_3h_count']} trạm)**\n"
    msg += f"🚫 **Mất kết nối > 3h ({data['summary']['lost_over_3h_count']} trạm)**"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def dongset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Đang quét mây đối lưu & dông sét từ iWeather...")
    data = get_iweather_storm_warning("Thanh Hóa")

    if data.get("status") == "success":
        if data.get("has_warning"):
            msg = f"🚨 **CẢNH BÁO DÔNG SÉT CẤP BÁCH - THANH HÓA!**\n\n"
            msg += f"📍 **Số vùng dông phát hiện:** {data['count']}\n"
            for idx, alert in enumerate(data['alerts'], 1):
                msg += f"\n**Vùng {idx}:** {alert.get('location', 'Thanh Hóa')}\n"
                if alert.get('message'): msg += f"• *Nội dung:* {alert['message']}\n"
                if alert.get('intensity'): msg += f"• *Cường độ:* {alert['intensity']}\n"
            msg += "\n🌐 Bản đồ Radar: https://iweather.gov.vn/dashboard?areaRadar=COM&productRadar=CMAX"
        else:
            msg = f"✅ **AN TOÀN:** Hiện chưa phát hiện mây dông hay cảnh báo sét nguy hiểm tại khu vực Thanh Hóa trên iWeather."
    else:
        msg = f"❌ Lỗi truy xuất iWeather: {data.get('message')}"

    await update.message.reply_text(msg, parse_mode='Markdown')

def setup_telegram_bot():
    if TELEGRAM_BOT_TOKEN:
        try:
            tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            tg_app.add_handler(CommandHandler("start", dongset_command))
            tg_app.add_handler(CommandHandler("baocao", baocao_command))
            tg_app.add_handler(CommandHandler("dongset", dongset_command))
            
            loop = asyncio.get_event_loop()
            loop.create_task(tg_app.initialize())
            loop.create_task(tg_app.start())
            loop.create_task(tg_app.updater.start_polling())
            print("🤖 Telegram Bot đã khởi chạy thành công!")
        except Exception as e:
            print(f"❌ Lỗi Bot: {e}")

setup_telegram_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
