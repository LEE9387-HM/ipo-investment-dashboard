"""
details.py - 공모주 상세 정보 수집 (수요예측·사업개요·뉴스)

Usage:
    python scripts/details.py

각 활성 공모주(청약예정/청약중/상장예정)에 대해:
1. DART 증권신고서 XML에서 수요예측·사업개요 추가 파싱
2. Google News RSS로 최근 뉴스 5건 수집
3. data/details/{rcept_no}.json 저장
"""

import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
        if hasattr(sys.stdout, "fileno") else sys.stdout
    )],
)
logger = logging.getLogger("details")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR = PROJECT_ROOT / "data"
DETAILS_DIR = DATA_DIR / "details"
DETAILS_DIR.mkdir(parents=True, exist_ok=True)

IPO_LIST_PATH = DATA_DIR / "ipo_list.csv"

DART_API_KEY: str = os.environ.get("DART_API_KEY", "")
DART_BASE_URL = "https://opendart.fss.or.kr/api"
API_DELAY = 0.5

NAVER_CLIENT_ID: str     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET: str = os.environ.get("NAVER_CLIENT_SECRET", "")
NAVER_SEARCH_BASE = "https://openapi.naver.com/v1/search"


# ---------------------------------------------------------------------------
# DART document 다운로드 (collect.py와 동일 로직)
# ---------------------------------------------------------------------------

def fetch_document_text(rcept_no: str) -> Optional[str]:
    """DART 문서 ZIP을 다운로드하여 가장 큰 XML 텍스트를 반환합니다."""
    try:
        resp = requests.get(
            f"{DART_BASE_URL}/document.xml",
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        # DART가 오류일 때 ZIP 대신 XML 오류 응답을 반환하는 경우 처리
        if resp.content[:4] != b"PK\x03\x04":
            logger.debug(f"DART 오류 응답 (ZIP 아님): {resp.text[:200]}")
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_names = [n for n in zf.namelist() if n.endswith(".xml")]
            if not xml_names:
                return None
            target = max(xml_names, key=lambda n: zf.getinfo(n).file_size)
            raw = zf.read(target)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
    except Exception as exc:
        logger.debug(f"document 다운로드 실패 ({rcept_no}): {exc}")
    return None


# ---------------------------------------------------------------------------
# 추가 파싱 — 수요예측·사업개요
# ---------------------------------------------------------------------------

def _plain(xml_text: str) -> str:
    """XML 태그 제거 후 평문 반환."""
    t = re.sub(r"<[^>]+>", " ", xml_text)
    t = re.sub(r"&[a-zA-Z#\d]+;", " ", t)
    return re.sub(r"\s+", " ", t)


def _num(s: str) -> str:
    return re.sub(r"[,\s원]", "", s)


def extract_detail_fields(xml_text: str) -> dict:
    """
    DART 증권신고서 XML에서 수요예측 관련 추가 정보를 추출합니다.

    Returns:
        {
            competition_ratio:       기관투자자 경쟁률 (예: "842")
            lock_up_ratio:           의무보유확약 비율 (예: "31.2")
            demand_forecast_period:  수요예측 기간 (YYYYMMDD~YYYYMMDD)
            business_summary:        사업 개요 (최대 300자)
            total_shares_offered:    공모주식수 (재확인)
            min_bid_price:           최저 입찰 공모가
        }
    """
    plain = _plain(xml_text)
    result: dict = {
        "competition_ratio": "",
        "lock_up_ratio": "",
        "demand_forecast_period": "",
        "business_summary": "",
        "total_shares_offered": "",
        "min_bid_price": "",
    }

    DATE_PAT = r"(\d{4}[년.\s]*\d{1,2}[월.\s]*\d{1,2}일?)"

    # 기관투자자 경쟁률 — "X : 1" / "X대 1" 형식
    cr = re.search(
        r"기관\s*(?:투자자\s*)?(?:수요예측\s*)?경쟁률[^\d]*(\d[\d,]*\.?\d*)\s*(?::\s*1|대\s*1)", plain
    )
    if not cr:
        cr = re.search(r"경쟁률[^\d]*(\d[\d,]*\.?\d*)\s*(?::\s*1|대\s*1)", plain)
    # 수요예측 테이블 형식: "경쟁률주N) [숫자들] 합계" — 주N) 바로 앞 숫자가 합계 경쟁률
    if not cr:
        cr = re.search(
            r"경쟁률[^\n]{5,300}([\d,]+\.\d+)\s+주\d",
            plain,
        )
    if cr:
        result["competition_ratio"] = _num(cr.group(1))

    # 의무보유확약 비율 (기관투자자 수요예측 확약비율)
    # 상장주선인 의무인수 1% 조항("공모주식의 1%에 해당하는 수량")과 반드시 구별
    lu = re.search(
        r"의무\s*보유\s*확약\s*(?:신청\s*)?비율[^\d%\n]*(\d+\.?\d*)\s*%", plain
    )
    if not lu:
        # 넓은 패턴으로 재탐색, 단 상장주선인 컨텍스트는 제외
        for m in re.finditer(r"의무\s*보유\s*확약[^%\n]{0,80}?(\d+\.?\d*)\s*%", plain):
            ctx = plain[max(0, m.start() - 80) : m.start()]
            if "상장주선인" in ctx or "금번 공모주식의" in ctx:
                continue
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            if val <= 1.0:  # 규정 최솟값 1% 이하 → 상장주선인 조항일 가능성
                continue
            lu = m
            break
    if lu:
        result["lock_up_ratio"] = lu.group(1)

    # 수요예측 기간
    df_matches = re.findall(
        r"수요\s*예측\s*(?:기간|일정)[^0-9]*" + DATE_PAT + r"[^0-9]*[~～\-]\s*" + DATE_PAT, plain
    )
    if df_matches:
        last = df_matches[-1]
        s = re.sub(r"[년월일.\s]", "", last[0]).strip()
        e = re.sub(r"[년월일.\s]", "", last[1]).strip()
        if len(s) == 8 and len(e) == 8:
            result["demand_forecast_period"] = f"{s}~{e}"

    # 사업 개요 (회사 소개 첫 문단, 최대 300자)
    biz = re.search(
        r"(?:회사의\s*개요|사업의\s*내용|주요\s*사업)[^가-힣]{0,20}([가-힣A-Za-z0-9][^.]{50,300}\.)", plain
    )
    if biz:
        result["business_summary"] = biz.group(1).strip()[:300]

    # 공모주식수 재확인
    sh = re.search(r"공모\s*주식[수]?\s*[:\s]*(\d[\d,]*)\s*주", plain)
    if sh:
        result["total_shares_offered"] = _num(sh.group(1))

    # 최저입찰공모가 (밴드 하단)
    mbp = re.search(r"최저\s*(?:입찰\s*)?공모가[^\d]*(\d[\d,]*)\s*원", plain)
    if mbp:
        result["min_bid_price"] = _num(mbp.group(1))

    return result


# ---------------------------------------------------------------------------
# 뉴스 수집 (Google News RSS)
# ---------------------------------------------------------------------------

def fetch_news(corp_name: str) -> list[dict]:
    """Google News RSS로 최근 뉴스를 최대 5건 반환합니다."""
    query = requests.utils.quote(f"{corp_name} 공모주")
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:5]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            if title and link:
                items.append({"title": title, "link": link, "pubDate": pub})
        return items
    except Exception as exc:
        logger.debug(f"뉴스 조회 실패 ({corp_name}): {exc}")
        return []


# ---------------------------------------------------------------------------
# 네이버 커뮤니티 뉴스 수집 (뉴스 + 블로그)
# ---------------------------------------------------------------------------

def fetch_community_news(corp_name: str) -> list[dict]:
    """
    네이버 검색 API로 뉴스·블로그 게시물을 수집합니다.
    NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없으면 빈 리스트 반환.
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    headers = {
        "X-Naver-Client-Id":     NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    items: list[dict] = []

    # 뉴스 검색 (최대 5건)
    try:
        query = f"{corp_name} 공모주"
        resp = requests.get(
            f"{NAVER_SEARCH_BASE}/news.json",
            headers=headers,
            params={"query": query, "display": 5, "sort": "date"},
            timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                items.append({
                    "title":       item.get("title", ""),
                    "link":        item.get("originallink") or item.get("link", ""),
                    "description": item.get("description", ""),
                    "pubDate":     item.get("pubDate", ""),
                    "source":      "뉴스",
                })
        else:
            logger.debug(f"네이버 뉴스 실패 ({resp.status_code}): {corp_name}")
    except Exception as exc:
        logger.debug(f"네이버 뉴스 오류 ({corp_name}): {exc}")

    time.sleep(0.2)

    # 블로그 검색 (최대 3건)
    try:
        query_blog = f"{corp_name} 공모주 청약"
        resp = requests.get(
            f"{NAVER_SEARCH_BASE}/blog.json",
            headers=headers,
            params={"query": query_blog, "display": 3, "sort": "date"},
            timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                items.append({
                    "title":       item.get("title", ""),
                    "link":        item.get("link", ""),
                    "description": item.get("description", ""),
                    "pubDate":     item.get("postdate", ""),
                    "source":      "블로그",
                })
        else:
            logger.debug(f"네이버 블로그 실패 ({resp.status_code}): {corp_name}")
    except Exception as exc:
        logger.debug(f"네이버 블로그 오류 ({corp_name}): {exc}")

    return items


# ---------------------------------------------------------------------------
# DART 공시 링크 생성
# ---------------------------------------------------------------------------

def dart_link(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


# ---------------------------------------------------------------------------
# 메인 처리
# ---------------------------------------------------------------------------

def build_detail(row: pd.Series) -> Optional[dict]:
    rcept_no = str(row.get("rcept_no", "")).strip()
    corp_name = str(row.get("corp_name", "")).strip()
    if not rcept_no or not corp_name:
        return None

    detail: dict = {
        "rcept_no":       rcept_no,
        "corp_name":      corp_name,
        "corp_code":      str(row.get("corp_code", "")),
        "stock_code":     str(row.get("stock_code", "")),
        "market":         str(row.get("market", "")),
        "status":         str(row.get("status", "")),
        "dart_link":      dart_link(rcept_no),
        "offering_price_low":   str(row.get("offering_price_low", "")),
        "offering_price_high":  str(row.get("offering_price_high", "")),
        "offering_price_final": str(row.get("offering_price_final", "")),
        "subscription_start_dt": str(row.get("subscription_start_dt", "")),
        "subscription_end_dt":   str(row.get("subscription_end_dt", "")),
        "listing_dt":     str(row.get("listing_dt", "")),
        "underwriter":    str(row.get("underwriter", "")),
        "total_shares":   str(row.get("total_shares", "")),
        # 추가 파싱 필드 (document XML)
        "competition_ratio":      "",
        "lock_up_ratio":          "",
        "demand_forecast_period": "",
        "business_summary":       "",
        # 뉴스
        "news":           [],
        "community_news": [],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # DART 문서에서 추가 파싱
    logger.info(f"{corp_name} 문서 다운로드 중...")
    xml_text = fetch_document_text(rcept_no)
    time.sleep(API_DELAY)

    if xml_text:
        extra = extract_detail_fields(xml_text)
        detail.update(extra)
        logger.info(
            f"  경쟁률={extra['competition_ratio'] or '-'}, "
            f"확약={extra['lock_up_ratio'] or '-'}%, "
            f"사업개요={len(extra['business_summary'])}자"
        )
    else:
        logger.warning(f"  {corp_name} 문서 없음")

    # 구글 뉴스
    detail["news"] = fetch_news(corp_name)
    time.sleep(0.3)
    logger.info(f"  뉴스 {len(detail['news'])}건")

    # 네이버 커뮤니티 뉴스·블로그
    detail["community_news"] = fetch_community_news(corp_name)
    if detail["community_news"]:
        logger.info(f"  커뮤니티 {len(detail['community_news'])}건")
    time.sleep(0.3)

    return detail


def run() -> int:
    logger.info("=" * 50)
    logger.info("details.py 시작")
    logger.info("=" * 50)

    if not DART_API_KEY:
        logger.error("DART_API_KEY가 설정되지 않았습니다.")
        return 1

    if not IPO_LIST_PATH.exists():
        logger.error("ipo_list.csv가 없습니다.")
        return 1

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str).fillna("")
    today = datetime.now().strftime("%Y%m%d")
    in_30days = (datetime.now().replace(hour=0, minute=0, second=0) +
                 __import__("datetime").timedelta(days=30)).strftime("%Y%m%d")

    # status 컬럼 대신 날짜 기준으로 활성 종목 선별 (stale status 방지)
    def _is_active(row: pd.Series) -> bool:
        listing_dt = str(row.get("listing_dt", "")).strip()
        sub_start  = str(row.get("subscription_start_dt", "")).strip()
        sub_end    = str(row.get("subscription_end_dt", "")).strip()
        if listing_dt and listing_dt <= today:
            return False  # 이미 상장 완료
        if sub_end and sub_end < today and not listing_dt:
            return False  # 청약 종료, 상장일 미확인
        if sub_start and sub_start > in_30days:
            return False  # 30일 이후 청약 예정은 제외
        return True

    targets = ipo_df[ipo_df.apply(_is_active, axis=1)].copy()

    logger.info(f"처리 대상: {len(targets)}건")

    saved = 0
    for _, row in targets.iterrows():
        rcept_no = str(row.get("rcept_no", "")).strip()
        detail_path = DETAILS_DIR / f"{rcept_no}.json"

        # 24시간 이내 생성된 파일은 스킵
        if detail_path.exists():
            age_hours = (datetime.now().timestamp() - detail_path.stat().st_mtime) / 3600
            if age_hours < 24:
                logger.debug(f"스킵 (최신): {row.get('corp_name')}")
                continue

        detail = build_detail(row)
        if detail:
            detail_path.write_text(
                json.dumps(detail, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            saved += 1

    logger.info(f"상세 정보 저장: {saved}건 (총 {len(list(DETAILS_DIR.glob('*.json')))}개 파일)")
    logger.info("=" * 50)
    logger.info("details.py 완료")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run())
