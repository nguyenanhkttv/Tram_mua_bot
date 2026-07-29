import datetime
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- THÔNG TIN CẤU HÌNH ---
TELEGRAM_BOT_TOKEN = "8587075816:AAHlm9r7mwCjEQlgmx6KjoZ8AE7Vd844x6s"

LOGIN_URL = "http://222.255.11.82/Login.aspx"  # URL trang đăng nhập thực tế
DATA_URL = "http://222.255.11.82/Modules/MuaTudong/BaoCaoChiTietDuLieu.aspx"

USERNAME = "admin"
PASSWORD = "ttdl@2021"


def get_station_status():
    """Hàm đăng nhập và lấy trạng thái các trạm từ hệ thống"""
    session = requests.Session()
    
    # 1. Đăng nhập hệ thống (Lưu ý điều chỉnh payload theo form thực tế)
    login_payload = {
        'username': USERNAME,
        'password': PASSWORD
    }
    try:
        session.post(LOGIN_URL, data=login_payload, timeout=10)
        
        # 2. Truy cập trang báo cáo dữ liệu
        response = session.get(DATA_URL, timeout=10)
        if response.status_code != 200:
            return "❌ Không thể truy cập dữ liệu hệ thống trạm."
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 3. Phân tích bảng dữ liệu (Giả định bảng có id="gridData" hoặc class tương ứng)
        # Bổ sung logic trích xuất dữ liệu thực tế từ HTML tại đây
        rows = soup.find_all('tr') 
        
        active_stations = []
        off_1h_to_3h = []
        off_over_3h = []
        
        now = datetime.datetime.now()

        # Ví dụ mô phỏng duyệt qua danh sách trạm từ dữ liệu web
        # Bạn thay thế đoạn mô phỏng này bằng dữ liệu bóc tách được từ `soup`
        sample_stations = [
            {"name": "Trạm Cẩm Thủy", "last_time": now - datetime.timedelta(minutes=20)},
            {"name": "Trạm Thạch Thành", "last_time": now - datetime.timedelta(hours=1, minutes=45)},
            {"name": "Trạm Tĩnh Gia", "last_time": now - datetime.timedelta(hours=4)},
        ]

        for st in sample_stations:
            diff_hours = (now - st["last_time"]).total_seconds() / 3600
            time_str = st["last_time"].strftime("%H:%M %d/%m")
            
            if diff_hours < 1:
                active_stations.append(f"• {st['name']} ({time_str})")
            elif 1 <= diff_hours <= 3:
                off_1h_to_3h.append(f"• {st['name']} (Mất kết nối: {int(diff_hours)}h{(int(diff_hours*60)%60)}p)")
            else:
                off_over_3h.append(f"• {st['name']} (Mất kết nối > 3h)")

        # 4. Định dạng tin nhắn báo cáo
        report = f"📊 **BÁO CÁO TRẠM MƯA TỰ ĐỘNG**\n"
        report += f"🕒 *Thời gian xuất báo cáo:* {now.strftime('%H:%M:%S %d/%m/%Y')}\n\n"
        
        report += f"✅ **ĐANG HOẠT ĐỘNG ({len(active_stations)} trạm):**\n"
        report += "\n".join(active_stations) if active_stations else "Không có"
        report += "\n\n"
        
        report += f"⚠️ **MẤT TÍN HIỆU TỪ 1H ĐẾN 3H ({len(off_1h_to_3h)} trạm):**\n"
        report += "\n".join(off_1h_to_3h) if off_1h_to_3h else "Không có"
        report += "\n\n"
        
        report += f"🚫 **MẤT TÍN HIỆU TRÊN 3H ({len(off_over_3h)} trạm):**\n"
        report += "\n".join(off_over_3h) if off_over_3h else "Không có"

        return report

    except Exception as e:
        return f"⚠️ Có lỗi xảy ra khi kết nối máy chủ: {str(e)}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hàm xử lý phản hồi mỗi khi có tin nhắn gửi tới Bot"""
    await update.message.reply_text("🔄 Đang lấy dữ liệu trạm từ hệ thống, vui lòng đợi giây lát...")
    report_text = get_station_status()
    await update.message.reply_text(report_text, parse_mode='Markdown')


if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Nhận mọi tin nhắn văn bản gửi tới bot
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CommandHandler("start", handle_message))
    
    print("Bot Telegram đang chạy...")
    app.run_polling()
