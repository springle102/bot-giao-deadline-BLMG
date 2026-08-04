"""
Utility functions cho việc parse và hiển thị chapter (bao gồm ngoại truyện).

Quy ước:
- Chap thường: chapter_number > 0, chapter_name = "Chap X"
- Ngoại truyện: chapter_number < 0, chapter_name = "Ngoại truyện X"
  Cú pháp nhập: NT1, NT2, NT3 (liền kề, không cách)
  Lưu DB: NT1 → chapter_number = -1, NT2 → -2, v.v.
"""

import re
from typing import Optional, Tuple, List


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


def parse_chap_numbers(text: str) -> list[int]:
    """Parse các số chap và dải chap như '1, 2, 5-8, NT1, NT2' thành danh sách [1, 2, 5, 6, 7, 8, -1, -2]."""
    chaps = []

    # Tìm các NT[số] pattern trước (case-insensitive)
    for nt_match in re.finditer(r'(?i)\bNT(\d+)\b', text):
        num = int(nt_match.group(1))
        if num > 0:
            chaps.append(-num)

    # Xóa NT patterns khỏi text để tránh trùng với số lẻ
    text_no_nt = re.sub(r'(?i)\bNT\d+\b', '', text)

    # Tìm dải số x-y (ví dụ: 11-15)
    for range_match in re.finditer(r'(\d+)\s*[-–—]\s*(\d+)', text_no_nt):
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end:
            chaps.extend(range(start, end + 1))

    # Xóa các dải x-y khỏi text để tránh trùng với số lẻ
    text_no_range = re.sub(r'\d+\s*[-–—]\s*\d+', '', text_no_nt)
    for num in re.findall(r'\b\d+\b', text_no_range):
        chaps.append(int(num))

    return sorted(list(set(chaps)), key=lambda x: (x >= 0, abs(x)))


def parse_series_and_chaps_input(chap_str: str, truyen_str: str = None) -> List[Tuple[Optional[str], int]]:
    """
    Phân tích cú pháp chuỗi đầu vào chap và truyện từ Admin.
    Hỗ trợ:
    - truyen="ALPHEGA", chap="11, 12" hoặc "11-15"
    - truyen="ALPHEGA, SOLO", chap="11, 12"
    - truyen=None, chap="ALPHEGA chap 11, chap 12"
    - truyen=None, chap="ALPHEGA 11, SOLO 5, 6"
    """
    results = []
    raw_chap = (chap_str or "").strip()
    raw_truyen = (truyen_str or "").strip()

    if raw_truyen:
        series_list = [s.strip() for s in re.split(r'[,;]', raw_truyen) if s.strip()]
        chap_nums = parse_chap_numbers(raw_chap)
        for s in series_list:
            for c in chap_nums:
                results.append((s, c))
        return results

    clauses = [c.strip() for c in re.split(r'[,;]', raw_chap) if c.strip()]
    current_series = None

    for clause in clauses:
        clean_clause_for_text = re.sub(r'\b(chap|chương|c)\b', '', clause, flags=re.IGNORECASE).strip()
        words = re.findall(r'[a-zA-ZÀ-ỹ0-9_]+', clean_clause_for_text)
        non_numeric = [w for w in words if not w.isdigit() and not re.match(r'(?i)^NT\d+$', w)]

        if non_numeric:
            current_series = " ".join(non_numeric)

        nums = parse_chap_numbers(clause)
        for num in nums:
            results.append((current_series, num))

    return results

