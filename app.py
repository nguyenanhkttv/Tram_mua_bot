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

USERNAME = "admin"
PASSWORD = "ttdl@2021"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")

app = Flask(__name__)

# ==================== HÀM CÀO DỮ LIỆU ASP.NET ====================
def get_station_data():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    })

    active_list = []
    lost_1h_3h = []
    lost_over_3h = []

    try:
        # BƯỚC 1: GET trang Login để khởi tạo Session Cookie và lấy token Hidden Inputs
        res_get = session.get(LOGIN_URL, timeout=15)
        soup_login = BeautifulSoup(res_get.text, 'html.parser')

        form = soup_login.find('form')
        target_action = LOGIN_URL
        if form and form.get('action'):
            target_action = urljoin(LOGIN_URL, form.get('action'))

        payload = {}
        inputs = soup_login.find_all('input')
        for inp in inputs:
            name = inp.get('name')
            if name:
                payload[name] = inp.get('value', '')

        # Gán thông tin đăng nhập
        payload['txtUserName'] = USERNAME
        payload['txtPWD'] = PASSWORD
        payload['btnSubmit'] = 'Đăng Nhập'

        # BƯỚC 2: POST xác thực Đăng nhập
        session.headers.update({
            'Referer': LOGIN_URL,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': BASE_URL
        })
        res_post = session.post(target_action, data=payload, timeout=15, allow_redirects=True)

        # BƯỚC 3: TRUY CẬP TRANG CHỦ DEFAULT.ASPX ĐỂ KÍCH HOẠT PHIÊN DỮ LIỆU
        session.headers.update({'Referer': res_post.url})
        res_default = session.get(DEFAULT_URL, timeout=15)

        # BƯỚC 4: Truy cập trang Báo cáo chi tiết sau khi đã qua Default.aspx
        session.headers.update({'Referer': DEFAULT_URL})
        res_report = session.get(REPORT_URL, timeout=15)
        soup_report = BeautifulSoup(res_report.text, 'html.parser')

        # BƯỚC 5: Tìm bảng dữ liệu
        tables = soup_report.find_all('table')
        target_table = None

        for t in tables:
            rows = t.find_all('tr')
            if len(rows) >= 2:
                text_content = t.get_text()
                if any(kw in text_content for kw in ["Trạm", "trạm", "Thời gian", "Lượng mưa", "STT"]):
                    target_table = t
                    break
                elif not target_table and len(rows) > 3:
                    target_table = t

        # Kiểm tra iframe phụ nếu trang chính chứa iframe
        if not target_table:
            iframes = soup_report.find_all('iframe')
            for iframe in iframes:
                src = iframe.get('src')
                if src:
                    iframe_url = urljoin(REPORT_URL, src)
                    res_iframe = session.get(iframe_url, timeout=15)
                    soup_iframe = BeautifulSoup(res_iframe.text, 'html.parser')
                    for t in soup_iframe.find_all('table'):
                        if len(t.find_all('tr')) >= 2:
                            target_table = t
                            break
                if target_table:
                    break

        if not target_table:
            page_title = soup_report.title.string.strip() if soup_report.title else "N/A"
            return {
                "status": "error",
                "message": "Không tìm thấy bảng dữ liệu trạm trên trang web.",
                "debug_info": {
                    "page_title": page_title,
                    "final_url": res_report.url,
                    "tables_found": len(tables),
                    "is_login_page": "txtPWD" in res_report.text
                }
            }

        rows = target_table.find_all('tr')
        now = datetime.now()

        # BƯỚC 6: Trích xuất và phân loại trạm
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cols) < 2:
                continue

            station_name = cols[1] if len(cols) > 1 else cols[0]
            last_time_str = cols[2] if len(cols) > 2 else cols[-1]

            if any(k in station_name.lower() for k in ["tên", "trạm", "stt", "tên trạm"]):
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
                    lost_over_3h.append({"name": station_name, "last_time": last_time_str or "Mất kết nối"})
            except Exception:
                lost_over_3h.append({"name": station_name, "last_time": str(last_time_str)})

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
        return {
            "status": "error",
            "message": f"Lỗi xử lý: {str(e)}",
            "traceback": traceback.format_exc()
        }

# ==================== FLASK API & TELEGRAM BOT ====================
@app.route('/')
@app.route('/api/report')
def report_api():
    data = get_station_data()
    return jsonify(data)

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
