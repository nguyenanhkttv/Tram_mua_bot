import os
import logging
import datetime
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# 1. Cấu hình nhật ký hệ thống (Logging)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# 2. Khởi tạo ứng dụng Flask & Bật CORS
app = Flask(__name__)
CORS(app)  # Cho phép file HTML ở trình duyệt bất kỳ gọi API không bị chặn

# 3. Thông tin cấu hình hệ thống
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s")

BASE_URL = "http://222.255.11.82"
LOGIN_URL = f"{BASE_URL}/Login.aspx"
REPORT_URL = f"{BASE_URL}/Modules/MuaTudong/BaoCaoChiTietDuLieu.aspx"

USERNAME = "admin"
PASSWORD = "ttdl@2021"


def get_station_data():
    """Hàm trung gian: Đăng nhập ASP.NET và bóc tách dữ liệu trạm mưa"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })

    active_list = []
    lost_1h_3h = []
    lost_over_3h = []
    now = datetime.datetime.now()

    try:
        # Bước A: Lấy ViewState từ trang đăng nhập ASP.NET
        res_get = session.get(LOGIN_URL, timeout=12)
        soup_login = BeautifulSoup(res_get.text, 'html.parser')

        viewstate = soup_login.find('input', {'id': '__VIEWSTATE'})
        eventval = soup_login.find('input', {'id': '__EVENTVALIDATION'})
        viewstategen = soup_login.find('input', {'id': '__VIEWSTATEGENERATOR'})

        login_data = {
            '__VIEWSTATE': viewstate['value'] if viewstate else '',
            '__EVENTVALIDATION': eventval['value'] if eventval else '',
            '__VIEWSTATEGENERATOR': viewstategen['value'] if viewstategen else '',
            'txtUsername': USERNAME,
            'txtPassword': PASSWORD,
            'btnLogin': 'Đăng nhập'
        }

        # Bước B: Gửi dữ liệu đăng nhập
        session.post(LOGIN_URL, data=login_data, timeout=12)

        # Bước C: Tải trang báo cáo dữ liệu chi tiết
        res_report = session.get(REPORT_URL, timeout=12)
        if res_report.status_code != 200:
            return None, "Không thể tải trang báo cáo từ máy chủ trạm mưa."

        soup_report = BeautifulSoup(res_report.text, 'html.parser')
        
        # Tìm bảng dữ liệu
        table = soup_report.find('table', {'id': lambda x: x and 'grid' in x.lower()}) or soup_report.find('table')
        if not table:
            return None, "Không tìm thấy bảng dữ liệu trạm trên trang web."

        rows = table.find_all('tr')
        # Duyệt từng dòng (bỏ qua dòng tiêu đề)
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cols) < 3:
                continue

            st_name = cols[1] if len(cols) > 1 else "Trạm không tên"
            time_str = cols[2] if len(cols) > 2 else ""

            # Thử phân tích chuỗi thời gian
            parsed_time = None
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%H:%M %d/%m/%Y"):
                try:
                    parsed_time = datetime.datetime.strptime(time_str, fmt)
                    break
                except ValueError:
                    pass

            if parsed_time:
                diff_hours = (now - parsed_time).total_seconds() / 3600.0
                time_fmt = parsed_time.strftime('%H:%M %d/%m')

                if diff_hours < 1:
                    active_list.append(f"{st_name} ({time_fmt})")
                elif 1 <= diff_hours <= 3:
                    hrs = int(diff_hours)
                    mins = int((diff_hours - hrs) * 60)
                    lost_1h_3h.append(f"{st_name} (Mất tín hiệu: {hrs}h{mins}p)")
                else:
                    lost_over_3h.append(f"{st_name} (Mất tín hiệu > 3h)")
            else:
                lost_over_3h.append(f"{st_name} (Chưa có dữ liệu thời gian)")

        result_data = {
            "status": "success",
            "updated_at": now.strftime("%H:%M:%S %d/%m/%Y"),
            "active": active_list,
            "lost_1h_3h": lost_1h_3h,
            "lost_over_3h": lost_over_3h
        }
        return result_data, None

    except Exception as e:
        return None, str(e)


# ---------------- API CHO FILE HTML ----------------
@app.route('/api/report', methods=['GET'])
def api_report():
    data, error = get_station_data()
    if error:
        return jsonify({"status": "error", "message": error}), 500
    return jsonify(data)


@app.route('/', methods=['GET'])
def home():
    return "✅ Backend Python đang chạy bình thường!"


# ---------------- XỬ LÝ TELEGRAM BOT ----------------
def build_telegram_message():
    data, error = get_station_data()
    if error:
        return f"❌ Có lỗi kết nối: {error}"

    msg = f"📊 **BÁO CÁO TRẠM MƯA TỰ ĐỘNG**\n"
    msg += f"🕒 *Cập nhật lúc:* {data['updated_at']}\n\n"

    msg += f"✅ **ĐANG HOẠT ĐỘNG ({len(data['active'])} trạm):**\n"
    msg += ("\n".join([f"• {x}" for x in data['active']]) if data['active'] else "Không có") + "\n\n"

    msg += f"⚠️ **MẤT KẾT NỐI TỪ 1H ĐẾN 3H ({len(data['lost_1h_3h'])} trạm):**\n"
    msg += ("\n".join([f"• {x}" for x in data['lost_1h_3h']]) if data['lost_1h_3h'] else "Không có") + "\n\n"

    msg += f"🚫 **MẤT KẾT NỐI TRÊN 3H ({len(data['lost_over_3h'])} trạm):**\n"
    msg += ("\n".join([f"• {x}" for x in data['lost_over_3h']]) if data['lost_over_3h'] else "Không có")

    return msg


async def handle_telegram_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mỗi khi nhận tin nhắn từ Telegram -> Tự động trả lời báo cáo"""
    await update.message.reply_text("⏳ Đang cào dữ liệu mới nhất từ hệ thống, vui lòng đợi giây lát...")
    report_text = build_telegram_message()
    await update.message.reply_text(report_text, parse_mode='Markdown')


# ---------------- CHẠY ỨNG DỤNG ----------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)