"""
Utility functions cho việc parse và hiển thị chapter (bao gồm ngoại truyện).

Quy ước:
- Chap thường: chapter_number > 0, chapter_name = "Chap X"
- Ngoại truyện: chapter_number < 0, chapter_name = "Ngoại truyện X"
  Cú pháp nhập: NT1, NT2, NT3 (liền kề, không cách)
  Lưu DB: NT1 → chapter_number = -1, NT2 → -2, v.v.
"""

import re
from typing import Optional, Tuple


def parse_chapter_input(chap_str: str) -> Optional[Tuple[int, str]]:
    """
    Parse chuỗi chap input từ người dùng.

    Args:
        chap_str: Chuỗi nhập vào, ví dụ "10", "NT1", "nt2"

    Returns:
        Tuple (chapter_number, chapter_name) hoặc None nếu không hợp lệ.
        - "10"  → (10, "Chap 10")
        - "NT1" → (-1, "Ngoại truyện 1")
    """
    text = chap_str.strip()
    if not text:
        return None

    # Check ngoại truyện pattern: NT[số] (case-insensitive, không cách)
    nt_match = re.match(r'^[Nn][Tt](\d+)$', text)
    if nt_match:
        num = int(nt_match.group(1))
        if num <= 0:
            return None
        return (-num, f"Ngoại truyện {num}")

    # Check số chap thường
    if text.isdigit():
        num = int(text)
        return (num, f"Chap {num}")

    return None


def chapter_number_to_display(chapter_number: int) -> str:
    """
    Chuyển chapter_number thành chuỗi hiển thị.

    Args:
        chapter_number: Số chap trong DB (âm = ngoại truyện, dương = chap thường)

    Returns:
        Chuỗi hiển thị, ví dụ "Chap 10" hoặc "Ngoại truyện 1"
    """
    if chapter_number < 0:
        return f"Ngoại truyện {abs(chapter_number)}"
    return f"Chap {chapter_number}"


def chapter_number_to_input_syntax(chapter_number: int) -> str:
    """
    Chuyển chapter_number thành cú pháp nhập cho user.

    Args:
        chapter_number: Số chap trong DB

    Returns:
        Cú pháp nhập, ví dụ "10" hoặc "NT1"
    """
    if chapter_number < 0:
        return f"NT{abs(chapter_number)}"
    return str(chapter_number)
