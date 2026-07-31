"""
Script tiện ích giúp Quản trị viên xóa sạch toàn bộ dữ liệu trong file CSDL deadline_bot.db khi cần làm mới hoàn toàn.
Chạy script bằng lệnh: python reset_db.py
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "deadline_bot.db")

def clear_all_data():
    if not os.path.exists(DB_PATH):
        print(f"⚠️ File CSDL `{DB_PATH}` chưa tồn tại.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Lấy danh sách tất cả các bảng hiện có
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = cursor.fetchall()

        for table in tables:
            table_name = table[0]
            cursor.execute(f"DELETE FROM {table_name};")
            print(f"  • Đã xóa toàn bộ dữ liệu bảng `{table_name}`")

        # Commit transaction xóa dữ liệu trước khi VACUUM
        conn.commit()

        # Đổi isolation_level sang None để chạy VACUUM độc lập
        conn.isolation_level = None
        cursor.execute("VACUUM;")
        conn.close()

        print(f"\n🎉 Đã xóa sạch toàn bộ dữ liệu trong file `{DB_PATH}` thành công!")
    except Exception as e:
        print(f"❌ Lỗi khi xóa CSDL: {e}")

if __name__ == "__main__":
    print("🧹 Đang thực hiện xóa sạch toàn bộ dữ liệu CSDL...")
    clear_all_data()
