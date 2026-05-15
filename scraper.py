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
    BACKUP_DOCTOR: re.compile(r"양\s*희\s*규"),
}


def fetch_post_list() -> list[dict]:
    """게시판 목록에서 진료일정표 게시글의 seq와 제목을 가져온다."""
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

        # 10페이지 이상은 너무 오래됨
        if not found_any or page >= 10:
            break
        page += 1

    return posts


def infer_year_month_from_title(title: str) -> tuple[int, int] | None:
    """제목에서 연도/월을 추출한다. 예: '2026년 5월 전체 의료진 진료일정표'"""
    m = re.search(r"(\d{4})년\s*(\d{1,2})월", title)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 연도 없이 월만 있는 경우: 현재 연도로 추정
    m2 = re.search(r"(\d{1,2})월", title)
    if m2:
        month = int(m2.group(1))
        now = datetime.now()
        year = now.year
        # 이미 지난 월이면 작년으로 보정
        if month > now.month + 1:
            year -= 1
        return year, month
    return None


def parse_schedule_from_post(seq: int, year: int, month: int) -> list[dict]:
    """
    게시글 본문을 파싱해서 김현의 근무 일정을 우선 반환한다.
    김현이 근무하지 않는 날에만 양희규의 근무 일정을 보강한다.
    반환 형식: [{"date": "2026-05-02", "start": "09", "end": "19", "doctor": "김현"}, ...]
    """
    url = f"{BASE_URL}?cf=view&seq={seq}&pg=1"
    resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
    soup = BeautifulSoup(resp.text, "lxml")

    # 본문 텍스트 추출 (줄 단위)
    content_div = _find_content_div(soup)
    if content_div is None:
        text = soup.get_text()
    else:
        text = content_div.get_text()

    return _parse_schedule_text(text, year, month)


def _find_content_div(soup: BeautifulSoup):
    """게시글 본문 영역 탐색 - 여러 후보 클래스/구조 시도."""
    # 가장 흔한 게시판 본문 클래스들
    for selector in [".view_content", ".board_view", ".content", ".bbs_content", "#content", ".post-content"]:
        el = soup.select_one(selector)
        if el:
            return el
    # 텍스트가 가장 많은 div 찾기
    candidates = soup.find_all("div")
    if candidates:
        return max(candidates, key=lambda d: len(d.get_text()))
    return None


def _parse_schedule_text(text: str, year: int, month: int) -> list[dict]:
    """
    텍스트에서 날짜별 의료진 정보를 파싱한다.

    날짜 패턴 예시:
      - "05/02(토)" 또는 "5/02(토)" 또는 "5월 2일(토)"
    의료진 패턴 예시:
      - "김현 (09-19)" 또는 "김 현(09-19)" 또는 "김현(09-19)"
      - "양희규 (09-19)" 또는 "양 희규(09-19)" 또는 "양희규(09-19)"
    """
    by_doctor = {PRIMARY_DOCTOR: [], BACKUP_DOCTOR: []}
    lines = text.split("\n")

    current_day = None

    # 날짜 매칭 패턴들
    date_patterns = [
        # "05/02(토)" 또는 "5/02(토)"
        re.compile(r"(\d{1,2})/(\d{1,2})\s*[(\[（【]?[월화수목금토일]"),
        # "5월 2일" 형태
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

        # 날짜 감지
        for dp in date_patterns:
            dm = dp.search(line)
            if dm:
                parsed_month = int(dm.group(1))
                parsed_day = int(dm.group(2))
                # 해당 월 날짜만 추적 (다른 월 날짜는 무시)
                if parsed_month == month:
                    current_day = parsed_day
                else:
                    current_day = None
                break

        # 근무시간 감지. 김현 일정은 무조건 우선하고, 양희규 일정은 마지막에 빈 날짜만 보강한다.
        if current_day is not None:
            for doctor, pattern in doctor_time_patterns.items():
                tm = pattern.search(line)
                if not tm:
                    continue

                start_h = int(tm.group(1))
                end_h = int(tm.group(2))
                date_str = f"{year:04d}-{month:02d}-{current_day:02d}"
                # 같은 의사의 같은 날짜 중복 방지
                if not any(r["date"] == date_str for r in by_doctor[doctor]):
                    by_doctor[doctor].append({
                        "date": date_str,
                        "start": f"{start_h:02d}",
                        "end": f"{end_h:02d}",
                        "doctor": doctor,
                    })

    return by_doctor


def get_schedule(year: int, month: int) -> dict[str, list[dict]]:
    """주어진 년월의 김현/양희규 근무 일정을 각각 반환한다."""
    posts = fetch_post_list()

    # 해당 월 게시글 찾기
    target_seq = None
    for post in posts:
        ym = infer_year_month_from_title(post["title"])
        if ym and ym[0] == year and ym[1] == month:
            target_seq = post["seq"]
            break

    if target_seq is None:
        return {PRIMARY_DOCTOR: [], BACKUP_DOCTOR: []}

    return parse_schedule_from_post(target_seq, year, month)
