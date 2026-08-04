"""
Google Drive API Helper - Quản lý tự động cấp quyền truy cập vào Folder/File Google Drive.
"""

import re
import os
import json
from typing import Optional, Tuple
from config import GOOGLE_CREDENTIALS_FILE


import re
import os
import json
import time
from typing import Optional, Tuple
from config import GOOGLE_CREDENTIALS_FILE


def extract_drive_id(url: str) -> Optional[str]:
    """
    Trích xuất ID của Folder hoặc File từ Google Drive URL hoặc Bare ID.
    Hỗ trợ các định dạng URL phổ biến:
    - https://drive.google.com/drive/folders/ID
    - https://drive.google.com/file/d/ID/view
    - https://docs.google.com/document/d/ID/edit
    - https://docs.google.com/spreadsheets/d/ID/edit
    - https://docs.google.com/presentation/d/ID/edit
    - https://drive.google.com/open?id=ID
    - Direct Bare ID: ID string (ví dụ: 1aB2c3d4...)
    """
    if not url:
        return None

    clean_url = url.strip()

    # Folder pattern: /folders/ID
    folder_match = re.search(r'/folders/([a-zA-Z0-9_-]+)', clean_url)
    if folder_match:
        return folder_match.group(1)

    # File / Docs / Sheets / Slides pattern: /(file|document|spreadsheets|presentation)/d/ID
    d_match = re.search(r'/(?:file|document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]+)', clean_url)
    if d_match:
        return d_match.group(1)

    # Generic /d/ID
    generic_d_match = re.search(r'/d/([a-zA-Z0-9_-]+)', clean_url)
    if generic_d_match:
        return generic_d_match.group(1)

    # Query param pattern: ?id=ID hoặc &id=ID
    id_param_match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', clean_url)
    if id_param_match:
        return id_param_match.group(1)

    # Bare ID check: chuỗi chữ cái/số/gạch nối, dài từ 20 đến 100 ký tự
    if re.match(r'^[a-zA-Z0-9_-]{20,100}$', clean_url):
        return clean_url

    return None


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
    Hỗ trợ Shared Drive (supportsAllDrives=True), retry tự động và fallback notification.
    
    :param drive_url: Đường dẫn Google Drive.
    :param email: Địa chỉ email của người nhận.
    :param role: Mức quyền ('writer', 'reader', 'commenter').
    :param send_notification: True để Google tự gửi email thông báo.
    :return: (thành_công: bool, thông_báo: str)
    """
    if not email:
        return False, "Thiếu địa chỉ email"

    target_email = email.strip().lower()
    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        return False, f"Không thể trích xuất ID từ Google Drive URL: `{drive_url}`"

    service, err_msg = get_drive_service()
    if not service:
        return False, f"Chưa cấu hình Google Service Account ({err_msg})"

    permission_body = {
        'type': 'user',
        'role': role,
        'emailAddress': target_email,
    }

    max_retries = 3
    last_exception = None

    for attempt in range(max_retries):
        try:
            service.permissions().create(
                fileId=drive_id,
                body=permission_body,
                sendNotificationEmail=send_notification,
                supportsAllDrives=True,
                supportsTeamDrives=True,
                fields='id',
            ).execute()

            return True, f"Đã cấp quyền **{role}** cho email `{target_email}`"

        except Exception as e:
            last_exception = e
            error_str = str(e).lower()

            # 1. Kiểm tra nếu email đã có quyền từ trước
            if "alreadyexists" in error_str or "already has" in error_str or "useraccessalreadyexists" in error_str:
                return True, f"Email `{target_email}` đã có quyền truy cập từ trước"

            # 2. Nếu lỗi do gửi email notification (invalidSharingRequest / cannotSendNotification), fallback sang sendNotificationEmail=False
            if send_notification and ("notification" in error_str or "invalidsharingrequest" in error_str or "cannot share" in error_str):
                try:
                    service.permissions().create(
                        fileId=drive_id,
                        body=permission_body,
                        sendNotificationEmail=False,
                        supportsAllDrives=True,
                        supportsTeamDrives=True,
                        fields='id',
                    ).execute()
                    return True, f"Đã cấp quyền **{role}** cho email `{target_email}` (Không gửi mail thông báo tự động)"
                except Exception as fallback_err:
                    last_exception = fallback_err
                    error_str = str(fallback_err).lower()
                    if "alreadyexists" in error_str or "already has" in error_str or "useraccessalreadyexists" in error_str:
                        return True, f"Email `{target_email}` đã có quyền truy cập từ trước"

            # 3. Phân tích lỗi thiếu quyền chia sẻ (Cấu hình "Editors can change permissions and share" bị tắt trên Drive)
            if "you do not have permission to share" in error_str:
                return False, (
                    f"⚠️ **Google Drive chặn chia sẻ thư mục này!**\n"
                    f"👉 **Nguyên nhân**: Thư mục Drive này đang tắt tùy chọn cho phép Người chỉnh sửa (Editor) chia sẻ.\n"
                    f"👉 **Cách khắc phục cho Admin**:\n"
                    f" 1. Mở Thư mục này trên Google Drive > Nút **Chia sẻ (Share)**.\n"
                    f" 2. Bấm icon **Bánh răng ⚙️** (Góc trên bên phải cửa sổ chia sẻ).\n"
                    f" 3. Tích chọn ✅ **'Người chỉnh sửa có thể thay đổi quyền và chia sẻ'** (*Editors can change permissions and share*)."
                )

            # 4. Phân tích lỗi không có quyền chỉnh sửa / thiếu quyền Admin Drive
            if "insufficientfilepermissions" in error_str or "does not have sufficient permissions" in error_str:
                return False, f"Bot không có quyền Editor trên Folder/File Drive này (Hãy đảm bảo email của bot đã được add quyền Editor vào thư mục gốc)."

            if "filenotfound" in error_str or "file not found" in error_str:
                return False, f"Không tìm thấy Folder/File Drive (ID: `{drive_id}`). Hãy kiểm tra link hoặc quyền truy cập của Bot."

            # 5. Nếu là lỗi mạng transient (5xx, rateLimitExceeded), chờ rồi retry
            if attempt < max_retries - 1 and any(k in error_str for k in ["500", "503", "ratelimitexceeded", "backenderror", "userratelimitexceeded"]):
                time.sleep(1 * (attempt + 1))
                continue

            break

    return False, f"Lỗi Google API: {last_exception}"


def revoke_drive_permission(
    drive_url: str,
    email: str,
) -> Tuple[bool, str]:
    """
    Thu hồi (xóa) quyền truy cập của email khỏi Google Drive Folder / File.
    Hỗ trợ Shared Drive (supportsAllDrives=True).
    
    :param drive_url: Đường dẫn Google Drive.
    :param email: Địa chỉ email cần thu hồi quyền.
    :return: (thành_công: bool, thông_báo: str)
    """
    if not email:
        return False, "Thiếu địa chỉ email"

    target_email = email.strip().lower()
    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        return False, f"Không thể trích xuất ID từ Google Drive URL: `{drive_url}`"

    service, err_msg = get_drive_service()
    if not service:
        return False, f"Chưa cấu hình Google Service Account ({err_msg})"

    try:
        # Lấy danh sách permissions của file/folder với supportsAllDrives=True
        perm_list = service.permissions().list(
            fileId=drive_id,
            supportsAllDrives=True,
            supportsTeamDrives=True,
            fields="permissions(id, emailAddress)"
        ).execute()

        permissions = perm_list.get("permissions", [])
        permission_id = None

        for p in permissions:
            if p.get("emailAddress", "").lower() == target_email:
                permission_id = p.get("id")
                break

        if not permission_id:
            return True, f"Email `{target_email}` không còn nằm trong danh sách quyền truy cập"

        # Xóa quyền truy cập với supportsAllDrives=True
        service.permissions().delete(
            fileId=drive_id,
            permissionId=permission_id,
            supportsAllDrives=True,
            supportsTeamDrives=True,
        ).execute()

        return True, f"Đã thu hồi quyền Drive của email `{target_email}`"
    except Exception as e:
        error_str = str(e).lower()
        if "insufficientfilepermissions" in error_str or "does not have sufficient permissions" in error_str:
            return False, f"Bot không có quyền Editor trên Folder/File Drive để thu hồi."
        return False, f"Lỗi Google API khi thu hồi quyền: {e}"


