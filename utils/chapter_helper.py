"""
Utility functions cho việc parse và hiển thị chapter (bao gồm ngoại truyện).

Quy ước:
- Chap thường: chapter_number > 0, chapter_name = "Chap X"
- Ngoại truyện: chapter_number < 0, chapter_name = "Ngoại truyện X"
  Cú pháp nhập: NT1, NT1.1, NT1.2 (không phân biệt hoa thường)
  Lưu DB: NT1 → chapter_number = -1, NT1.1 → -1.1, v.v.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Optional, Tuple, List, Union


_ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\ufeff"}
ChapterNumber = Union[int, float]
_NUMBER_PATTERN = r"\d+(?:\.\d+)?"


def _to_chapter_number(value: str) -> Optional[ChapterNumber]:
    """Convert a decimal string to a stable int/float representation."""
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError):
        return None

    if not decimal_value.is_finite():
        return None
    if decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)

    number = float(decimal_value)
    return number if isfinite(number) else None


def _format_chapter_number(number: ChapterNumber) -> str:
    """Format a chapter number without float artifacts such as ``1.1000001``."""
    text = format(Decimal(str(number)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_chapter_input(chap_str: object) -> Optional[Tuple[ChapterNumber, str]]:
    """
    Parse chuỗi chap input từ người dùng.

    Args:
        chap_str: Chuỗi nhập vào, ví dụ "10", "NT1", "NT1.1", "chap 1.1"

    Returns:
        Tuple (chapter_number, chapter_name) hoặc None nếu không hợp lệ.
        - "10" → (10, "Chap 10")
        - "NT1" → (-1, "Ngoại truyện 1")
        - "NT1.1" → (-1.1, "Ngoại truyện 1.1")
        - "chap 1.1" → (1.1, "Chap 1.1")
    """
    if isinstance(chap_str, bool):
        return None
    text = str(chap_str if chap_str is not None else "").strip()
    if not text:
        return None

    # Check ngoại truyện pattern: NT[số] hoặc NT[số thập phân].
    nt_match = re.fullmatch(rf"(?i)NT\s*({_NUMBER_PATTERN})", text)
    if nt_match:
        magnitude = _to_chapter_number(nt_match.group(1))
        if magnitude is None or magnitude <= 0:
            return None
        number = -magnitude
        return (number, f"Ngoại truyện {_format_chapter_number(magnitude)}")

    # Check số chap thường, có thể nhập thêm tiền tố "chap"/"chapter".
    chapter_match = re.fullmatch(rf"(?i)(?:chap(?:ter)?\s*)?({_NUMBER_PATTERN})", text)
    if chapter_match:
        number = _to_chapter_number(chapter_match.group(1))
        if number is None:
            return None
        return (number, f"Chap {_format_chapter_number(number)}")

    return None


def normalize_series_name(series_name: object) -> str:
    """Chuẩn hóa tên truyện để các lệnh dùng cùng một khóa tìm kiếm.

    Unicode normalization xử lý trường hợp cùng một chữ có nhiều biểu diễn,
    còn việc bỏ ký tự zero-width/gom khoảng trắng xử lý các giá trị nhìn giống
    nhau nhưng khác byte trong DB và input Discord.
    """
    text = unicodedata.normalize("NFKC", str(series_name or ""))
    text = "".join(char for char in text if char not in _ZERO_WIDTH_CHARS)
    text = " ".join(text.split())
    return text.casefold().strip()


def series_names_match(requested: object, stored: object) -> bool:
    """So khớp tên truyện sau chuẩn hóa, vẫn hỗ trợ tìm tên một phần."""
    requested_key = normalize_series_name(requested)
    stored_key = normalize_series_name(stored)
    if not requested_key or not stored_key:
        return False
    return requested_key in stored_key or stored_key in requested_key


def normalize_chapter_number(chapter_number: object) -> Optional[ChapterNumber]:
    """Đưa chapter number từ DB/input về cùng kiểu số nguyên/số thập phân.

    Hỗ trợ cả dữ liệu legacy lưu dạng chuỗi số, số thập phân và cú pháp
    ngoại truyện NT1/NT1.1.
    """
    if isinstance(chapter_number, bool):
        return None
    if isinstance(chapter_number, (int, float)):
        if isinstance(chapter_number, float) and not isfinite(chapter_number):
            return None
        return _to_chapter_number(str(chapter_number))

    text = str(chapter_number if chapter_number is not None else "").strip()
    if not text:
        return None

    if re.fullmatch(rf"-?{_NUMBER_PATTERN}", text):
        return _to_chapter_number(text)

    parsed = parse_chapter_input(text)
    return parsed[0] if parsed else None


def chapter_sort_key(chapter_number: object) -> tuple[int, float]:
    """Khóa sort chapter: chap thường tăng dần, ngoại truyện xếp sau."""
    number = normalize_chapter_number(chapter_number)
    if number is None:
        return (2, 0)
    if number < 0:
        return (1, abs(number))
    return (0, number)


def chapter_number_to_display(chapter_number: ChapterNumber) -> str:
    """
    Chuyển chapter_number thành chuỗi hiển thị.

    Args:
        chapter_number: Số chap trong DB (âm = ngoại truyện, dương = chap thường)

    Returns:
        Chuỗi hiển thị, ví dụ "Chap 10", "Chap 1.1" hoặc "Ngoại truyện 1.1"
    """
    number = normalize_chapter_number(chapter_number)
    if number is None:
        return f"Chap {chapter_number}"
    if number < 0:
        return f"Ngoại truyện {_format_chapter_number(abs(number))}"
    return f"Chap {_format_chapter_number(number)}"


def chapter_number_to_input_syntax(chapter_number: ChapterNumber) -> str:
    """
    Chuyển chapter_number thành cú pháp nhập cho user.

    Args:
        chapter_number: Số chap trong DB

    Returns:
        Cú pháp nhập, ví dụ "10", "1.1" hoặc "NT1.1"
    """
    number = normalize_chapter_number(chapter_number)
    if number is None:
        return str(chapter_number)
    if number < 0:
        return f"NT{_format_chapter_number(abs(number))}"
    return _format_chapter_number(number)


def parse_chap_numbers(text: str) -> list[ChapterNumber]:
    """Parse số/dải chap, gồm cả số thập phân như ``1.1`` và ``NT1.1``."""
    chaps: list[ChapterNumber] = []

    # Tìm các NT[số] pattern trước để không tách NT1.1 thành 1 và 1.
    nt_pattern = re.compile(rf"(?i)\bNT\s*({_NUMBER_PATTERN})(?![\d.])")
    for nt_match in nt_pattern.finditer(text):
        magnitude = _to_chapter_number(nt_match.group(1))
        if magnitude is not None and magnitude > 0:
            chaps.append(-magnitude)

    # Xóa NT patterns khỏi text để tránh trùng với số lẻ
    text_no_nt = nt_pattern.sub("", text)

    # Tìm dải số nguyên x-y (ví dụ: 11-15). Số thập phân được giữ như
    # từng chap riêng, tránh suy đoán bước tăng của dải 1.1-1.2.
    integer_range_pattern = r"(?<![\d.])(\d+)\s*[-–—]\s*(\d+)(?![\d.])"
    for range_match in re.finditer(integer_range_pattern, text_no_nt):
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end:
            chaps.extend(range(start, end + 1))

    # Xóa các dải x-y khỏi text để tránh trùng với số lẻ
    text_no_range = re.sub(integer_range_pattern, "", text_no_nt)
    number_pattern = re.compile(rf"(?<![\w.])({_NUMBER_PATTERN})(?![\w.])")
    for number_match in number_pattern.finditer(text_no_range):
        number = _to_chapter_number(number_match.group(1))
        if number is not None:
            chaps.append(number)

    # Giữ thứ tự tương thích cũ: ngoại truyện trước, sau đó đến chap thường.
    return sorted(list(set(chaps)), key=lambda number: (number >= 0, abs(number)))


def parse_series_and_chaps_input(
    chap_str: str, truyen_str: str = None
) -> List[Tuple[Optional[str], ChapterNumber]]:
    """
    Phân tích cú pháp chuỗi đầu vào chap và truyện từ Admin.
    Hỗ trợ:
    - truyen="ALPHEGA", chap="11, 12" hoặc "11-15"
    - truyen="ALPHEGA, SOLO", chap="11, 12"
    - truyen=None, chap="ALPHEGA chap 11, chap 12"
    - truyen=None, chap="ALPHEGA chap 1.1, chap 1.2"
    """
    results = []
    raw_chap = (chap_str or "").strip()
    raw_truyen = (truyen_str or "").strip()

    if raw_truyen:
        series_list = [s.strip() for s in re.split(r"[,;]", raw_truyen) if s.strip()]
        chap_nums = parse_chap_numbers(raw_chap)
        for s in series_list:
            for c in chap_nums:
                results.append((s, c))
        return results

    clauses = [c.strip() for c in re.split(r"[,;]", raw_chap) if c.strip()]
    current_series = None

    for clause in clauses:
        clean_clause_for_text = re.sub(
            r"\b(chap|chương|c)\b", "", clause, flags=re.IGNORECASE
        ).strip()
        clean_clause_for_text = re.sub(
            rf"(?i)\bNT\s*{_NUMBER_PATTERN}(?![\d.])", "", clean_clause_for_text
        )
        clean_clause_for_text = re.sub(
            rf"(?<![\w.]){_NUMBER_PATTERN}(?![\w.])", "", clean_clause_for_text
        )
        non_numeric = re.findall(r"[a-zA-ZÀ-ỹ_]+", clean_clause_for_text)

        if non_numeric:
            current_series = " ".join(non_numeric)

        nums = parse_chap_numbers(clause)
        for num in nums:
            results.append((current_series, num))

    return results
