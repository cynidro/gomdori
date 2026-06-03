import re
import httpx
from bs4 import BeautifulSoup
from datetime import datetime

BASE_URL = "https://www.gomdori.kr/page/s2/s3.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
}

PRIMARY_DOCTOR = "김현"
BACKUP_DOCTOR = "양희규"

DOCTOR_PATTERNS = {
    PRIMARY_DOCTOR: re.compile(r"김\s*현"),
    BACKUP_DOCTOR:  re.compile(r"양\s*희\s*규"),
}

_SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배조백허유남심노정하곽성차주우구신임나전민유류진지엄채원천방공강현함변염양변여추노도소신석선설마길주연방위표명기반왕모장남탁국여진어"

_NAME_PAT = re.compile(
    rf"([{_SURNAMES}])\s*([가-힣]{{1,2}})\s*(?:원장|의사|선생|Dr\.?)?\s*"
    r"[(\[（【]\s*(\d{1,2})\s*[-~]\s*(\d{1,2})\s*[)\]）】]"
)

def fetch_post_list() -> list[dict]:
    posts = []
    page = 1
    while True:
        resp = httpx.get(BASE_URL, params={"pg": page}, headers=HEADERS, timeout=10, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        found_any = False
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "cf=view" not in href:
                continue
            title = a.get_text(strip=True)
            if "전체 의료진 진료일정표" not in title and "의료진 진료일정표" not in title:
                continue
            m = re.search(r"seq=(\d+)", href)
            if not m:
                continue
            posts.append({"seq": int(m.group(1)), "title": title})
            found_any = True
        if not found_any or page >= 10:
            break
        page += 1
    return posts

def infer_year_month_from_title(title: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{4})년\s*(\d{1,2})월", title)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"(\d{1,2})월", title)
    if m2:
        month = int(m2.group(1))
        now = datetime.now()
        year = now.year
        if month > now.month + 1:
            year -= 1
        return year, month
    return None

def parse_schedule_from_post(seq: int, year: int, month: int) -> dict:
    url = f"{BASE_URL}?cf=view&seq={seq}&pg=1"
    resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
    soup = BeautifulSoup(resp.text, "lxml")
    content_div = _find_content_div(soup)
    text = content_div.get_text() if content_div else soup.get_text()
    return _parse_schedule_text(text, year, month)

def _find_content_div(soup: BeautifulSoup):
    for selector in [".view_content", ".board_view", ".content", ".bbs_content", "#content", ".post-content"]:
        el = soup.select_one(selector)
        if el:
            return el
    candidates = soup.find_all("div")
    if candidates:
        return max(candidates, key=lambda d: len(d.get_text()))
    return None

def _parse_schedule_text(text: str, year: int, month: int) -> dict:
    by_doctor = {PRIMARY_DOCTOR: [], BACKUP_DOCTOR: []}
    all_day: dict[str, list[dict]] = {}
    lines = text.split("\n")
    current_day = None
    date_patterns = [
        re.compile(r"(\d{1,2})/(\d{1,2})\s*[(\[（【]?[월화수목금토일]"),
        re.compile(r"(\d{1,2})월\s*(\d{1,2})일"),
    ]
    doctor_time_patterns = {
        doctor: re.compile(
            pattern.pattern + r"\s*[(\[（【]\s*(\d{1,2})\s*[-~]\s*(\d{1,2})\s*[)\]）】]"
        )
        for doctor, pattern in DOCTOR_PATTERNS.items()
    }
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for dp in date_patterns:
            dm = dp.search(line)
            if dm:
                parsed_month = int(dm.group(1))
                parsed_day   = int(dm.group(2))
                if parsed_month == month:
                    current_day = parsed_day
                else:
                    current_day = None
                break
        if current_day is None:
            continue
        date_str = f"{year:04d}-{month:02d}-{current_day:02d}"
        for doctor, pattern in doctor_time_patterns.items():
            tm = pattern.search(line)
            if not tm:
                continue
            start_h = int(tm.group(1))
            end_h   = int(tm.group(2))
            if not any(r["date"] == date_str for r in by_doctor[doctor]):
                by_doctor[doctor].append({
                    "date": date_str, "start": f"{start_h:02d}",
                    "end": f"{end_h:02d}", "doctor": doctor,
                })
        for m in _NAME_PAT.finditer(line):
            full_name = m.group(1) + m.group(2)
            start_h   = int(m.group(3))
            end_h     = int(m.group(4))
            if date_str not in all_day:
                all_day[date_str] = []
            if not any(r["name"] == full_name for r in all_day[date_str]):
                all_day[date_str].append({
                    "name": full_name, "start": f"{start_h:02d}", "end": f"{end_h:02d}",
                })
    return {PRIMARY_DOCTOR: by_doctor[PRIMARY_DOCTOR], BACKUP_DOCTOR: by_doctor[BACKUP_DOCTOR], "all": all_day}

def get_schedule(year: int, month: int) -> dict:
    posts = fetch_post_list()
    target_seq = None
    for post in posts:
        ym = infer_year_month_from_title(post["title"])
        if ym and ym[0] == year and ym[1] == month:
            target_seq = post["seq"]
            break
    if target_seq is None:
        return {PRIMARY_DOCTOR: [], BACKUP_DOCTOR: [], "all": {}}
    return parse_schedule_from_post(target_seq, year, month)
