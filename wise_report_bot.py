from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from typing import Iterable
from zoneinfo import ZoneInfo

import holidays
import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://comp.wisereport.co.kr/wiseReport/summary/ReportSummary.aspx"
SEOUL_TZ = ZoneInfo("Asia/Seoul")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class CategoryConfig:
    name: str
    fmt: int
    sort_column: str
    date_style: str
    sort_key: str
    headers: tuple[str, ...]


CATEGORIES: tuple[CategoryConfig, ...] = (
    CategoryConfig(
        name="기업",
        fmt=1,
        sort_column="CMP_NM_KOR",
        date_style="compact",
        sort_key="기업명",
        headers=("기업명", "기관명/작성자", "투자의견", "목표주가", "전일수정주가", "제목", "요약"),
    ),
    CategoryConfig(
        name="산업",
        fmt=2,
        sort_column="SEC_NM_KOR",
        date_style="dash",
        sort_key="산업명",
        headers=("산업명", "기관명/작성자", "투자의견", "이전의견", "제목", "요약"),
    ),
    CategoryConfig(
        name="정기",
        fmt=3,
        sort_column="BRK_NM_KOR",
        date_style="dash",
        sort_key="기관명",
        headers=("기관명", "작성자", "분류", "제목"),
    ),
)


def today_in_seoul() -> date:
    return datetime.now(SEOUL_TZ).date()


def is_korean_business_day(target_date: date) -> bool:
    if target_date.weekday() >= 5:
        return False

    kr_holidays = holidays.country_holidays("KR", years=target_date.year)
    return target_date not in kr_holidays


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def format_target_date(target_date: date, style: str) -> str:
    if style == "compact":
        return target_date.strftime("%Y%m%d")
    if style == "dash":
        return target_date.isoformat()
    raise ValueError(f"Unsupported date style: {style}")


def build_url(config: CategoryConfig, target_date: date) -> str:
    ee = format_target_date(target_date, config.date_style)
    return (
        f"{BASE_URL}?fmt={config.fmt}&ee={ee}&sortcol={config.sort_column}"
        f"&sorttyp=asc&typ=0&searchKeyWord="
    )


def clean_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unescape(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_regular_record(record: dict[str, str]) -> dict[str, str]:
    institution = record["기관명"]
    author = record["작성자"]
    category = record["분류"]
    title = record["제목"]
    suffix = f" {author} {category} {title}".strip()
    if suffix and institution.endswith(suffix):
        institution = institution[: -len(suffix)].strip()

    normalized = dict(record)
    normalized["기관명"] = institution
    return normalized


def get_header_row(table: Tag) -> list[str]:
    for row in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if cells:
            return cells
    return []


def find_data_table(soup: BeautifulSoup, expected_headers: tuple[str, ...]) -> Tag:
    for table in soup.find_all("table"):
        if tuple(get_header_row(table)) == expected_headers:
            return table
    raise RuntimeError(f"Expected table with headers {expected_headers!r} not found")


def extract_change_label(cell: Tag) -> str:
    icon = cell.find("img")
    if not icon:
        return ""
    alt = clean_text(icon.get("alt"))
    if "상향" in alt:
        return "상향"
    if "하향" in alt:
        return "하향"
    if "신규" in alt:
        return "신규"
    if "변동없음" in alt:
        return "변동없음"
    return alt


def parse_company_rows(table: Tag) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["th", "td"])
        if len(cells) != 7:
            continue

        record = {
            "기업명": clean_text(cells[0].get_text(" ", strip=True)),
            "기관명/작성자": clean_text(cells[1].get_text(" ", strip=True)),
            "투자의견": clean_text(cells[2].get_text(" ", strip=True)),
            "목표주가": clean_text(cells[3].get_text(" ", strip=True)),
            "전일수정주가": clean_text(cells[4].get_text(" ", strip=True)),
            "제목": clean_text(cells[5].get_text(" ", strip=True)),
            "요약": clean_text(cells[6].get_text(" ", strip=True)),
            "투자의견변화": extract_change_label(cells[2]),
            "목표주가변화": extract_change_label(cells[3]),
        }
        if record["기업명"]:
            rows.append(record)
    return rows


def parse_standard_rows(table: Tag, headers: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr")[1:]:
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        if len(cells) != len(headers):
            continue
        if not any(cells):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def fetch_category(session: requests.Session, config: CategoryConfig, target_date: date) -> list[dict[str, str]]:
    response = session.get(build_url(config, target_date), timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.content.decode("utf-8", errors="replace"), "html.parser")
    table = find_data_table(soup, config.headers)

    if config.name == "기업":
        records = parse_company_rows(table)
    else:
        records = parse_standard_rows(table, config.headers)
        if config.name == "정기":
            records = [normalize_regular_record(record) for record in records]

    return sorted(records, key=lambda item: item.get(config.sort_key, ""))


def truncate(value: str, limit: int = 140) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def escape_html(value: str) -> str:
    return html.escape(value, quote=False)


def write_console(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))


def visible_width(value: str) -> int:
    return len(value)


def pad_cell(value: str, width: int) -> str:
    value = value[:width]
    return value + (" " * max(0, width - visible_width(value)))


def decorate_change_label(change: str) -> str:
    mapping = {
        "상향": "🔺상향",
        "하향": "🔽하향",
        "신규": "🆕신규",
        "변동없음": "➖유지",
        "목표주가 없음": "·없음",
        "표시없음": "·없음",
        "-": "-",
        "": "-",
    }
    return mapping.get(change, change)


def build_pre_table(headers: list[tuple[str, int]], rows: list[list[str]]) -> str:
    header_line = " ".join(pad_cell(label, width) for label, width in headers)
    divider = "-" * len(header_line)
    body = [" ".join(pad_cell(cell, width) for cell, (_, width) in zip(row, headers)) for row in rows]
    return "<pre>" + escape_html("\n".join([header_line, divider, *body])) + "</pre>"


def chunked(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def build_company_table(items: list[dict[str, str]]) -> str:
    headers = [("기업명", 16), ("변화", 8), ("목표가", 10), ("증권사", 18)]
    rows = []
    for item in items:
        rows.append(
            [
                re.sub(r"\s*\(\d+\)$", "", item["기업명"]),
                decorate_change_label(item.get("목표주가변화") or "-"),
                item.get("목표주가") or "-",
                item["기관명/작성자"].split("[", 1)[0].strip(),
            ]
        )
    return build_pre_table(headers, rows)


def build_company_detail(item: dict[str, str]) -> str:
    title = escape_html(truncate(item["제목"], 90))
    summary = escape_html(truncate(item.get("요약", ""), 140) or "-")
    company = escape_html(item["기업명"])
    change = escape_html(decorate_change_label(item.get("목표주가변화") or "표시없음"))
    price = escape_html(item.get("목표주가") or "-")
    broker = escape_html(item["기관명/작성자"])
    return (
        f"<b>{company}</b>\n"
        f"제목: {title}\n"
        f"증권사: {broker}\n"
        f"목표주가: {change} / {price}\n"
        f"요약: {summary}"
    )


def build_company_section(items: list[dict[str, str]]) -> str:
    return ""


def build_industry_section(items: list[dict[str, str]]) -> str:
    return ""


def build_regular_section(items: list[dict[str, str]]) -> str:
    return ""


def build_company_messages(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["<b>[기업]</b>\n발간 리포트가 없거나 파싱에 실패했습니다."]

    messages: list[str] = []
    for index, batch in enumerate(chunked(items, 25), start=1):
        title = "<b>[기업]</b>" if index == 1 else f"<b>[기업 이어서 {index}]</b>"
        messages.append(f"{title}\n\n{build_company_table(batch)}")

    changed_items = [
        item for item in items if item.get("목표주가변화") in {"상향", "하향", "신규"}
    ]
    if changed_items:
        for index, batch in enumerate(chunked(changed_items, 6), start=1):
            title = "<b>[기업 상세: 목표주가 변동]</b>" if index == 1 else f"<b>[기업 상세: 목표주가 변동 {index}]</b>"
            body = "\n\n".join(build_company_detail(item) for item in batch)
            messages.append(f"{title}\n\n{body}")

    return messages


def build_industry_messages(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["<b>[산업]</b>\n발간 리포트가 없거나 파싱에 실패했습니다."]

    headers = [("산업명", 18), ("증권사", 14), ("제목", 30)]
    messages: list[str] = []
    for index, batch in enumerate(chunked(items, 24), start=1):
        rows = [
            [
                item["산업명"],
                item["기관명/작성자"].split("[", 1)[0].strip(),
                truncate(item["제목"], 30),
            ]
            for item in batch
        ]
        title = "<b>[산업]</b>" if index == 1 else f"<b>[산업 이어서 {index}]</b>"
        messages.append(f"{title}\n\n{build_pre_table(headers, rows)}")
    return messages


def build_regular_messages(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["<b>[정기]</b>\n발간 리포트가 없거나 파싱에 실패했습니다."]

    headers = [("기관", 12), ("분류", 12), ("제목", 36)]
    messages: list[str] = []
    for index, batch in enumerate(chunked(items, 32), start=1):
        rows = [[item["기관명"], item["분류"], truncate(item["제목"], 36)] for item in batch]
        title = "<b>[정기]</b>" if index == 1 else f"<b>[정기 이어서 {index}]</b>"
        messages.append(f"{title}\n\n{build_pre_table(headers, rows)}")
    return messages


def build_simple_section(category: str, items: list[dict[str, str]]) -> str:
    header = f"<b>[{escape_html(category)}]</b>"
    if not items:
        return f"{header}\n발간 리포트가 없거나 파싱에 실패했습니다."

    lines = [header]
    for item in items:
        if category == "산업":
            line = (
                f"- {escape_html(item['산업명'])} | "
                f"{escape_html(item['기관명/작성자'])} | "
                f"{escape_html(truncate(item['제목']))}"
            )
        elif category == "정기":
            line = (
                f"- {escape_html(item['기관명'])} | "
                f"{escape_html(item['분류'])} | "
                f"{escape_html(truncate(item['제목']))}"
            )
        else:
            raise ValueError(f"Unsupported category: {category}")
        lines.append(line)
    return "\n".join(lines)


def split_messages(chunks: Iterable[str], prefix: str) -> list[str]:
    messages: list[str] = []
    current = prefix

    for chunk in chunks:
        addition = f"\n\n{chunk}" if current else chunk
        if len(current) + len(addition) <= MESSAGE_LIMIT:
            current += addition
            continue

        if current:
            messages.append(current)
        current = prefix + ("\n\n" if prefix else "") + chunk

        if len(current) <= MESSAGE_LIMIT:
            continue

        lines = chunk.splitlines()
        current = prefix
        for line in lines:
            addition = f"\n{line}" if current else line
            if len(current) + len(addition) <= MESSAGE_LIMIT:
                current += addition
            else:
                if current:
                    messages.append(current)
                current = line

    if current:
        messages.append(current)
    return messages


def send_telegram_messages(token: str, chat_id: str, messages: list[str]) -> None:
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    for text in messages:
        response = requests.post(
            api_url,
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send today's WiseReport summaries to Telegram.")
    parser.add_argument("--date", help="Target date in YYYY-MM-DD format. Defaults to today in Asia/Seoul.")
    parser.add_argument("--dry-run", action="store_true", help="Print messages without sending them.")
    parser.add_argument(
        "--skip-non-business-day",
        action="store_true",
        help="Exit successfully without sending on weekends and Korean public holidays.",
    )
    return parser.parse_args()


def resolve_target_date(raw_date: str | None) -> date:
    if not raw_date:
        return today_in_seoul()
    return datetime.strptime(raw_date, "%Y-%m-%d").date()


def main() -> None:
    load_dotenv()
    args = parse_args()
    target_date = resolve_target_date(args.date)

    if args.skip_non_business_day and not is_korean_business_day(target_date):
        write_console(f"[SKIP] {target_date.isoformat()} is not a Korean business day.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    all_data: dict[str, list[dict[str, str]]] = {}
    for config in CATEGORIES:
        all_data[config.name] = fetch_category(session, config, target_date)

    prefix = f"<b>[{target_date.isoformat()} 와이즈리포트 발간 요약]</b>"
    messages = [prefix]
    messages.extend(build_company_messages(all_data["기업"]))
    messages.extend(build_industry_messages(all_data["산업"]))
    messages.extend(build_regular_messages(all_data["정기"]))

    if args.dry_run:
        write_console("\n\n" + ("\n" + ("-" * 80) + "\n").join(messages))
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")

    send_telegram_messages(token, chat_id, messages)


if __name__ == "__main__":
    main()
