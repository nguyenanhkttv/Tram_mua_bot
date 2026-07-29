import os
import re
import asyncio
from datetime import datetime
from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==================== CẤU HÌNH HỆ THỐNG ====================
BASE_URL = "http://222.255.11.82"
LOGIN_URL = f"{BASE_URL}/Login.aspx"
REPORT_URL = f"{BASE_URL}/Modules/MuaTudong/BaoCaoChiTietDuLieu.aspx"

USERNAME = "admin"
PASSWORD = "ttdl@2021"  # Thay bằng mật khẩu đúng của bạn

# Lấy Token từ biến môi trường Render (ưu tiên) hoặc điền trực tiếp
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "DÁN_TOKEN_BOT_CỦA_BẠN_VÀO_ĐÂY")

app = Flask(__name__)

# ==================== HÀM CÀO DỮ LIỆU ASP.NET ====================
def get_station_data():
    """Hàm trung gian: Đăng nhập ASP.NET và bóc tách dữ liệu trạm mưa"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7'
    })

    active_list = []
    lost_1h_3h = []
    lost_over_3h = []

    try:
        # Bước 1: GET trang Login để lấy các thuộc tính ẩn
        res_get = session.get(LOGIN_URL, timeout=15)
        soup_login = BeautifulSoup(res_get.text, 'html.parser')

        # Bóc tách các trường ASP.NET ViewState
        viewstate = soup_login.find('input', {'name': '__VIEWSTATE'})
        eventval = soup_login.find('input', {'name': '__EVENTVALIDATION'})
        generator = soup_login.find('input', {'name': '__VIEWSTATEGENERATOR'})

        # Tìm chính xác tên của ô username và password trong form
        user_field = 'txtUsername'
        pass_field = 'txtPassword'
        btn_field = 'btnLogin'

        # Đội tìm kiểm tra thuộc tính name thực tế nếu form dùng ID phức tạp
        for inp in soup_login.find_all('input'):
            name = inp.get('name', '')
            if 'Username' in name or 'TaiKhoan' in name:
                user_field = name
            elif 'Password' in name or 'MatKhau' in name:
                pass_field = name
            elif 'Login' in name or 'DangNhap' in name:
                btn_field = name

        payload = {
            '__VIEWSTATE': viewstate['value'] if viewstate else '',
            '__EVENTVALIDATION': eventval['value'] if eventval else '',
            '__VIEWSTATEGENERATOR': generator['value'] if generator else '',
            user_field: USERNAME,
            pass_field: PASSWORD,
            btn_field: 'Đăng nhập'
        }

        # Bước 2: Send POST Request để thực hiện Log in
        res_post = session.post(LOGIN_URL, data=payload, timeout=15)

        # Bước 3: Truy cập trang báo cáo dữ liệu
        res_report = session.get(REPORT_URL, timeout=15)
        soup_report = BeautifulSoup(res_report.text, 'html.parser')

        # Tìm bảng chứa dữ liệu trạm
        table = soup_report.find('table')
        if not table:
            return {
                "status": "error",
                "message": "Không tìm thấy bảng dữ liệu trạm trên trang web. Vui lòng kiểm tra lại tài khoản/mật khẩu."
            }

        rows = table.find_all('tr')
        now = datetime.now()

        # Bước 4: Duyệt qua từng dòng dữ liệu (bỏ qua dòng tiêu đề header)
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cols) < 3:
                continue

            # Thông thường: Cột 1 là Tên trạm, Cột 2/3 là Thời gian cập nhật gần nhất
            station_name = cols[1] if len(cols) > 1 else cols[0]
            last_time_str = cols[2] if len(cols) > 2 else ""

            # Thử parse thời gian từ chuỗi (định dạng DD/MM/YYYY HH:MM:SS)
            try:
                # Tìm chuỗi thời gian trong ô bằng regex
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
                    # Nếu không parse được thời gian, mặc định cho vào nhóm mất tín hiệu >3h
                    lost_over_3h.append({"name": station_name, "last_time": "Không xác định"})
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
    """Tạo định dạng tin nhắn đẹp mắt gửi qua Telegram"""
    if data.get("status") != "success":
        return f"⚠️ **BÁO LỖI**: {data.get('message', 'Không thể lấy dữ liệu')}"

    msg = f"📊 **BÁO CÁO TRẠM MƯA TỰ ĐỘNG**\n"
    msg += f"🕒 *Cập nhật lúc:* {data['updated_at']}\n\n"

    msg += f"✅ **ĐANG HOẠT ĐỘNG ({data['summary']['active_count']} trạm):**\n"
    if data['active']:
        for st in data['active'][:10]: # Hiển thị tối đa 10 trạm đầu
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
    """Xử lý khi người dùng nhắn /start hoặc bất kỳ tin nhắn nào cho Bot"""
    await update.message.reply_text("⏳ Đang cào dữ liệu trạm mưa, vui lòng đợi trong giây lát...")
    data = get_station_data()
    message_text = format_telegram_message(data)
    await update.message.reply_text(message_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)

def setup_telegram_bot():
    """Khởi tạo và lắng nghe Telegram Bot"""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != "DÁN_TOKEN_BOT_CỦA_BẠN_VÀO_ĐÂY":
        try:
            tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            tg_app.add_handler(CommandHandler("start", start_command))
            tg_app.add_handler(CommandHandler("baocao", start_command))
            
            # Chạy bot không chặn luồng chính
            loop = asyncio.get_event_loop()
            loop.create_task(tg_app.initialize())
            loop.create_task(tg_app.start())
            loop.create_task(tg_app.updater.start_polling())
            print("🤖 Telegram Bot đã khởi chạy thành công!")
        except Exception as e:
            print(f"❌ Không thể khởi chạy Telegram Bot: {e}")
    else:
        print("⚠️ Chưa cấu hình TELEGRAM_BOT_TOKEN.")

# Chạy bot khi Flask bắt đầu
setup_telegram_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
