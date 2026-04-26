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
    handlers=[logging.StreamHandler(sys.stdout)],
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

    # 기관투자자 경쟁률 — "X : 1" 또는 "X대 1" 패턴
    cr = re.search(
        r"기관\s*(?:투자자\s*)?(?:수요예측\s*)?경쟁률[^\d]*(\d[\d,]*)\s*(?::\s*1|대\s*1)", plain
    )
    if not cr:
        cr = re.search(r"경쟁률[^\d]*(\d[\d,]*)\s*(?::\s*1|대\s*1)", plain)
    if cr:
        result["competition_ratio"] = _num(cr.group(1))

    # 의무보유확약 비율
    lu = re.search(r"의무\s*보유\s*확약[^%]*?(\d+\.?\d*)\s*%", plain)
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
        "news":      [],
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

    # 뉴스
    detail["news"] = fetch_news(corp_name)
    time.sleep(0.3)
    logger.info(f"  뉴스 {len(detail['news'])}건")

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

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str)
    targets = ipo_df[
        ipo_df["status"].isin(["청약중", "청약예정", "상장예정"])
    ].copy()

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
