"""
Google Drive API Helper - Quản lý tự động cấp quyền truy cập vào Folder/File Google Drive.
"""

import re
import os
from typing import Optional, Tuple
from config import GOOGLE_CREDENTIALS_FILE


def extract_drive_id(url: str) -> Optional[str]:
    """
    Trích xuất ID của Folder hoặc File từ Google Drive URL.
    Hỗ trợ các định dạng URL phổ biến:
    - https://drive.google.com/drive/folders/ID
    - https://drive.google.com/file/d/ID/view
    - https://drive.google.com/open?id=ID
    """
    if not url:
        return None

    # Folder pattern: /folders/ID
    folder_match = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    if folder_match:
        return folder_match.group(1)

    # File pattern: /file/d/ID
    file_match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if file_match:
        return file_match.group(1)

    # Query param pattern: ?id=ID
    id_param_match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if id_param_match:
        return id_param_match.group(1)

    return None


import json


def find_credentials_file() -> Optional[str]:
    """Tìm đường dẫn file credentials.json hoặc credential.json trong thư mục."""
    possible_paths = [
        GOOGLE_CREDENTIALS_FILE,
        "credentials.json",
        "credential.json"
    ]
    for path in possible_paths:
        if path and os.path.exists(path):
            return path
    return None


def get_drive_service() -> Tuple[Optional[object], Optional[str]]:
    """Tạo kết nối đến Google Drive API v3 sử dụng Service Account credentials."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:
        return None, f"Thiếu thư viện Python (`pip install google-api-python-client google-auth`): {e}"

    scopes = ['https://www.googleapis.com/auth/drive']

    # 1. Thử đọc credentials từ biến môi trường GOOGLE_CREDENTIALS_JSON (Cho Hosting/Production)
    env_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if env_json:
        try:
            info = json.loads(env_json)
            creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
            service = build('drive', 'v3', credentials=creds)
            return service, None
        except Exception as e:
            print(f"[GoogleDriveError] Lỗi parse GOOGLE_CREDENTIALS_JSON từ biến môi trường: {e}")
            return None, f"Lỗi parse chuỗi JSON trong biến môi trường GOOGLE_CREDENTIALS_JSON: {e}"

    # 2. Thử đọc file credentials.json từ ổ đĩa (Cho Local)
    creds_path = find_credentials_file()
    if not creds_path:
        return None, "Chưa cấu hình `GOOGLE_CREDENTIALS_JSON` trong biến môi trường và không tìm thấy file `credentials.json` trong thư mục bot"

    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=scopes
        )
        service = build('drive', 'v3', credentials=creds)
        return service, None
    except Exception as e:
        print(f"[GoogleDriveError] Lỗi kết nối Google Drive Service Account: {e}")
        return None, f"Lỗi đọc file khóa `{creds_path}`: {e}"



def grant_drive_permission(
    drive_url: str,
    email: str,
    role: str = "writer",
    send_notification: bool = True,
) -> Tuple[bool, str]:
    """
    Thêm email vào danh sách truy cập của Google Drive Folder / File.
    
    :param drive_url: Đường dẫn Google Drive.
    :param email: Địa chỉ email của người nhận.
    :param role: Mức quyền ('writer', 'reader', 'commenter').
    :param send_notification: True để Google tự gửi email thông báo.
    :return: (thành_công: bool, thông_báo: str)
    """
    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        return False, "Không thể trích xuất ID từ Google Drive URL"

    service, err_msg = get_drive_service()
    if not service:
        return False, f"Chưa cấu hình Google Service Account ({err_msg})"

    try:
        permission_body = {
            'type': 'user',
            'role': role,
            'emailAddress': email,
        }
        service.permissions().create(
            fileId=drive_id,
            body=permission_body,
            sendNotificationEmail=send_notification,
            fields='id',
        ).execute()

        return True, f"Đã cấp quyền **{role}** cho email `{email}`"
    except Exception as e:
        error_msg = str(e)
        if "alreadyExists" in error_msg or "already has" in error_msg:
            return True, f"Email `{email}` đã có quyền truy cập từ trước"
        return False, f"Lỗi Google API: {e}"

