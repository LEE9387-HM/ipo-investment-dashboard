"""
predict.py - Gemini API를 활용한 공모주 상장일 주가 예측

Usage:
    python scripts/predict.py

Required Environment Variables (.env.local):
    GEMINI_API_KEY: Google Gemini API 키
    발급: https://aistudio.google.com/apikey
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from google import genai
import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("predict")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR = PROJECT_ROOT / "data"
IPO_LIST_PATH = DATA_DIR / "ipo_list.csv"
PREDICTIONS_PATH = DATA_DIR / "predictions.csv"

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash"

# ---------------------------------------------------------------------------
# 데이터 스키마
# ---------------------------------------------------------------------------

PREDICTIONS_COLUMNS: list[str] = [
    "rcept_no",                  # DART 접수번호 (FK → ipo_list)
    "corp_name",                 # 기업명
    "predicted_first_day_close", # 예측 상장일 종가 (원)
    "predicted_first_day_high",  # 예측 상장일 고가 (원)
    "upside_pct",                # 공모가 대비 예측 상승률 (%)
    "confidence",                # 신뢰도 (1: 낮음 / 2: 보통 / 3: 높음)
    "reasoning",                 # 예측 근거 요약
    "predicted_at",              # 예측 생성일시
]


# ---------------------------------------------------------------------------
# Gemini API 초기화
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None


def init_gemini() -> bool:
    """Gemini API를 초기화합니다."""
    global _gemini_client
    if not GEMINI_API_KEY:
        logger.error(
            "GEMINI_API_KEY가 설정되지 않았습니다.\n"
            "  .env.local 에 GEMINI_API_KEY=<키> 를 추가하세요.\n"
            "  발급: https://aistudio.google.com/apikey"
        )
        return False
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Gemini API 초기화 완료")
    return True


# ---------------------------------------------------------------------------
# 예측 프롬프트 생성
# ---------------------------------------------------------------------------

def build_prediction_prompt(row: pd.Series) -> str:
    """
    공모주 정보를 기반으로 Gemini 예측 프롬프트를 생성합니다.

    Args:
        row: ipo_list DataFrame의 단일 행

    Returns:
        Gemini에 전달할 프롬프트 문자열
    """
    corp_name = row.get("corp_name", "")
    price_low = row.get("offering_price_low", "N/A")
    price_high = row.get("offering_price_high", "N/A")
    price_final = row.get("offering_price_final", "N/A")
    market = row.get("market", "N/A")
    underwriter = row.get("underwriter", "N/A")
    listing_dt = row.get("listing_dt", "N/A")
    total_amount = row.get("total_amount", "N/A")

    effective_price = price_final if price_final and price_final not in ("", "N/A") else price_high

    return f"""당신은 한국 주식시장 전문 애널리스트입니다.
다음 공모주의 상장일(첫 거래일) 주가를 예측해주세요.

## 기업 정보
- 기업명: {corp_name}
- 상장 시장: {market}
- 공모가 희망밴드: {price_low}원 ~ {price_high}원
- 확정 공모가: {price_final}원
- 공모 금액: {total_amount}원
- 주관사: {underwriter}
- 상장 예정일: {listing_dt}

## 분석 요청
위 정보와 현재 한국 주식 시장 환경을 고려하여 다음 형식으로 예측해주세요.
추측이 불가한 항목은 "-"로 표기하세요.

## 출력 형식 (반드시 아래 형식으로만 응답)
PREDICTED_CLOSE: [예측 종가, 숫자만]
PREDICTED_HIGH: [예측 고가, 숫자만]
UPSIDE_PCT: [공모가({effective_price}원) 대비 예측 종가 상승률, 소수점 1자리]
CONFIDENCE: [1(낮음)/2(보통)/3(높음) 중 하나]
REASONING: [예측 근거를 2-3문장으로 요약, 한국어]
"""


def parse_gemini_response(text: str) -> dict:
    """
    Gemini 응답 텍스트를 파싱합니다.

    Args:
        text: Gemini 응답 문자열

    Returns:
        파싱된 예측 결과 dict
    """
    result: dict = {
        "predicted_first_day_close": "",
        "predicted_first_day_high": "",
        "upside_pct": "",
        "confidence": "",
        "reasoning": "",
    }

    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("PREDICTED_CLOSE:"):
            val = line.split(":", 1)[1].strip().replace(",", "").replace("원", "")
            result["predicted_first_day_close"] = val
        elif line.startswith("PREDICTED_HIGH:"):
            val = line.split(":", 1)[1].strip().replace(",", "").replace("원", "")
            result["predicted_first_day_high"] = val
        elif line.startswith("UPSIDE_PCT:"):
            val = line.split(":", 1)[1].strip().replace("%", "")
            result["upside_pct"] = val
        elif line.startswith("CONFIDENCE:"):
            result["confidence"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()

    return result


# ---------------------------------------------------------------------------
# 예측 실행
# ---------------------------------------------------------------------------

def predict_ipo(row: pd.Series, client: genai.Client) -> Optional[dict]:
    """
    단일 공모주에 대해 Gemini 예측을 실행합니다.

    Args:
        row:    ipo_list DataFrame의 단일 행
        client: Gemini 클라이언트 인스턴스

    Returns:
        예측 결과 dict 또는 None (실패 시)
    """
    prompt = build_prediction_prompt(row)
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        parsed = parse_gemini_response(response.text)
        parsed["rcept_no"] = str(row.get("rcept_no", ""))
        parsed["corp_name"] = str(row.get("corp_name", ""))
        parsed["predicted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"{row.get('corp_name')} 예측 완료: "
            f"종가 {parsed.get('predicted_first_day_close')}원, "
            f"상승률 {parsed.get('upside_pct')}%"
        )
        return parsed
    except Exception as exc:
        logger.error(f"{row.get('corp_name')} 예측 실패: {exc}")
        return None


def run_predictions(ipo_df: pd.DataFrame) -> pd.DataFrame:
    """
    예측 대상 IPO 목록에 대해 Gemini 예측을 실행합니다.
    이미 예측된 항목(rcept_no 기준)은 건너뜁니다.
    """
    if PREDICTIONS_PATH.exists():
        pred_df = pd.read_csv(PREDICTIONS_PATH, dtype=str)
    else:
        pred_df = pd.DataFrame(columns=PREDICTIONS_COLUMNS)

    existing_rcept_nos = set(pred_df["rcept_no"].tolist())

    # 예측 대상: 청약예정 또는 청약중이면서 공모가 정보가 있는 항목
    targets = ipo_df[
        (ipo_df["status"].isin(["청약예정", "청약중", "상장예정"]))
        & (~ipo_df["rcept_no"].isin(existing_rcept_nos))
        & (
            ipo_df["offering_price_final"].notna()
            | ipo_df["offering_price_high"].notna()
        )
    ]

    if targets.empty:
        logger.info("예측 대상 신규 항목이 없습니다.")
        return pred_df

    logger.info(f"예측 대상: {len(targets)}건")

    new_predictions: list[dict] = []

    for _, row in targets.iterrows():
        result = predict_ipo(row, _gemini_client)
        if result:
            new_predictions.append(result)

    if new_predictions:
        new_df = pd.DataFrame(new_predictions, columns=PREDICTIONS_COLUMNS)
        pred_df = pd.concat([pred_df, new_df], ignore_index=True)
        pred_df.to_csv(PREDICTIONS_PATH, index=False, encoding="utf-8-sig")
        logger.info(f"predictions.csv 저장: 총 {len(pred_df)}건 (신규 {len(new_df)}건)")

    return pred_df


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def run() -> int:
    logger.info("=" * 50)
    logger.info("predict.py 시작")
    logger.info("=" * 50)

    if not init_gemini():
        return 1

    if not IPO_LIST_PATH.exists():
        logger.error("ipo_list.csv가 없습니다. 먼저 collect.py → analyze.py 를 실행하세요.")
        return 1

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str)
    logger.info(f"ipo_list.csv 로드: {len(ipo_df)}건")

    run_predictions(ipo_df)

    logger.info("=" * 50)
    logger.info("predict.py 완료")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run())
