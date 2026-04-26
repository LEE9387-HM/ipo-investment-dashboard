"""
analyze.py - 수집된 공모주 데이터 분석 및 상태 업데이트

Usage:
    python scripts/analyze.py

Required Environment Variables (.env.local):
    KIS_APP_KEY:    한국투자증권 앱키 (실제 주가 수집 시 필요)
    KIS_APP_SECRET: 한국투자증권 앱시크릿
    KIS_ENVIRONMENT: real 또는 virtual (기본값: real)
"""

import logging
import os
import sys
from datetime import datetime
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
logger = logging.getLogger("analyze")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR = PROJECT_ROOT / "data"

IPO_LIST_PATH = DATA_DIR / "ipo_list.csv"
RESULTS_PATH = DATA_DIR / "results.csv"
ACCURACY_LOG_PATH = DATA_DIR / "accuracy_log.csv"

# ---------------------------------------------------------------------------
# 데이터 스키마
# ---------------------------------------------------------------------------

RESULTS_COLUMNS: list[str] = [
    "rcept_no",             # DART 접수번호 (FK → ipo_list)
    "corp_name",            # 기업명
    "stock_code",           # 종목코드
    "listing_dt",           # 실제 상장일 (YYYYMMDD)
    "offering_price_final", # 확정 공모가 (원)
    "first_day_open",       # 상장일 시가 (원)
    "first_day_high",       # 상장일 고가 (원)
    "first_day_low",        # 상장일 저가 (원)
    "first_day_close",      # 상장일 종가 (원)
    "first_day_volume",     # 상장일 거래량 (주)
    "first_day_change_pct", # 공모가 대비 종가 변동률 (%)
    "collected_at",         # 수집일시
]

ACCURACY_LOG_COLUMNS: list[str] = [
    "log_dt",               # 로그 날짜 (YYYY-MM-DD)
    "total_predictions",    # 총 예측 건수
    "evaluated",            # 평가 완료 건수
    "within_10pct",         # 예측 오차 10% 이내 건수
    "within_20pct",         # 예측 오차 20% 이내 건수
    "mean_error_pct",       # 평균 오차율 (%)
    "accuracy_score",       # 종합 정확도 점수 (0-100)
]

# ---------------------------------------------------------------------------
# KIS API 설정
# ---------------------------------------------------------------------------

KIS_APP_KEY: str = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET: str = os.environ.get("KIS_APP_SECRET", "")
KIS_ENV: str = os.environ.get("KIS_ENVIRONMENT", "real")

KIS_BASE_URL = (
    "https://openapi.koreainvestment.com:9443"
    if KIS_ENV == "real"
    else "https://openapivts.koreainvestment.com:29443"
)

_kis_access_token: Optional[str] = None


def get_kis_access_token() -> Optional[str]:
    """KIS OAuth 접근 토큰을 발급합니다."""
    global _kis_access_token
    if _kis_access_token:
        return _kis_access_token

    if not KIS_APP_KEY or not KIS_APP_SECRET:
        logger.warning("KIS_APP_KEY 또는 KIS_APP_SECRET이 설정되지 않았습니다.")
        return None

    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        _kis_access_token = resp.json().get("access_token")
        logger.info("KIS 접근 토큰 발급 완료")
        return _kis_access_token
    except requests.exceptions.RequestException as exc:
        logger.error(f"KIS 토큰 발급 실패: {exc}")
        return None


def fetch_daily_ohlcv(stock_code: str, date: str) -> Optional[dict]:
    """
    KIS API로 특정 날짜의 OHLCV 데이터를 가져옵니다.

    Args:
        stock_code: 종목코드 (6자리)
        date:       조회 날짜 (YYYYMMDD)

    Returns:
        OHLCV dict 또는 None
    """
    token = get_kis_access_token()
    if not token:
        return None

    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010400",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": date,
        "FID_INPUT_DATE_2": date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output2", [])
        if output:
            row = output[0]
            return {
                "open": row.get("stck_oprc", ""),
                "high": row.get("stck_hgpr", ""),
                "low": row.get("stck_lwpr", ""),
                "close": row.get("stck_clpr", ""),
                "volume": row.get("acml_vol", ""),
            }
    except requests.exceptions.RequestException as exc:
        logger.error(f"KIS OHLCV 조회 실패 ({stock_code}, {date}): {exc}")
    return None


# ---------------------------------------------------------------------------
# 상태 업데이트 로직
# ---------------------------------------------------------------------------

def determine_status(row: pd.Series, today: str) -> str:
    """
    공모주의 현재 상태를 날짜 기준으로 산출합니다.

    Args:
        row:   ipo_list DataFrame의 단일 행
        today: 오늘 날짜 (YYYYMMDD)

    Returns:
        상태 문자열
    """
    listing_dt = str(row.get("listing_dt", "")).strip()
    sub_start = str(row.get("subscription_start_dt", "")).strip()
    sub_end = str(row.get("subscription_end_dt", "")).strip()

    if listing_dt and listing_dt <= today:
        return "상장완료"
    # 청약 날짜 체크를 listing_dt보다 먼저: 상장예정이어도 현재 청약중일 수 있음
    if sub_start and sub_end and sub_start <= today <= sub_end:
        return "청약중"
    if sub_end and sub_end < today:
        return "청약종료" if not (listing_dt and listing_dt > today) else "상장예정"
    if sub_start and sub_start > today:
        return "청약예정"
    if listing_dt and listing_dt > today:
        return "상장예정"
    return "정보수집중"


def update_ipo_statuses(df: pd.DataFrame) -> pd.DataFrame:
    """
    ipo_list DataFrame의 status 컬럼을 오늘 날짜 기준으로 갱신합니다.
    """
    today = datetime.now().strftime("%Y%m%d")
    df = df.copy()
    df["status"] = df.apply(lambda row: determine_status(row, today), axis=1)
    df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("IPO 상태 업데이트 완료")
    return df


# ---------------------------------------------------------------------------
# 상장 결과 수집 (KIS API)
# ---------------------------------------------------------------------------

def collect_listing_results(ipo_df: pd.DataFrame) -> pd.DataFrame:
    """
    상장완료 상태이면서 results.csv에 없는 항목의 실제 주가를 수집합니다.

    Args:
        ipo_df: 현재 ipo_list DataFrame

    Returns:
        업데이트된 results DataFrame
    """
    if RESULTS_PATH.exists():
        results_df = pd.read_csv(RESULTS_PATH, dtype=str)
    else:
        results_df = pd.DataFrame(columns=RESULTS_COLUMNS)

    existing_rcept_nos = set(results_df["rcept_no"].tolist())
    # stock_code 기준으로도 중복 방지 (동일 종목 여러 공시 차단)
    existing_stock_codes = set(
        results_df.loc[results_df["stock_code"].notna() & (results_df["stock_code"] != ""), "stock_code"].tolist()
    )
    listed = ipo_df[ipo_df["status"] == "상장완료"].copy()
    new_rows: list[dict] = []

    for _, row in listed.iterrows():
        rcept_no = str(row.get("rcept_no", ""))
        if rcept_no in existing_rcept_nos:
            continue

        stock_code = str(row.get("stock_code", "")).strip()
        listing_dt = str(row.get("listing_dt", "")).strip()

        if not stock_code or not listing_dt:
            logger.debug(f"{row.get('corp_name')}: 종목코드 또는 상장일 미확인, 건너뜀")
            continue

        # 이미 같은 종목코드로 수집된 결과가 있으면 건너뜀
        if stock_code in existing_stock_codes:
            logger.debug(f"{row.get('corp_name')} ({stock_code}): 결과 이미 존재 (stock_code 기준), 건너뜀")
            continue

        logger.info(f"{row.get('corp_name')} ({stock_code}) 상장일 데이터 수집 중...")
        ohlcv = fetch_daily_ohlcv(stock_code, listing_dt)

        offering_price = str(row.get("offering_price_final", "")).strip()
        change_pct = ""
        if ohlcv and offering_price and offering_price.isdigit():
            close = ohlcv.get("close", "")
            if close and str(close).isdigit():
                change_pct = f"{(int(close) - int(offering_price)) / int(offering_price) * 100:.2f}"

        new_rows.append(
            {
                "rcept_no": rcept_no,
                "corp_name": row.get("corp_name", ""),
                "stock_code": stock_code,
                "listing_dt": listing_dt,
                "offering_price_final": offering_price,
                "first_day_open": ohlcv.get("open", "") if ohlcv else "",
                "first_day_high": ohlcv.get("high", "") if ohlcv else "",
                "first_day_low": ohlcv.get("low", "") if ohlcv else "",
                "first_day_close": ohlcv.get("close", "") if ohlcv else "",
                "first_day_volume": ohlcv.get("volume", "") if ohlcv else "",
                "first_day_change_pct": change_pct,
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=RESULTS_COLUMNS)
        results_df = pd.concat([results_df, new_df], ignore_index=True)
        results_df.to_csv(RESULTS_PATH, index=False, encoding="utf-8-sig")
        logger.info(f"results.csv 갱신 완료: {len(results_df)}건 (신규 {len(new_rows)}건)")
    else:
        logger.info("수집할 신규 상장 결과가 없습니다.")

    return results_df


# ---------------------------------------------------------------------------
# 예측 정확도 계산
# ---------------------------------------------------------------------------

def calculate_accuracy(results_df: pd.DataFrame) -> None:
    """
    predictions.csv와 results.csv를 비교하여 accuracy_log.csv를 갱신합니다.
    """
    predictions_path = DATA_DIR / "predictions.csv"
    if not predictions_path.exists() or results_df.empty:
        logger.info("예측 데이터 또는 결과 데이터가 없어 정확도 계산 건너뜀")
        return

    pred_df = pd.read_csv(predictions_path, dtype=str)
    if pred_df.empty:
        return

    merged = pd.merge(
        pred_df[["rcept_no", "predicted_first_day_close"]],
        results_df[["rcept_no", "first_day_close"]],
        on="rcept_no",
        how="inner",
    )

    evaluated = len(merged)
    if evaluated == 0:
        logger.info("평가 가능한 예측 결과가 없습니다.")
        return

    errors: list[float] = []
    within_10 = 0
    within_20 = 0

    for _, row in merged.iterrows():
        pred = str(row["predicted_first_day_close"]).strip()
        actual = str(row["first_day_close"]).strip()
        if pred.replace(".", "").isdigit() and actual.replace(".", "").isdigit():
            p, a = float(pred), float(actual)
            if a > 0:
                err = abs(p - a) / a * 100
                errors.append(err)
                if err <= 10:
                    within_10 += 1
                if err <= 20:
                    within_20 += 1

    mean_err = sum(errors) / len(errors) if errors else 0.0
    accuracy_score = max(0, 100 - mean_err)

    log_row = {
        "log_dt": datetime.now().strftime("%Y-%m-%d"),
        "total_predictions": len(pred_df),
        "evaluated": evaluated,
        "within_10pct": within_10,
        "within_20pct": within_20,
        "mean_error_pct": f"{mean_err:.2f}",
        "accuracy_score": f"{accuracy_score:.2f}",
    }

    if ACCURACY_LOG_PATH.exists():
        acc_df = pd.read_csv(ACCURACY_LOG_PATH, dtype=str)
    else:
        acc_df = pd.DataFrame(columns=ACCURACY_LOG_COLUMNS)

    acc_df = pd.concat(
        [acc_df, pd.DataFrame([log_row], columns=ACCURACY_LOG_COLUMNS)],
        ignore_index=True,
    )
    acc_df = acc_df.drop_duplicates(subset=["log_dt"], keep="last")
    acc_df.to_csv(ACCURACY_LOG_PATH, index=False, encoding="utf-8-sig")
    logger.info(
        f"accuracy_log.csv 갱신: 평가 {evaluated}건, "
        f"평균 오차 {mean_err:.2f}%, 정확도 점수 {accuracy_score:.2f}"
    )


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def run() -> int:
    logger.info("=" * 50)
    logger.info("analyze.py 시작")
    logger.info("=" * 50)

    if not IPO_LIST_PATH.exists():
        logger.error(f"ipo_list.csv가 없습니다: {IPO_LIST_PATH}")
        logger.error("먼저 python scripts/collect.py 를 실행하세요.")
        return 1

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str)
    logger.info(f"ipo_list.csv 로드: {len(ipo_df)}건")

    ipo_df = update_ipo_statuses(ipo_df)
    ipo_df.to_csv(IPO_LIST_PATH, index=False, encoding="utf-8-sig")
    logger.info("ipo_list.csv 상태 갱신 완료")

    results_df = collect_listing_results(ipo_df)
    calculate_accuracy(results_df)

    logger.info("=" * 50)
    logger.info("analyze.py 완료")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run())
