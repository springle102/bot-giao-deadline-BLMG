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
_LINK_FAILURE_REASONS = {
    "cannotinvitenongoogleuser",
    "cannotshareacrossdomains",
    "domainpolicy",
    "filenotfound",
    "insufficientfilepermissions",
    "teamdrivemembershiprequired",
}
_TRANSIENT_FRIENDLY_MARKERS = (
    "đang giới hạn",
    "tạm thời gặp lỗi",
    "vui lòng thử lại sau",
)


class DriveErrorMessage(str):
    """User-safe error text carrying structured Drive failure metadata."""

    def __new__(
        cls,
        value: str,
        *,
        status: Optional[int] = None,
        reasons: Optional[set[str]] = None,
        transient: bool = False,
    ):
        instance = super().__new__(cls, value)
        instance.status = status
        instance.reasons = frozenset(reasons or set())
        instance.transient = transient
        return instance


def _api_error_details(error: object) -> tuple[Optional[int], set[str], str]:
    """Extract structured status/reasons from a Google API exception."""
    raw_status = getattr(getattr(error, "resp", None), "status", None)
    try:
        status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status = None
    if status is None:
        status_match = re.search(
            r"(?:http(?:error)?|google\s+api|status|code)\D{0,16}([45]\d{2})\b",
            str(error),
            flags=re.IGNORECASE,
        )
        if status_match:
            status = int(status_match.group(1))
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


def is_transient_drive_error(error_text: object) -> bool:
    """Tell the assignment flow not to blacklist a link for a temporary API issue."""
    if bool(getattr(error_text, "transient", False)):
        return True

    status, reasons, parsed_error_text = _api_error_details(error_text)
    if _is_transient_error(status, reasons, parsed_error_text):
        return True

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
    ) or any(marker in normalized for marker in _TRANSIENT_FRIENDLY_MARKERS)


def should_block_drive_link(error_text: object) -> bool:
    """Return whether a failure is deterministic enough to block a Drive ID.

    Operational failures (quota, timeout, credentials/network outages) must not
    make every chapter using the same Drive item disappear from selection.
    """
    if is_transient_drive_error(error_text):
        return False

    status, reasons, normalized = _api_error_details(error_text)
    if reasons.intersection(_LINK_FAILURE_REASONS):
        return True
    return status in {400, 403, 404}


def _role_satisfies(actual_role: str, required_role: str) -> bool:
    """Check whether a current Drive role fulfils the requested role."""
    accepted_roles = {
        "reader": {"owner", "reader", "commenter", "writer", "organizer"},
        "commenter": {"owner", "commenter", "writer", "organizer"},
        # fileOrganizer can organize Shared Drive content but is not a
        # reliable equivalent of Editor/writer for a file.
        "writer": {"owner", "writer", "organizer"},
    }
    return actual_role in accepted_roles.get(required_role, {required_role})


def _find_user_permission(service: object, drive_id: str, target_email: str) -> Optional[dict]:
    """Find a direct/inherited user permission, following list pagination."""
    page_token = None
    while True:
        request = service.permissions().list(
            fileId=drive_id,
            supportsAllDrives=True,
            fields="nextPageToken,permissions(id,type,emailAddress,role)",
            pageToken=page_token,
        )
        response = request.execute()
        for permission in response.get("permissions", []) or []:
            if (
                permission.get("type") == "user"
                and permission.get("emailAddress", "").strip().lower() == target_email
            ):
                return permission

        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def _verify_drive_permission_with_retry(
    drive_url: str,
    email: str,
    required_role: str = "writer",
) -> tuple[bool, str, Optional[int]]:
    """Poll permission state after an ambiguous write or update response."""
    last_result: tuple[bool, str, Optional[int]] = (
        False,
        "Ch\\u01b0a x\\u00e1c minh \\u0111\\u01b0\\u1ee3c quy\\u1ec1n Drive",
        None,
    )
    for delay in (0, 1, 2, 4):
        if delay:
            time.sleep(delay)

        last_result = check_drive_permission(
            drive_url,
            email,
            required_role=required_role,
        )
        ok, _message, status = last_result
        if ok:
            return last_result

        # A missing permission (status=None) may be eventual consistency after
        # create/update, so keep polling. A definitive non-transient API error
        # should return immediately.
        if status is not None and not is_transient_drive_error(last_result[1]):
            return last_result

    return last_result


def _handle_existing_permission(
    service: object,
    drive_id: str,
    drive_url: str,
    target_email: str,
    required_role: str,
) -> Optional[tuple[bool, str]]:
    """Verify or upgrade a permission when Google says it already exists."""
    try:
        permission = _find_user_permission(service, drive_id, target_email)
    except Exception as error:
        if is_transient_drive_error(error):
            return None
        return False, friendly_drive_error(
            error,
            email=target_email,
            drive_url=drive_url,
        )

    if not permission:
        return None

    current_role = str(permission.get("role") or "")
    if _role_satisfies(current_role, required_role):
        return True, (
            f"Email `{target_email}` \u0111\u00e3 c\u00f3 quy\u1ec1n "
            f"**{current_role}** t\u1eeb tr\u01b0\u1edbc"
        )

    permission_id = permission.get("id")
    if not permission_id:
        return False, "Google Drive kh\u00f4ng tr\u1ea3 v\u1ec1 ID permission \u0111\u1ec3 n\u00e2ng quy\u1ec1n."

    try:
        service.permissions().update(
            fileId=drive_id,
            permissionId=permission_id,
            body={"role": required_role},
            supportsAllDrives=True,
            fields="id,role",
        ).execute()
    except Exception as error:
        if is_transient_drive_error(error):
            verified, _verify_message, _ = _verify_drive_permission_with_retry(
                drive_url,
                target_email,
                required_role=required_role,
            )
            if verified:
                return True, (
                    f"Email `{target_email}` \u0111\u00e3 \u0111\u01b0\u1ee3c n\u00e2ng "
                    f"quy\u1ec1n **{required_role}**"
                )
        return False, friendly_drive_error(
            error,
            email=target_email,
            drive_url=drive_url,
        )

    verified, verify_message, _ = _verify_drive_permission_with_retry(
        drive_url,
        target_email,
        required_role=required_role,
    )
    if verified:
        return True, (
            f"Email `{target_email}` \u0111\u00e3 \u0111\u01b0\u1ee3c n\u00e2ng "
            f"quy\u1ec1n **{required_role}**"
        )
    return False, verify_message


def _friendly_drive_error(
    status: Optional[int],
    reasons: set[str],
    error_text: str,
    email: Optional[str] = None,
    drive_id: Optional[str] = None,
) -> str:
    """Convert raw Google API details into a short user-facing message."""
    normalized = str(error_text).lower()

    if "cannotinvitenongoogleuser" in reasons or "cannotinvitenongoogleuser" in normalized:
        target = f" `{email}`" if email else " này"
        return f"Email người nhận{target} chưa có tài khoản Google."

    if (
        "insufficientfilepermissions" in reasons
        or "insufficientfilepermissions" in normalized
        or "does not have sufficient permissions" in normalized
        or "you do not have permission to share" in normalized
    ):
        return "Bot không có quyền Editor trên thư mục Drive này."

    if "teamdrivemembershiprequired" in reasons or "teamdrivemembershiprequired" in normalized:
        return "Bot chưa là thành viên của Shared Drive hoặc thiếu quyền phù hợp."

    if "filenotfound" in reasons or "filenotfound" in normalized or "file not found" in normalized:
        return "Link Google Drive không tồn tại hoặc bot không truy cập được."

    if (
        "domainpolicy" in reasons
        or "cannotshareacrossdomains" in reasons
        or "domainpolicy" in normalized
        or "cannotshareacrossdomains" in normalized
    ):
        return "Chính sách Google Workspace đang chặn chia sẻ email này."

    if _is_transient_error(status, reasons, normalized):
        return "Google Drive đang giới hạn hoặc tạm thời gặp lỗi. Vui lòng thử lại sau."

    if status == 401 or "unauthorized" in normalized:
        return "Thông tin xác thực Google của bot không hợp lệ hoặc đã hết hạn."

    if status == 400:
        return "Yêu cầu chia sẻ Google Drive không hợp lệ."

    if status == 403:
        return "Google Drive từ chối thao tác chia sẻ quyền."

    if drive_id:
        return "Google Drive không thể cấp quyền cho link này."
    return "Google Drive không thể cấp quyền lúc này."


def friendly_drive_error(
    error: object,
    email: Optional[str] = None,
    drive_url: Optional[str] = None,
) -> str:
    """Return a short safe message while keeping raw API details out of Discord."""
    status, reasons, error_text = _api_error_details(error)
    drive_id = extract_drive_id(drive_url) if drive_url else None
    return DriveErrorMessage(
        _friendly_drive_error(status, reasons, error_text, email, drive_id),
        status=status,
        reasons=reasons,
        transient=_is_transient_error(status, reasons, error_text),
    )


def clean_drive_error_message(
    message: object,
    email: Optional[str] = None,
    drive_url: Optional[str] = None,
) -> str:
    """Keep already-friendly messages and sanitize raw API messages."""
    if isinstance(message, DriveErrorMessage):
        return message

    raw_message = str(message).strip()
    if not raw_message:
        return "Google Drive không thể cấp quyền lúc này."

    normalized = raw_message.lower()
    api_markers = (
        "httperror",
        "http ",
        "google api",
        "googleapis.com",
        "bad request",
        "forbidden",
        "quota",
        "ratelimit",
        "cannotinvite",
        "permission denied",
        "file not found",
    )
    if not any(marker in normalized for marker in api_markers):
        return raw_message
    return friendly_drive_error(raw_message, email=email, drive_url=drive_url)


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
        print(f"[GoogleDriveError] Link không hợp lệ: {drive_url}")
        return False, "Link Google Drive không hợp lệ."

    service, err_msg = get_drive_service()
    if not service:
        print(f"[GoogleDriveError] Không khởi tạo được service: {err_msg}")
        return False, "Bot chưa kết nối được Google Drive. Admin cần kiểm tra credentials."

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
                    existing_result = _handle_existing_permission(
                        service,
                        drive_id,
                        drive_url,
                        target_email,
                        role,
                    )
                    if existing_result is not None:
                        return existing_result

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
        return False, friendly_drive_error(
            last_exception or last_error_text,
            email=target_email,
            drive_url=drive_url,
        )

    if (
        "filenotfound" in last_reasons
        or "filenotfound" in last_error_text
        or "file not found" in last_error_text
    ):
        return False, friendly_drive_error(
            last_exception or last_error_text,
            email=target_email,
            drive_url=drive_url,
        )

    # A create request can time out or return a 400 after Google has already
    # applied the permission. Verify the actual permission before reporting a
    # failure, otherwise the deadline transaction is rolled back incorrectly.
    verified, _, _ = _verify_drive_permission_with_retry(
        drive_url,
        target_email,
        required_role=role,
    )
    if verified:
        return True, f"Đã xác minh quyền **{role}** cho email `{target_email}` sau khi Google trả lỗi tạm thời"

    print(
        f"[GoogleDriveError] grant failed; status={last_status}; "
        f"reasons={sorted(last_reasons)}; error={last_exception or last_error_text}"
    )
    return False, friendly_drive_error(
        last_exception or last_error_text,
        email=target_email,
        drive_url=drive_url,
    )


def check_drive_permission(
    drive_url: str,
    email: str,
    required_role: str = "writer",
) -> Tuple[bool, str, Optional[int]]:
    """Verify that an email currently has usable access to a Drive item."""
    if not email:
        return False, "Thiếu địa chỉ email", None

    target_email = email.strip().lower()
    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        print(f"[GoogleDriveError] Link không hợp lệ khi kiểm tra quyền: {drive_url}")
        return False, "Link Google Drive không hợp lệ.", None

    service, err_msg = get_drive_service()
    if not service:
        print(f"[GoogleDriveError] Không khởi tạo được service khi kiểm tra quyền: {err_msg}")
        return False, "Bot chưa kết nối được Google Drive. Admin cần kiểm tra credentials.", None

    try:
        permission = _find_user_permission(service, drive_id, target_email)
        if not permission:
            return False, f"Không tìm thấy quyền Drive của email `{target_email}`", None

        actual_role = str(permission.get("role") or "")
        if _role_satisfies(actual_role, required_role):
            return True, f"Email `{target_email}` có quyền `{actual_role}`", None
        return False, f"Email `{target_email}` chỉ có quyền `{actual_role}`", None
    except Exception as e:
        status, reasons, error_text = _api_error_details(e)
        print(
            f"[GoogleDriveError] check permission failed; status={status}; "
            f"reasons={sorted(reasons)}; error={e}"
        )
        return False, _friendly_drive_error(
            status, reasons, error_text, target_email, drive_id
        ), status


def revoke_drive_permission(
    drive_url: str,
    email: str,
    _retry_attempt: int = 0,
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
        print(f"[GoogleDriveError] Link không hợp lệ khi thu hồi quyền: {drive_url}")
        return False, "Link Google Drive không hợp lệ."

    service, err_msg = get_drive_service()
    if not service:
        print(f"[GoogleDriveError] Không khởi tạo được service khi thu hồi quyền: {err_msg}")
        return False, "Bot chưa kết nối được Google Drive. Admin cần kiểm tra credentials."

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
        status, reasons, error_text = _api_error_details(e)
        if _is_transient_error(status, reasons, error_text) and _retry_attempt < 3:
            time.sleep(min(2 ** _retry_attempt, 8))
            return revoke_drive_permission(
                drive_url,
                email,
                _retry_attempt=_retry_attempt + 1,
            )
        print(
            f"[GoogleDriveError] revoke permission failed; status={status}; "
            f"reasons={sorted(reasons)}; error={e}"
        )
        return False, _friendly_drive_error(
            status, reasons, error_text, target_email, drive_id
        )
