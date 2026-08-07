"""
Google Drive API Helper - Quản lý tự động cấp quyền truy cập vào Folder/File Google Drive.
"""

import re
import os
import json
import time
from typing import Optional, Tuple
from config import GOOGLE_CREDENTIALS_FILE


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_TRANSIENT_REASONS = {
    "backenderror",
    "internalerror",
    "quotaexceeded",
    "ratelimitexceeded",
    "serviceunavailable",
    "sharingratelimitexceeded",
    "temporarilyunavailable",
    "userratelimitexceeded",
}
_NOTIFICATION_REASONS = {"cannotsendnotification", "invalidsharingrequest"}


def _api_error_details(error: Exception) -> tuple[Optional[int], set[str], str]:
    """Extract structured status/reasons from a Google API exception."""
    status = getattr(getattr(error, "resp", None), "status", None)
    reasons: set[str] = set()
    messages: list[str] = [str(error)]

    content = getattr(error, "content", None)
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    if content:
        messages.append(str(content))
        try:
            payload = json.loads(content)
            api_error = payload.get("error", payload) if isinstance(payload, dict) else {}
            if isinstance(api_error, dict):
                if api_error.get("message"):
                    messages.append(str(api_error["message"]))
                for item in api_error.get("errors", []) or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("reason"):
                        reasons.add(str(item["reason"]).lower())
                    if item.get("message"):
                        messages.append(str(item["message"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    return status, reasons, " ".join(messages).lower()


def _is_transient_error(
    status: Optional[int], reasons: set[str], error_text: str
) -> bool:
    if status in _TRANSIENT_STATUS_CODES or reasons.intersection(_TRANSIENT_REASONS):
        return True
    return any(
        phrase in error_text
        for phrase in (
            "backenderror",
            "quotaexceeded",
            "ratelimitexceeded",
            "sharingratelimitexceeded",
            "userratelimitexceeded",
            "rate limit exceeded",
            "timed out",
            "timeout",
            "connection reset",
            "temporarily unavailable",
            "service unavailable",
        )
    )


def is_transient_drive_error(error_text: str) -> bool:
    """Tell the assignment flow not to blacklist a link for a temporary API issue."""
    normalized = str(error_text).lower()
    return any(
        phrase in normalized
        for phrase in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "backenderror",
            "connection reset",
            "internalerror",
            "quotaexceeded",
            "ratelimitexceeded",
            "rate limit exceeded",
            "service unavailable",
            "sharingratelimitexceeded",
            "temporarily unavailable",
            "timed out",
            "timeout",
            "userratelimitexceeded",
        )
    )


def _is_notification_error(
    status: Optional[int], reasons: set[str], error_text: str
) -> bool:
    return bool(
        reasons.intersection(_NOTIFICATION_REASONS)
        or "sharingratelimitexceeded" in reasons
        or "sharingratelimitexceeded" in error_text
        or "notification" in error_text
        or (status == 400 and "bad request" in error_text)
    )


def _permission_already_exists(error_text: str) -> bool:
    """Recognize the different messages returned for an existing permission."""
    normalized = str(error_text).lower()
    return any(
        phrase in normalized
        for phrase in (
            "alreadyexists",
            "already exists",
            "already has",
            "useraccessalreadyexists",
            "permissionalreadyexists",
        )
    )


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
    """Tìm đường dẫn file credentials.json hoặc credential.json trong thư mục bot."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    possible_paths = [
        os.path.join(base_dir, GOOGLE_CREDENTIALS_FILE) if GOOGLE_CREDENTIALS_FILE else None,
        os.path.join(base_dir, "credentials.json"),
        os.path.join(base_dir, "credential.json"),
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

    max_retries = 4
    last_exception = None
    last_status: Optional[int] = None
    last_reasons: set[str] = set()
    last_error_text = ""

    # Try notification first. If Google rejects the notification or its
    # sharing quota, retry the same permission without sending an email.
    notification_modes = [bool(send_notification)]
    if send_notification:
        notification_modes.append(False)

    for notification_mode in notification_modes:
        for attempt in range(max_retries):
            try:
                service.permissions().create(
                    fileId=drive_id,
                    body=permission_body,
                    sendNotificationEmail=notification_mode,
                    supportsAllDrives=True,
                    fields="id",
                ).execute()

                if notification_mode:
                    return True, f"Đã cấp quyền **{role}** cho email `{target_email}`"
                return True, (
                    f"Đã cấp quyền **{role}** cho email `{target_email}` "
                    "(Không gửi mail thông báo tự động)"
                )

            except Exception as error:
                last_exception = error
                status, reasons, error_text = _api_error_details(error)
                last_status = status
                last_reasons = reasons
                last_error_text = error_text

                if _permission_already_exists(error_text):
                    return True, f"Email `{target_email}` đã có quyền truy cập từ trước"

                # Move immediately to no-notification mode for email/quota
                # failures; retrying the same notification cannot help.
                if notification_mode and _is_notification_error(status, reasons, error_text):
                    break

                if _is_transient_error(status, reasons, error_text) and attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                break

        if not send_notification or notification_mode is False:
            break

    if (
        "you do not have permission to share" in last_error_text
        or "insufficientfilepermissions" in last_reasons
        or "insufficientfilepermissions" in last_error_text
        or "does not have sufficient permissions" in last_error_text
    ):
        return False, (
            "⚠️ Google Drive từ chối quyền chia sẻ thư mục này. "
            "Hãy kiểm tra service account của bot có quyền Editor trên đúng "
            "thư mục/link này và tùy chọn cho phép Editor thay đổi quyền chia sẻ."
        )

    if (
        "filenotfound" in last_reasons
        or "filenotfound" in last_error_text
        or "file not found" in last_error_text
    ):
        return False, (
            f"Không tìm thấy Folder/File Drive (ID: `{drive_id}`). "
            "Hãy kiểm tra link hoặc quyền truy cập của Bot."
        )

    # A create request can time out or return a 400 after Google has already
    # applied the permission. Verify the actual permission before reporting a
    # failure, otherwise the deadline transaction is rolled back incorrectly.
    verified, _, _ = check_drive_permission(drive_url, target_email)
    if verified:
        return True, f"Đã xác minh quyền **{role}** cho email `{target_email}` sau khi Google trả lỗi tạm thời"

    status_label = f"HTTP {last_status}" if last_status else "Google API"
    reason_label = f" [{', '.join(sorted(last_reasons))}]" if last_reasons else ""
    return False, f"Lỗi {status_label}{reason_label}: {last_exception or last_error_text}"


def check_drive_permission(
    drive_url: str,
    email: str,
) -> Tuple[bool, str, Optional[int]]:
    """Verify that an email currently has usable access to a Drive item."""
    if not email:
        return False, "Thiếu địa chỉ email", None

    target_email = email.strip().lower()
    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        return False, f"Không thể trích xuất ID từ Google Drive URL: `{drive_url}`", None

    service, err_msg = get_drive_service()
    if not service:
        return False, f"Chưa cấu hình Google Service Account ({err_msg})", None

    try:
        page_token = None
        accepted_roles = {"owner", "writer", "organizer", "fileOrganizer"}
        while True:
            request = service.permissions().list(
                fileId=drive_id,
                supportsAllDrives=True,
                fields="nextPageToken,permissions(type,emailAddress,role)",
                pageToken=page_token,
            )
            response = request.execute()
            for permission in response.get("permissions", []):
                if (
                    permission.get("type") == "user"
                    and permission.get("emailAddress", "").strip().lower() == target_email
                ):
                    role = permission.get("role", "")
                    if role in accepted_roles:
                        return True, f"Email `{target_email}` có quyền `{role}`", None
                    return False, f"Email `{target_email}` chỉ có quyền `{role}`", None

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return False, f"Không tìm thấy quyền Drive của email `{target_email}`", None
    except Exception as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        status_label = f"Google API {status}" if status else "Google API"
        return False, f"{status_label}: {e}", status


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
        ).execute()

        return True, f"Đã thu hồi quyền Drive của email `{target_email}`"
    except Exception as e:
        error_str = str(e).lower()
        if "insufficientfilepermissions" in error_str or "does not have sufficient permissions" in error_str:
            return False, f"Bot không có quyền Editor trên Folder/File Drive để thu hồi."
        return False, f"Lỗi Google API khi thu hồi quyền: {e}"
