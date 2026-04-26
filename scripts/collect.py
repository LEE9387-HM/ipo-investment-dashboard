"""
collect.py - DART OpenAPI를 통한 공모주(증권신고서) 목록 수집 스크립트

Usage:
    python scripts/collect.py

Required Environment Variables (.env.local):
    DART_API_KEY: DART OpenAPI 인증키
    발급: https://opendart.fss.or.kr/uss/usr/ehAuthManagg/insertManaggForm.do
"""

import io
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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
logger = logging.getLogger("collect")

# 프로젝트 루트 기준으로 .env.local 로드
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

IPO_LIST_PATH = DATA_DIR / "ipo_list.csv"

# ---------------------------------------------------------------------------
# DART API 설정
# ---------------------------------------------------------------------------

DART_BASE_URL = "https://opendart.fss.or.kr/api"
DART_API_KEY: str = os.environ.get("DART_API_KEY", "")

# pblntf_ty=C: 발행공시 필터 (pblntf_detail_ty는 API에서 무시됨 — report_nm으로 2차 필터링)
DISCLOSURE_TYPE = "C"
IPO_REPORT_KEYWORDS = ["증권신고서(지분증권)", "[기재정정]증권신고서(지분증권)"]

# ---------------------------------------------------------------------------
# Phase 2: 상세 정보 보완 상수
# ---------------------------------------------------------------------------

CORP_CLS_MAP: dict[str, str] = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "기타"}
API_DELAY: float = 0.4  # DART API 호출 간격(초) — rate limiting

# ---------------------------------------------------------------------------
# 데이터 스키마 정의
# ---------------------------------------------------------------------------

IPO_LIST_COLUMNS: list[str] = [
    "corp_code",              # DART 기업 고유코드
    "corp_name",              # 기업명
    "stock_code",             # 종목코드 (상장 전 미정 가능)
    "rcept_no",               # DART 접수번호 (Primary Key)
    "rcept_dt",               # 접수일 (YYYYMMDD)
    "report_nm",              # 보고서 명칭
    "market",                 # 상장시장 (KOSPI / KOSDAQ / N/A)
    "subscription_start_dt",  # 청약시작일 (YYYYMMDD)
    "subscription_end_dt",    # 청약종료일 (YYYYMMDD)
    "listing_dt",             # 상장예정일 (YYYYMMDD)
    "offering_price_low",     # 공모가 희망밴드 하단 (원)
    "offering_price_high",    # 공모가 희망밴드 상단 (원)
    "offering_price_final",   # 확정 공모가 (원)
    "total_shares",           # 공모주식수 (주)
    "total_amount",           # 공모금액 (원)
    "underwriter",            # 주관사
    "status",                 # 상태: 정보수집중 / 청약예정 / 청약중 / 상장예정 / 상장완료
    "updated_at",             # 마지막 갱신일시 (YYYY-MM-DD HH:MM:SS)
]


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------


def check_api_key() -> bool:
    """DART API 키가 설정되어 있는지 확인합니다."""
    if not DART_API_KEY:
        logger.error(
            "DART_API_KEY가 설정되지 않았습니다.\n"
            "  1. https://opendart.fss.or.kr 에서 API 키를 발급받으세요.\n"
            "  2. 프로젝트 루트의 .env.local 파일에 다음 줄을 추가하세요:\n"
            "     DART_API_KEY=<발급받은_키>"
        )
        return False
    logger.info("DART_API_KEY 확인 완료.")
    return True


# ---------------------------------------------------------------------------
# DART API 호출
# ---------------------------------------------------------------------------


def fetch_disclosure_list(
    start_dt: str,
    end_dt: str,
    page_no: int = 1,
    page_count: int = 100,
) -> dict:
    """
    DART 공시 목록 API를 호출합니다.
    pblntf_ty 파라미터는 DART API에서 지원하지 않아 전체 조회 후 report_nm으로 필터링합니다.

    Args:
        start_dt:   조회 시작일 (YYYYMMDD)
        end_dt:     조회 종료일 (YYYYMMDD)
        page_no:    페이지 번호 (1부터)
        page_count: 페이지당 항목 수 (최대 100)

    Returns:
        DART API 응답 JSON dict
    """
    url = f"{DART_BASE_URL}/list.json"
    params: dict = {
        "crtfc_key": DART_API_KEY,
        "pblntf_ty": DISCLOSURE_TYPE,  # C = 발행공시 (pblntf_detail_ty는 API가 무시)
        "bgn_de": start_dt,
        "end_de": end_dt,
        "page_no": page_no,
        "page_count": page_count,
    }
    logger.info(f"DART 공시 목록 조회: {start_dt}~{end_dt} (page {page_no})")
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_disclosure_list(data: dict) -> list[dict]:
    """
    DART 공시 목록 응답에서 공모주 기본 정보를 파싱합니다.

    Phase 1에서는 DART list API에서 제공하는 기본 필드만 추출합니다.
    상세 필드(청약일, 공모가 등)는 Phase 2에서 개별 공시 상세 파싱으로 보완합니다.

    Args:
        data: DART API 응답 dict

    Returns:
        공모주 기본 정보 목록 (list of dict, IPO_LIST_COLUMNS 기준)
    """
    status_code = data.get("status", "")
    if status_code != "000":
        message = data.get("message", "알 수 없는 오류")
        logger.warning(f"DART API 오류 응답: status={status_code}, message={message}")
        return []

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results: list[dict] = []

    for item in data.get("list", []):
        # 증권신고서(지분증권) 관련 항목만 필터링
        report_nm = item.get("report_nm", "")
        if not any(kw in report_nm for kw in IPO_REPORT_KEYWORDS):
            continue
        results.append(
            {
                "corp_code": item.get("corp_code", ""),
                "corp_name": item.get("corp_name", ""),
                "stock_code": item.get("stock_code", ""),
                "rcept_no": item.get("rcept_no", ""),
                "rcept_dt": item.get("rcept_dt", ""),
                "report_nm": item.get("report_nm", ""),
                # 아래 필드는 상세 파싱(Phase 2) 전까지 빈 값 유지
                "market": "",
                "subscription_start_dt": "",
                "subscription_end_dt": "",
                "listing_dt": "",
                "offering_price_low": "",
                "offering_price_high": "",
                "offering_price_final": "",
                "total_shares": "",
                "total_amount": "",
                "underwriter": "",
                "status": "정보수집중",
                "updated_at": now_str,
            }
        )

    return results


# ---------------------------------------------------------------------------
# CSV 입출력
# ---------------------------------------------------------------------------


def load_existing_ipo_list() -> pd.DataFrame:
    """기존 ipo_list.csv를 로드합니다. 없으면 빈 DataFrame을 반환합니다."""
    if IPO_LIST_PATH.exists():
        df = pd.read_csv(IPO_LIST_PATH, dtype=str)
        logger.info(f"기존 ipo_list.csv 로드 완료: {len(df)}건")
        return df
    logger.info("ipo_list.csv가 존재하지 않습니다. 새로 생성합니다.")
    return pd.DataFrame(columns=IPO_LIST_COLUMNS)


def upsert_ipo_list(existing: pd.DataFrame, new_items: list[dict]) -> pd.DataFrame:
    """
    기존 DataFrame에 새 항목을 병합(upsert)합니다.
    rcept_no를 기준으로 중복 시 새 데이터를 우선합니다.

    Args:
        existing:  기존 ipo_list DataFrame
        new_items: 새로 수집된 항목 목록

    Returns:
        병합된 DataFrame
    """
    if not new_items:
        logger.info("신규 수집 항목이 없습니다.")
        return existing

    new_df = pd.DataFrame(new_items, columns=IPO_LIST_COLUMNS)

    if existing.empty:
        logger.info(f"신규 항목 {len(new_df)}건으로 초기 DataFrame 생성")
        return new_df

    merged = pd.concat([existing, new_df], ignore_index=True)
    before_count = len(merged)
    merged = merged.drop_duplicates(subset=["rcept_no"], keep="last")
    after_count = len(merged)
    logger.info(
        f"upsert 완료: 전체 {after_count}건 "
        f"(중복 제거 {before_count - after_count}건, 신규 {len(new_df)}건)"
    )
    return merged


def save_ipo_list(df: pd.DataFrame) -> None:
    """DataFrame을 ipo_list.csv로 저장합니다."""
    df = df[IPO_LIST_COLUMNS]  # 컬럼 순서 보장
    df.to_csv(IPO_LIST_PATH, index=False, encoding="utf-8-sig")
    logger.info(f"ipo_list.csv 저장 완료: {IPO_LIST_PATH} (총 {len(df)}건)")


# ---------------------------------------------------------------------------
# Phase 2: 상세 정보 보완 (company API + document XML 파싱)
# ---------------------------------------------------------------------------


def fetch_company_info(corp_code: str) -> dict:
    """DART 기업 기본정보 API 호출."""
    url = f"{DART_BASE_URL}/company.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_market_from_corp(corp_code: str) -> str:
    """corp_code로 상장시장(KOSPI/KOSDAQ 등) 조회."""
    try:
        data = fetch_company_info(corp_code)
        if data.get("status") == "000":
            return CORP_CLS_MAP.get(data.get("corp_cls", ""), "")
    except Exception as exc:
        logger.debug(f"company API 실패 ({corp_code}): {exc}")
    return ""


def fetch_document_text(rcept_no: str) -> str:
    """DART document.xml ZIP을 다운로드하여 주 XML 파일의 텍스트를 반환합니다."""
    url = f"{DART_BASE_URL}/document.xml"
    params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()

    if resp.content[:2] != b"PK":  # ZIP 시그니처 확인
        logger.debug(f"document.xml: ZIP 아님 (rcept_no={rcept_no})")
        return ""

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                return ""
            xml_names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
            raw = zf.read(xml_names[0])
    except zipfile.BadZipFile:
        logger.debug(f"document.xml: 손상된 ZIP (rcept_no={rcept_no})")
        return ""

    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_kr_date(text: str) -> str:
    """한국어/점표기 날짜를 YYYYMMDD로 변환."""
    m = re.search(r"(\d{4})[년.\s]*(\d{1,2})[월.\s]*(\d{1,2})일?", text)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return ""


def _parse_num(text: str) -> str:
    """숫자+콤마 문자열에서 순수 정수 문자열 추출."""
    cleaned = re.sub(r"[,\s원]", "", text)
    m = re.search(r"\d+", cleaned)
    return m.group(0) if m else ""


def extract_offering_details(xml_text: str) -> dict:
    """
    DART 증권신고서 XML 텍스트에서 공모 상세 필드를 정규식으로 추출합니다.
    정정 문서는 날짜가 여러 번 나타나므로 findall로 마지막 매치를 사용합니다.
    """
    plain = re.sub(r"<[^>]+>", " ", xml_text)
    plain = re.sub(r"&[a-zA-Z#\d]+;", " ", plain)
    plain = re.sub(r"\s+", " ", plain)

    result: dict = {
        "subscription_start_dt": "",
        "subscription_end_dt": "",
        "listing_dt": "",
        "offering_price_low": "",
        "offering_price_high": "",
        "offering_price_final": "",
        "total_shares": "",
        "total_amount": "",
        "underwriter": "",
    }

    DATE_PAT = r"(\d{4}[년.\s]*\d{1,2}[월.\s]*\d{1,2}일?)"

    # 청약기간/청약기일: start ~ end
    # DART 문서는 '청약기일' 또는 '일반청약자 청약일' 형식을 사용
    sub_matches = re.findall(
        r"청약\s*(?:기간|기일)[^0-9]*" + DATE_PAT + r"[^0-9]*[~～\-]\s*" + DATE_PAT,
        plain,
    )
    if not sub_matches:
        sub_matches = re.findall(
            r"일반\s*청약\s*(?:자\s*)?청약\s*일[^0-9]*" + DATE_PAT + r"[^0-9]*[~～\-]\s*" + DATE_PAT,
            plain,
        )
    if sub_matches:
        last = sub_matches[-1]  # 정정 문서는 마지막 값이 최신
        result["subscription_start_dt"] = _parse_kr_date(last[0])
        result["subscription_end_dt"] = _parse_kr_date(last[1])

    # 상장예정일 (정정 문서는 두 번 나오므로 두 날짜 모두 캡처해 마지막 사용)
    listing_matches = re.findall(
        r"상장\s*예정\s*일[^0-9]*" + DATE_PAT, plain
    )
    if listing_matches:
        result["listing_dt"] = _parse_kr_date(listing_matches[-1])

    # 희망공모가 밴드: X원 ~ Y원 (마지막 매치 사용)
    price_matches = re.findall(
        r"희망\s*공모\s*가[격액]?\s*[:\s]*(\d[\d,]*)\s*원[^0-9]*[~～\-]\s*(\d[\d,]*)\s*원",
        plain,
    )
    if price_matches:
        last = price_matches[-1]
        result["offering_price_low"] = _parse_num(last[0])
        result["offering_price_high"] = _parse_num(last[1])

    # 확정공모가 (마지막 매치 사용)
    final_matches = re.findall(r"확정\s*공모\s*가[격액]?\s*[:\s]*(\d[\d,]*)\s*원", plain)
    if final_matches:
        result["offering_price_final"] = _parse_num(final_matches[-1])

    # 공모주식수
    shares_matches = re.findall(r"공모\s*주식[수]?\s*[:\s]*(\d[\d,]*)\s*주", plain)
    if shares_matches:
        result["total_shares"] = re.sub(r",", "", shares_matches[0])

    # 공모금액
    amount_matches = re.findall(r"공모\s*금액\s*[:\s]*(\d[\d,]+)\s*원", plain)
    if amount_matches:
        result["total_amount"] = re.sub(r",", "", amount_matches[0])

    # 대표주관회사 (공동대표주관회사 포함)
    under_match = re.search(
        r"(?:공동\s*)?대표\s*주관\s*회사\s+([가-힣A-Za-z][가-힣A-Za-z\s\(\)&㈜]+?)(?:\s+기명식|\s{2,}|[<\n]|$)",
        plain,
    )
    if under_match:
        result["underwriter"] = under_match.group(1).strip()

    return result


def enrich_ipo_items(df: pd.DataFrame) -> pd.DataFrame:
    """
    ipo_list DataFrame에서 market / 상세 필드가 비어있는 행을 보완합니다.
    - market: DART company API (corp_cls → KOSPI/KOSDAQ/KONEX)
    - 나머지: DART document.xml 정규식 파싱
    """
    df = df.copy()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    needs_market = df.index[df["market"].fillna("").eq("")].tolist()
    needs_detail = df.index[df["subscription_start_dt"].fillna("").eq("")].tolist()

    logger.info(
        f"보완 대상 - market: {len(needs_market)}건 / 상세: {len(needs_detail)}건"
    )

    # market 필드 보완 (company API)
    for idx in needs_market:
        corp_code = str(df.at[idx, "corp_code"]).strip()
        if not corp_code:
            continue
        market = get_market_from_corp(corp_code)
        if market:
            df.at[idx, "market"] = market
            logger.info(f"  market 확인: {df.at[idx, 'corp_name']} → {market}")
        time.sleep(API_DELAY)

    # 상세 필드 보완 (document.xml 파싱)
    for idx in needs_detail:
        rcept_no = str(df.at[idx, "rcept_no"]).strip()
        corp_name = str(df.at[idx, "corp_name"]).strip()
        if not rcept_no:
            continue
        logger.info(f"  문서 파싱: {corp_name} ({rcept_no})")
        try:
            xml_text = fetch_document_text(rcept_no)
            if xml_text:
                details = extract_offering_details(xml_text)
                for field, value in details.items():
                    if value and str(df.at[idx, field]).strip() in ("", "nan"):
                        df.at[idx, field] = value
                df.at[idx, "updated_at"] = now_str
                logger.info(
                    f"    청약: {details.get('subscription_start_dt')}~"
                    f"{details.get('subscription_end_dt')} / "
                    f"공모가: {details.get('offering_price_low')}~"
                    f"{details.get('offering_price_high')}원"
                )
        except requests.exceptions.RequestException as exc:
            logger.warning(f"    문서 다운로드 실패 ({rcept_no}): {exc}")
        time.sleep(API_DELAY)

    return df


# ---------------------------------------------------------------------------
# 수집 메인 로직
# ---------------------------------------------------------------------------


def collect_period(start_dt: str, end_dt: str) -> list[dict]:
    """단일 기간(최대 88일)의 공시를 페이지네이션으로 수집합니다."""
    all_items: list[dict] = []
    page_no = 1
    total_count = None

    while True:
        data = fetch_disclosure_list(start_dt, end_dt, page_no=page_no)

        if data.get("status") != "000":
            logger.warning(f"DART API 오류: {data.get('status')} {data.get('message')}")
            break

        if total_count is None:
            total_count = int(data.get("total_count", 0))
            logger.info(f"  기간 전체 공시: {total_count}건")

        items = parse_disclosure_list(data)
        all_items.extend(items)

        if page_no * 100 >= total_count:
            break
        page_no += 1

    return all_items


def collect_recent_ipos(days_back: int = 180) -> list[dict]:
    """
    최근 N일간의 증권신고서(지분증권) 공시를 수집합니다.
    DART API 90일 제한으로 인해 88일 단위로 분할 조회합니다.

    Args:
        days_back: 조회 시작 기준일 (오늘로부터 N일 전)

    Returns:
        수집된 공모주 기본 정보 목록
    """
    MAX_DAYS = 88
    all_items: list[dict] = []
    today = datetime.now()
    cutoff = today - timedelta(days=days_back)
    chunk_end = today

    while chunk_end > cutoff:
        chunk_start = max(chunk_end - timedelta(days=MAX_DAYS), cutoff)
        start_str = chunk_start.strftime("%Y%m%d")
        end_str = chunk_end.strftime("%Y%m%d")
        logger.info(f"구간 조회: {start_str} ~ {end_str}")
        items = collect_period(start_str, end_str)
        all_items.extend(items)
        logger.info(f"  IPO 수집: {len(items)}건 (누적: {len(all_items)}건)")
        chunk_end = chunk_start - timedelta(days=1)

    logger.info(f"총 {len(all_items)}건 수집 완료")
    return all_items


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


def run() -> int:
    """
    메인 실행 함수.

    Returns:
        성공 시 0, 실패 시 1
    """
    logger.info("=" * 50)
    logger.info("collect.py 시작")
    logger.info("=" * 50)

    if not check_api_key():
        return 1

    try:
        new_items = collect_recent_ipos(days_back=180)
    except requests.exceptions.ConnectionError:
        logger.error("네트워크 연결에 실패했습니다. 인터넷 연결 상태를 확인하세요.")
        return 1
    except requests.exceptions.Timeout:
        logger.error("DART API 요청이 시간 초과되었습니다. 잠시 후 다시 시도하세요.")
        return 1
    except requests.exceptions.RequestException as exc:
        logger.error(f"DART API 호출 실패: {exc}")
        return 1

    existing_df = load_existing_ipo_list()
    merged_df = upsert_ipo_list(existing_df, new_items)

    # Phase 2: market / 청약일 / 공모가 등 상세 필드 보완
    merged_df = enrich_ipo_items(merged_df)
    save_ipo_list(merged_df)

    logger.info("=" * 50)
    logger.info("collect.py 완료")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run())
