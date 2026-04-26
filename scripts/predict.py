"""
predict.py - Gemini API를 활용한 공모주 상장일 주가 예측

Usage:
    python scripts/predict.py

Required Environment Variables (.env.local):
    GEMINI_API_KEY: Google Gemini API 키
    발급: https://aistudio.google.com/apikey

무료 티어 모델 (우선순위 순):
    1. gemini-3-flash-preview  — 5 RPM / 20 RPD
    2. gemini-2.5-flash        — 5 RPM / 20 RPD
    3. gemini-2.5-flash-lite   — 10 RPM / 20 RPD
    쿼터 초과 시 다음 모델로 자동 전환. 총 최대 42건/일.
"""

import logging
import os
import sys
import time
from datetime import date, timedelta
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

# (model_id, rpm_limit, max_per_run)
# max_per_run = 14 → RPD 20에서 여유 버퍼 유지
GEMINI_MODELS: list[tuple[str, int, int]] = [
    ("gemini-3-flash-preview", 5,  14),
    ("gemini-2.5-flash",       5,  14),
    ("gemini-2.5-flash-lite",  10, 14),
]

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
    models_str = ", ".join(m[0] for m in GEMINI_MODELS)
    logger.info(f"Gemini API 초기화 완료 (모델: {models_str})")
    return True


# ---------------------------------------------------------------------------
# 예측 프롬프트 생성
# ---------------------------------------------------------------------------

def build_prediction_prompt(row: pd.Series) -> str:
    corp_name  = row.get("corp_name", "")
    price_low  = row.get("offering_price_low", "N/A")
    price_high = row.get("offering_price_high", "N/A")
    price_final = row.get("offering_price_final", "N/A")
    market     = row.get("market", "N/A")
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
            result["upside_pct"] = line.split(":", 1)[1].strip().replace("%", "")
        elif line.startswith("CONFIDENCE:"):
            result["confidence"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()
    return result


# ---------------------------------------------------------------------------
# 예측 실행 (단일 모델)
# ---------------------------------------------------------------------------

def predict_ipo(row: pd.Series, model_id: str) -> Optional[dict]:
    """
    단일 모델로 IPO 예측을 실행합니다.
    쿼터 초과(429/RESOURCE_EXHAUSTED) 시 예외를 re-raise하여
    호출자가 다음 모델로 전환하도록 합니다.
    """
    prompt = build_prediction_prompt(row)
    try:
        response = _gemini_client.models.generate_content(model=model_id, contents=prompt)
        parsed = parse_gemini_response(response.text)
        parsed["rcept_no"]     = str(row.get("rcept_no", ""))
        parsed["corp_name"]    = str(row.get("corp_name", ""))
        parsed["predicted_at"] = date.today().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[{model_id}] {row.get('corp_name')} 예측 완료: "
            f"종가 {parsed.get('predicted_first_day_close')}원, "
            f"상승률 {parsed.get('upside_pct')}%"
        )
        return parsed
    except Exception as exc:
        err = str(exc)
        if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
            raise  # 호출자가 모델 전환 처리
        logger.error(f"[{model_id}] {row.get('corp_name')} 예측 실패: {exc}")
        return None


# ---------------------------------------------------------------------------
# 예측 실행 (멀티 모델 폴백)
# ---------------------------------------------------------------------------

def run_predictions(ipo_df: pd.DataFrame) -> pd.DataFrame:
    """
    무료 티어 모델을 순서대로 사용하여 예측을 실행합니다.
    - 모델별 최대 14건/일 (RPD 20 한도에서 버퍼 유지)
    - 쿼터 초과 시 다음 우선순위 모델로 자동 전환
    - 상장 예정일이 가까운 항목을 우선 예측
    """
    if PREDICTIONS_PATH.exists():
        pred_df = pd.read_csv(PREDICTIONS_PATH, dtype=str)
    else:
        pred_df = pd.DataFrame(columns=PREDICTIONS_COLUMNS)

    existing_rcept_nos = set(pred_df["rcept_no"].tolist())

    targets = ipo_df[
        (ipo_df["status"].isin(["청약예정", "청약중", "상장예정"]))
        & (~ipo_df["rcept_no"].isin(existing_rcept_nos))
        & (
            ipo_df["offering_price_final"].notna() & (ipo_df["offering_price_final"] != "")
            | ipo_df["offering_price_high"].notna() & (ipo_df["offering_price_high"] != "")
        )
    ].copy()

    if targets.empty:
        logger.info("예측 대상 신규 항목이 없습니다.")
        return pred_df

    # 상장 예정일이 가까운 순 정렬 (없으면 맨 뒤)
    today_str = date.today().strftime("%Y%m%d")
    targets["_sort_key"] = targets["listing_dt"].apply(
        lambda x: x if x and x >= today_str else "99999999"
    )
    targets = targets.sort_values("_sort_key").drop(columns=["_sort_key"])

    logger.info(f"예측 대상: {len(targets)}건 (모델별 최대 {GEMINI_MODELS[0][2]}건)")

    new_predictions: list[dict] = []
    counts = [0] * len(GEMINI_MODELS)  # 모델별 이번 실행 카운트

    for _, row in targets.iterrows():
        # 사용 가능한 모델 찾기
        model_idx = next(
            (i for i, (_, _, mx) in enumerate(GEMINI_MODELS) if counts[i] < mx),
            None
        )
        if model_idx is None:
            total = sum(counts)
            logger.info(f"모든 모델 1일 할당량 소진 - 중단 (완료 {total}건, 잔여 {len(targets) - total}건은 내일 처리)")
            break

        model_id, rpm, _ = GEMINI_MODELS[model_idx]
        # RPM 제한: 분당 요청수에 맞춰 딜레이 (여유 2초 추가)
        sleep_sec = 60.0 / rpm + 2
        time.sleep(sleep_sec)

        try:
            result = predict_ipo(row, model_id)
            if result:
                new_predictions.append(result)
                counts[model_idx] += 1
        except Exception:
            # 쿼터 초과 → 이 모델은 더 이상 사용 불가로 처리
            logger.warning(f"[{model_id}] 쿼터 소진 - 다음 모델로 전환")
            counts[model_idx] = GEMINI_MODELS[model_idx][2]  # 한도로 표시

    if new_predictions:
        new_df = pd.DataFrame(new_predictions, columns=PREDICTIONS_COLUMNS)
        pred_df = pd.concat([pred_df, new_df], ignore_index=True)
        pred_df.to_csv(PREDICTIONS_PATH, index=False, encoding="utf-8-sig")
        model_summary = ", ".join(
            f"{GEMINI_MODELS[i][0].split('-')[1]}:{counts[i]}건"
            for i in range(len(GEMINI_MODELS)) if counts[i] > 0
        )
        logger.info(f"predictions.csv 저장: 총 {len(pred_df)}건 (신규 {len(new_df)}건 [{model_summary}])")

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
