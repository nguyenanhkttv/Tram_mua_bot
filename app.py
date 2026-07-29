import os
import re
import asyncio
from datetime import datetime
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

# ==================== HÀM CÀO DỮ LIỆU ASP.NET ====================
def get_station_data():
    """Hàm xử lý đăng nhập ASP.NET và lấy danh sách trạm mưa"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': LOGIN_URL
    })

    active_list = []
    lost_1h_3h = []
    lost_over_3h = []

    try:
        # Bước 1: GET trang Login lấy các thuộc tính ViewState ban đầu
        res_get = session.get(LOGIN_URL, timeout=15)
        soup_login = BeautifulSoup(res_get.text, 'html.parser')

        # Tự động trích xuất mọi thẻ input (đặc biệt là ViewState & EventValidation)
        payload = {}
        for inp in soup_login.find_all('input'):
            name = inp.get('name')
            if not name:
                continue
            val = inp.get('value', '')
            inp_type = inp.get('type', '').lower()

            if inp_type in ['hidden', 'submit', 'button']:
                payload[name] = val

        # Gán chính xác tài khoản và mật khẩu
        # Hỗ trợ cả tên trường mặc định lẫn trường động của ASP.NET
        user_field = None
        pass_field = None
        for inp in soup_login.find_all('input'):
            name = inp.get('name', '')
            if 'user' in name.lower() or 'acc' in name.lower() or 'taikhoan' in name.lower():
                user_field = name
            elif 'pass' in name.lower() or 'matkhau' in name.lower():
                pass_field = name

        payload[user_field or 'txtUsername'] = USERNAME
        payload[pass_field or 'txtPassword'] = PASSWORD
        if 'btnLogin' not in payload:
            payload['btnLogin'] = 'Đăng nhập'

        # Bước 2: Gửi POST Đăng nhập (Cho phép Redirect)
        res_post = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)

        # Bước 3: Truy cập trang Báo cáo chi tiết dữ liệu
        res_report = session.get(REPORT_URL, timeout=15)
        soup_report = BeautifulSoup(res_report.text, 'html.parser')

        # Lấy tất cả các bảng tìm thấy trên trang báo cáo
        tables = soup_report.find_all('table')
        target_table = None

        for t in tables:
            rows = t.find_all('tr')
            if len(rows) > 2:  # Bảng dữ liệu thực sự sẽ có trên 2 hàng
                target_table = t
                break

        if not target_table:
            # Nếu bị chuyển hướng về lại trang Login -> Sai tài khoản/mật khẩu hoặc lỗi Session
            if "Login.aspx" in res_report.url or soup_report.find('input', {'type': 'password'}):
                return {
                    "status": "error",
                    "message": "Đăng nhập không thành công. Hãy kiểm tra lại tài khoản hoặc kết nối máy chủ."
                }
            return {
                "status": "error",
                "message": "Không tìm thấy bảng dữ liệu trạm trên trang web."
            }

        rows = target_table.find_all('tr')
        now = datetime.now()

        # Bước 4: Duyệt và phân loại trạng thái trạm
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cols) < 2:
                continue

            station_name = cols[1] if len(cols) > 1 else cols[0]
            last_time_str = cols[2] if len(cols) > 2 else cols[-1]

            # Bỏ qua các dòng tiêu đề trùng lặp trong bảng
            if any(k in station_name for k in ["Tên", "Trạm", "STT"]):
                continue

            try:
                # Tìm định dạng ngày giờ DD/MM/YYYY HH:MM(:SS)
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
    """Định dạng văn bản hiển thị đẹp trên Telegram"""
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
            print("🤖 Telegram Bot đã khởi chạy thành công!")
        except Exception as e:
            print(f"❌ Không thể khởi chạy Telegram Bot: {e}")

setup_telegram_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)