import math
from datetime import datetime, timedelta
from config import ROLE_TYPES

def calculate_deadline(role_type: str, chap_count: int) -> datetime:
    """Tính hạn nộp deadline.
    - Role có days_per_chap < 1 (2 chap/ngày): làm tròn LÊN
    - Role khác: nhân thẳng
    """
    config = ROLE_TYPES[role_type]
    if config['days_per_chap'] < 1:
        total_days = math.ceil(chap_count * config['days_per_chap'])
    else:
        total_days = chap_count * config['days_per_chap']
    return datetime.now() + timedelta(days=total_days)

def format_deadline(dt: datetime) -> str:
    """Format datetime thành chuỗi dd/mm/yyyy HH:MM"""
    return dt.strftime('%d/%m/%Y %H:%M')

def format_remaining(deadline_at: datetime) -> str:
    """Format thời gian còn lại dạng tiếng Việt."""
    if isinstance(deadline_at, str):
        deadline_at = datetime.fromisoformat(deadline_at)
    remaining = deadline_at - datetime.now()
    if remaining.total_seconds() <= 0:
        return '⚠️ Đã quá hạn!'
    days = remaining.days
    hours = remaining.seconds // 3600
    if days > 0:
        return f'{days} ngày {hours} giờ'
    if hours > 0:
        return f'{hours} giờ'
    minutes = remaining.seconds // 60
    return f'{minutes} phút'

def get_deadline_status_emoji(deadline_at: datetime) -> str:
    """Trả về emoji dựa trên thời gian còn lại."""
    if isinstance(deadline_at, str):
        deadline_at = datetime.fromisoformat(deadline_at)
    remaining = deadline_at - datetime.now()
    if remaining.total_seconds() <= 0:
        return '🔴'
    elif remaining.total_seconds() <= 6 * 3600:
        return '🟡'
    else:
        return '🟢'

def calculate_total_days(role_type: str, chap_count: int) -> int:
    """Tính tổng số ngày (đã làm tròn) - dùng cho hiển thị."""
    config = ROLE_TYPES[role_type]
    if config['days_per_chap'] < 1:
        return math.ceil(chap_count * config['days_per_chap'])
    else:
        return int(chap_count * config['days_per_chap'])
