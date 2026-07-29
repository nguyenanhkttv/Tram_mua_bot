import os
import re
import asyncio
from datetime import datetime
from urllib.parse import urljoin
from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==================== CẤU HÌNH HỆ THỐNG ====================
BASE_URL = "http://222.255.11.82"
LOGIN_URL = f"{BASE_URL}/Login.aspx"
REPORT_URL = f"{BASE_URL}/Modules/MuaTudong/BaoCaoChiTietDuLieu.aspx"

USERNAME = "admin"
PASSWORD = "ttdl@2021"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")

app = Flask(__name__)

def fetch_and_login():
    """Xử lý khởi tạo phiên, bóc tách ViewState và đăng nhập ASP.NET"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    })

    # 1. Tải trang Đăng nhập
    res_get = session.get(LOGIN_URL, timeout=15)
    soup_login = BeautifulSoup(res_get.text, 'html.parser')

    form = soup_login.find('form')
    target_action = LOGIN_URL
    if form and form.get('action'):
        target_action = urljoin(LOGIN_URL, form.get('action'))

    payload = {}
    inputs_found = []
    inputs = form.find_all('input') if form else soup_login.find_all('input')

    for inp in inputs:
        name = inp.get('name')
        if not name:
            continue
        val = inp.get('value', '')
        inp_type = inp.get('type', '').lower()
        inputs_found.append({"name": name, "type": inp_type, "value": val[:30]})
        payload[name] = val

    # 2. Xác định tên các ô dữ liệu
    user_key = None
    pass_key = None
    btn_key = None

    for k in payload.keys():
        k_lower = k.lower()
        if 'user' in k_lower or 'acc' in k_lower or 'taikhoan' in k_lower or 'txtuser' in k_lower:
            user_key = k
        elif 'pass' in k_lower or 'matkhau' in k_lower or 'txtpass' in k_lower:
            pass_key = k
        elif 'btn' in k_lower or 'login' in k_lower or 'dangnhap' in k_lower or 'submit' in k_lower:
            btn_key = k

    payload[user_key or 'txtUsername'] = USERNAME
    payload[pass_key or 'txtPassword'] = PASSWORD

    if btn_key and btn_key not in payload:
        payload[btn_key] = 'Đăng nhập'

    # 3. Gửi POST xác thực
    session.headers.update({'Referer': LOGIN_URL})
    res_post = session.post(target_action, data=payload, timeout=15, allow_redirects=True)

    # 4. Tải trang Báo cáo
    res_report = session.get(REPORT_URL, timeout=15)
    soup_report = BeautifulSoup(res_report.text, 'html.parser')

    return session, res_post, res_report, soup_report, inputs_found, payload

def get_station_data():
    active_list = []
    lost_1h_3h = []
    lost_over_3h = []

    try:
        session, res_post, res_report, soup_report, inputs_found, payload = fetch_and_login()

        tables = soup_report.find_all('table')
        target_table = None

        for t in tables:
            rows = t.find_all('tr')
            if len(rows) > 2:
                target_table = t
                break

        if not target_table:
            return {
                "status": "error",
                "message": "Không tìm thấy bảng dữ liệu trạm trên trang web.",
                "debug_info": {
                    "report_url": res_report.url,
                    "inputs_detected": inputs_found,
                    "page_title": soup_report.title.string if soup_report.title else None
                }
            }

        rows = target_table.find_all('tr')
        now = datetime.now()

        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cols) < 2:
                continue

            station_name = cols[1] if len(cols) > 1 else cols[0]
            last_time_str = cols[2] if len(cols) > 2 else cols[-1]

            if any(k in station_name for k in ["Tên", "Trạm", "STT", "Tên trạm"]):
                continue

            try:
                match = re.search(r'\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(:\d{2})?', last_time_str)
                if match:
                    time_str = match.group(0)
                    fmt = "%d/%m/%Y %H:%M:%S" if time_str.count(':') == 2 else "%d/%m/%Y %H:%M"
                    last_time = datetime.strptime(time_str, fmt)
                    
                    diff_minutes = (now - last_time).total_seconds() / 60
                    item = {"name": station_name, "last_time": time_str}

                    if diff_minutes <= 60:
                        active_list.append(item)
                    elif 60 < diff_minutes <= 180:
                        item["lost_minutes"] = int(diff_minutes)
                        lost_1h_3h.append(item)
                    else:
                        item["lost_minutes"] = int(diff_minutes)
                        lost_over_3h.append(item)
                else:
                    lost_over_3h.append({"name": station_name, "last_time": last_time_str or "Không xác định"})
            except Exception:
                lost_over_3h.append({"name": station_name, "last_time": last_time_str})

        return {
            "status": "success",
            "updated_at": now.strftime("%H:%M:%S %d/%m/%Y"),
            "summary": {
                "active_count": len(active_list),
                "lost_1h_3h_count": len(lost_1h_3h),
                "lost_over_3h_count": len(lost_over_3h)
            },
            "active": active_list,
            "lost_1h_3h": lost_1h_3h,
            "lost_over_3h": lost_over_3h
        }

    except Exception as e:
        return {"status": "error", "message": f"Lỗi kết nối máy chủ: {str(e)}"}

# ==================== CẤU HÌNH FLASK API ====================
@app.route('/')
@app.route('/api/report')
def report_api():
    data = get_station_data()
    return jsonify(data)

# ==================== CẤU HÌNH TELEGRAM BOT ====================
def format_telegram_message(data):
    if data.get("status") != "success":
        return f"⚠️ **BÁO LỖI**: {data.get('message', 'Không thể lấy dữ liệu')}"

    msg = f"📊 **BÁO CÁO TRẠM MƯA TỰ ĐỘNG**\n"
    msg += f"🕒 *Cập nhật lúc:* {data['updated_at']}\n\n"

    msg += f"✅ **ĐANG HOẠT ĐỘNG ({data['summary']['active_count']} trạm):**\n"
    if data['active']:
        for st in data['active'][:10]:
            msg += f"• {st['name']} ({st['last_time']})\n"
        if len(data['active']) > 10:
            msg += f"• ... và {len(data['active']) - 10} trạm khác.\n"
    else:
        msg += "• Không có trạm nào.\n"

    msg += f"\n⚠️ **MẤT KẾT NỐI TỪ 1H - 3H ({data['summary']['lost_1h_3h_count']} trạm):**\n"
    if data['lost_1h_3h']:
        for st in data['lost_1h_3h']:
            msg += f"• {st['name']} (Mất: {st.get('lost_minutes', '?')} phút)\n"
    else:
        msg += "• Không có trạm nào.\n"

    msg += f"\n🚫 **MẤT KẾT NỐI TRÊN 3H ({data['summary']['lost_over_3h_count']} trạm):**\n"
    if data['lost_over_3h']:
        for st in data['lost_over_3h']:
            msg += f"• {st['name']} (Lần cuối: {st['last_time']})\n"
    else:
        msg += "• Không có trạm nào.\n"

    return msg

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang cào dữ liệu trạm mưa, vui lòng đợi trong giây lát...")
    data = get_station_data()
    message_text = format_telegram_message(data)
    await update.message.reply_text(message_text, parse_mode='Markdown')

def setup_telegram_bot():
    if TELEGRAM_BOT_TOKEN:
        try:
            tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            tg_app.add_handler(CommandHandler("start", start_command))
            tg_app.add_handler(CommandHandler("baocao", start_command))
            
            loop = asyncio.get_event_loop()
            loop.create_task(tg_app.initialize())
            loop.create_task(tg_app.start())
            loop.create_task(tg_app.updater.start_polling())
            print("🤖 Telegram Bot đã khởi chạy!")
        except Exception as e:
            print(f"❌ Lỗi Bot: {e}")

setup_telegram_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
