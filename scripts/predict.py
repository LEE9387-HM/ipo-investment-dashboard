"""
predict.py - Gemini API를 활용한 공모주 상장일 주가 예측

Usage:
    python scripts/predict.py

Required Environment Variables (.env.local):
    GEMINI_API_KEY: Google Gemini API 키
    발급: https://aistudio.google.com/apikey

무료 티어 모델 (우선순위 순):
    1. gemini-2.5-flash-preview-04-17  — 10 RPM / 500 RPD (free)
    2. gemini-2.5-flash                — 10 RPM / 500 RPD (free)
    3. gemini-2.0-flash                — 15 RPM / 1500 RPD (free)

개선 사항 (TradingAgents 참조):
    - Bull/Bear 양방향 분석 → JSON 구조화 출력
    - 1차 정량 필터 (경쟁률 < 100, 청약 14일 초과는 스킵)
    - 주관사 과거 실적 컨텍스트 주입
    - KOSPI/KOSDAQ 시장 컨텍스트 주입
    - 수요예측 데이터(경쟁률·확약) 프롬프트 포함
"""

import json
import logging
import os
import re
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
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
        if hasattr(sys.stdout, "fileno") else sys.stdout
    )],
)
logger = logging.getLogger("predict")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR               = PROJECT_ROOT / "data"
DETAILS_DIR            = DATA_DIR / "details"
IPO_LIST_PATH          = DATA_DIR / "ipo_list.csv"
PREDICTIONS_PATH       = DATA_DIR / "predictions.csv"
UNDERWRITER_STATS_PATH = DATA_DIR / "underwriter_stats.csv"

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

# (model_id, rpm_limit, max_per_run)
GEMINI_MODELS: list[tuple[str, int, int]] = [
    ("gemini-2.5-flash-preview-04-17", 10, 20),
    ("gemini-2.5-flash",               10, 20),
    ("gemini-2.0-flash",               15, 20),
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
    "bull_points",               # 매수 근거 (|로 구분)
    "bear_points",               # 보류 근거 (|로 구분)
    "reasoning",                 # 종합 판단 요약
    "predicted_at",              # 예측 생성일시
]


# ---------------------------------------------------------------------------
# Gemini API 초기화
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None


def init_gemini() -> bool:
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
# 보조 데이터 로더
# ---------------------------------------------------------------------------

def load_detail(rcept_no: str) -> dict:
    """data/details/{rcept_no}.json 로드. 없으면 빈 dict."""
    path = DETAILS_DIR / f"{rcept_no}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_underwriter_stats() -> dict:
    """underwriter_stats.csv → {주관사명: {count, mean_return_pct, positive_pct}} 반환."""
    if not UNDERWRITER_STATS_PATH.exists():
        return {}
    try:
        df = pd.read_csv(UNDERWRITER_STATS_PATH, dtype=str)
        result = {}
        for _, row in df.iterrows():
            uw = str(row.get("underwriter", "")).strip()
            if uw:
                result[uw] = {
                    "count":           row.get("count", "0"),
                    "mean_return_pct": row.get("mean_return_pct", ""),
                    "positive_pct":    row.get("positive_pct", ""),
                }
        return result
    except Exception:
        return {}


def fetch_market_context() -> str:
    """KOSPI/KOSDAQ 최근 5거래일 등락률 문자열 반환. yfinance 필요."""
    try:
        import yfinance as yf
        parts = []
        for ticker, name in [("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ")]:
            hist = yf.Ticker(ticker).history(period="5d")["Close"]
            if len(hist) >= 2:
                chg = (hist.iloc[-1] / hist.iloc[0] - 1) * 100
                parts.append(f"{name} 최근5일 {chg:+.1f}%")
        return ", ".join(parts) if parts else ""
    except Exception as exc:
        logger.debug(f"시장 컨텍스트 조회 실패: {exc}")
        return ""


# ---------------------------------------------------------------------------
# 1차 정량 필터 (Gap 3)
# ---------------------------------------------------------------------------

def should_predict(row: pd.Series, detail: dict) -> bool:
    """
    LLM 호출 가치 여부를 판단합니다.
      - 청약 종료 후 종목 제외
      - 청약 시작 14일 초과 종목 제외 (정보 부족)
      - 경쟁률 데이터가 있고 100:1 미만이면 제외
    """
    today_str  = date.today().strftime("%Y%m%d")
    limit_str  = (date.today() + timedelta(days=14)).strftime("%Y%m%d")
    sub_start  = str(row.get("subscription_start_dt", "") or "").strip()
    sub_end    = str(row.get("subscription_end_dt",   "") or "").strip()

    if sub_end and sub_end < today_str:
        return False
    if sub_start and sub_start > limit_str:
        return False

    ratio_str = str(detail.get("competition_ratio", "") or "").strip()
    if ratio_str:
        try:
            if int(ratio_str.replace(",", "")) < 100:
                logger.info(f"  경쟁률 {ratio_str}:1 < 100 → 예측 제외")
                return False
        except ValueError:
            pass

    return True


# ---------------------------------------------------------------------------
# 프롬프트 생성 (Gap 1 + 4 + 5)
# ---------------------------------------------------------------------------

def _uw_context_line(underwriter: str, uw_stats: dict) -> str:
    """주관사 과거 실적 한 줄 요약."""
    if not underwriter or underwriter == "N/A":
        return ""
    for k, v in uw_stats.items():
        if underwriter in k or k in underwriter:
            return (
                f"- 주관사 과거 실적: 평균 수익률 {v['mean_return_pct']}%"
                f" (수익 비율 {v['positive_pct']}%, {v['count']}건 기준)"
            )
    return "- 주관사 과거 실적: 집계 데이터 없음"


def _community_sentiment_section(detail: dict) -> str:
    """커뮤니티/블로그 뉴스 헤드라인을 프롬프트 섹션으로 변환합니다."""
    items = detail.get("community_news", []) or []
    if not items:
        return ""
    lines = ["## 커뮤니티·블로그 반응 (최신 순)"]
    for item in items[:6]:
        title = item.get("title", "").replace("<b>", "").replace("</b>", "")
        source = item.get("source", "")
        desc = item.get("description", "").replace("<b>", "").replace("</b>", "")
        snippet = f"[{source}] {title}"
        if desc:
            snippet += f" — {desc[:80]}"
        lines.append(f"- {snippet}")
    return "\n".join(lines)


def build_prediction_prompt(
    row: pd.Series,
    detail: dict,
    uw_stats: dict,
    market_ctx: str,
) -> str:
    corp_name    = str(row.get("corp_name",            "") or "N/A")
    price_low    = str(row.get("offering_price_low",   "") or "N/A")
    price_high   = str(row.get("offering_price_high",  "") or "N/A")
    price_final  = str(row.get("offering_price_final", "") or "N/A")
    market       = str(row.get("market",               "") or "N/A")
    underwriter  = str(row.get("underwriter",          "") or "N/A")
    listing_dt   = str(row.get("listing_dt",           "") or "N/A")
    total_amount = str(row.get("total_amount",         "") or "N/A")

    effective_price = (
        price_final if price_final not in ("", "N/A", "nan")
        else price_high
    )

    # 수요예측 상세 (details.json)
    competition_ratio = str(detail.get("competition_ratio", "") or "").strip()
    lock_up_ratio     = str(detail.get("lock_up_ratio",     "") or "").strip()
    business_summary  = str(detail.get("business_summary",  "") or "").strip()

    demand_lines = []
    if competition_ratio:
        demand_lines.append(f"- 수요예측 경쟁률: {competition_ratio}:1")
    if lock_up_ratio:
        demand_lines.append(f"- 의무보유확약 비율: {lock_up_ratio}%")
    if business_summary:
        demand_lines.append(f"- 사업 개요: {business_summary[:200]}")
    demand_section = "\n".join(demand_lines) if demand_lines else "- 수요예측 상세 정보 미수집"

    uw_line          = _uw_context_line(underwriter, uw_stats)
    market_line      = f"- 시장 환경: {market_ctx}" if market_ctx else ""
    context_section  = "\n".join(filter(None, [market_line, uw_line])) or "- 컨텍스트 데이터 없음"
    community_section = _community_sentiment_section(detail)

    return f"""당신은 한국 공모주 전문 애널리스트입니다.
다음 공모주의 상장일(첫 거래일) 주가를 분석하고 예측하세요.

## 기업 정보
- 기업명: {corp_name}
- 상장 시장: {market}
- 공모가 희망밴드: {price_low}원 ~ {price_high}원
- 확정 공모가: {price_final}원
- 공모 금액: {total_amount}원
- 주관사: {underwriter}
- 상장 예정일: {listing_dt}

## 수요예측 결과
{demand_section}

## 시장·주관사 컨텍스트
{context_section}

{community_section + chr(10) if community_section else ""}## 분석 지시
Bull Case(청약 근거)와 Bear Case(보류 근거)를 각각 도출한 뒤 종합 판단하세요.

반드시 아래 JSON 형식으로만 응답하세요 (코드블록·추가 텍스트 없이):
{{
  "predicted_close": 숫자,
  "predicted_high": 숫자,
  "upside_pct": 소수점1자리,
  "confidence": 1또는2또는3,
  "bull_points": ["매수근거1", "매수근거2"],
  "bear_points": ["보류근거1", "보류근거2"],
  "reasoning": "종합판단 2-3문장"
}}

confidence: 1=정보부족/불확실, 2=보통, 3=수요예측·데이터 충분
upside_pct = (predicted_close - {effective_price}) / {effective_price} * 100 (공모가 {effective_price}원 기준)
"""


# ---------------------------------------------------------------------------
# 응답 파싱 (JSON 우선, 구형 텍스트 포맷 fallback)
# ---------------------------------------------------------------------------

def parse_gemini_response(text: str) -> dict:
    result: dict = {
        "predicted_first_day_close": "",
        "predicted_first_day_high":  "",
        "upside_pct":    "",
        "confidence":    "",
        "bull_points":   "",
        "bear_points":   "",
        "reasoning":     "",
    }

    # 코드블록 제거 후 JSON 추출
    clean = re.sub(r"```(?:json)?\s*", "", text.strip()).strip().rstrip("`").strip()
    m = re.search(r"\{[\s\S]+\}", clean)
    if m:
        try:
            data = json.loads(m.group())
            result["predicted_first_day_close"] = str(data.get("predicted_close", ""))
            result["predicted_first_day_high"]  = str(data.get("predicted_high",  ""))
            result["upside_pct"]  = str(data.get("upside_pct",  ""))
            result["confidence"]  = str(data.get("confidence",  ""))
            result["bull_points"] = "|".join(str(p) for p in data.get("bull_points", []))
            result["bear_points"] = "|".join(str(p) for p in data.get("bear_points", []))
            result["reasoning"]   = str(data.get("reasoning",   ""))
            return result
        except (json.JSONDecodeError, TypeError):
            logger.debug("JSON 파싱 실패, 구형 텍스트 포맷으로 fallback")

    # fallback: 기존 KEY: VALUE 형식
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("PREDICTED_CLOSE:"):
            result["predicted_first_day_close"] = (
                line.split(":", 1)[1].strip().replace(",", "").replace("원", "")
            )
        elif line.startswith("PREDICTED_HIGH:"):
            result["predicted_first_day_high"] = (
                line.split(":", 1)[1].strip().replace(",", "").replace("원", "")
            )
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

def predict_ipo(
    row: pd.Series,
    model_id: str,
    detail: dict,
    uw_stats: dict,
    market_ctx: str,
) -> Optional[dict]:
    """
    단일 모델로 IPO 예측을 실행합니다.
    쿼터 초과(429/RESOURCE_EXHAUSTED) 시 re-raise → 호출자가 다음 모델로 전환합니다.
    """
    prompt = build_prediction_prompt(row, detail, uw_stats, market_ctx)
    try:
        response = _gemini_client.models.generate_content(model=model_id, contents=prompt)
        parsed = parse_gemini_response(response.text)
        parsed["rcept_no"]     = str(row.get("rcept_no", ""))
        parsed["corp_name"]    = str(row.get("corp_name", ""))
        parsed["predicted_at"] = date.today().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[{model_id}] {row.get('corp_name')} 예측 완료: "
            f"종가 {parsed.get('predicted_first_day_close')}원 "
            f"(+{parsed.get('upside_pct')}%), 신뢰도 {parsed.get('confidence')}"
        )
        return parsed
    except Exception as exc:
        err = str(exc)
        if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
            raise
        logger.error(f"[{model_id}] {row.get('corp_name')} 예측 실패: {exc}")
        return None


# ---------------------------------------------------------------------------
# 예측 실행 (멀티 모델 폴백)
# ---------------------------------------------------------------------------

def run_predictions(ipo_df: pd.DataFrame) -> pd.DataFrame:
    """
    무료 티어 모델을 순서대로 사용하여 예측을 실행합니다.
    - 1차 정량 필터 적용 (경쟁률·청약일 기준)
    - 수요예측 상세, 주관사 실적, 시장 컨텍스트를 프롬프트에 주입
    - 쿼터 초과 시 다음 우선순위 모델로 자동 전환
    """
    if PREDICTIONS_PATH.exists():
        pred_df = pd.read_csv(PREDICTIONS_PATH, dtype=str)
        # 컬럼 스키마 업데이트 (bull/bear_points 신규 컬럼 대응)
        for col in PREDICTIONS_COLUMNS:
            if col not in pred_df.columns:
                pred_df[col] = ""
    else:
        pred_df = pd.DataFrame(columns=PREDICTIONS_COLUMNS)

    existing_rcept_nos = set(pred_df["rcept_no"].tolist())
    today_str = date.today().strftime("%Y%m%d")

    # 상태 기반 + 날짜 기반 대상 선별
    def _is_active(row: pd.Series) -> bool:
        lst = str(row.get("listing_dt", "") or "").strip()
        ss  = str(row.get("subscription_start_dt", "") or "").strip()
        se  = str(row.get("subscription_end_dt",   "") or "").strip()
        if lst and lst <= today_str:
            return False
        if se and se < today_str and not lst:
            return False
        return True

    has_price = (
        (ipo_df["offering_price_final"].fillna("") != "")
        | (ipo_df["offering_price_high"].fillna("") != "")
    )
    targets = ipo_df[
        has_price
        & (~ipo_df["rcept_no"].isin(existing_rcept_nos))
        & ipo_df.apply(_is_active, axis=1)
    ].copy()

    if targets.empty:
        logger.info("예측 대상 신규 항목이 없습니다.")
        return pred_df

    # 상장 예정일이 가까운 순 정렬
    targets["_sort_key"] = targets["listing_dt"].apply(
        lambda x: x if x and str(x).strip() >= today_str else "99999999"
    )
    targets = targets.sort_values("_sort_key").drop(columns=["_sort_key"])

    # 공유 컨텍스트 1회 로드
    uw_stats   = load_underwriter_stats()
    market_ctx = fetch_market_context()
    logger.info(f"시장 컨텍스트: {market_ctx or '조회 실패'}")
    logger.info(f"주관사 통계: {len(uw_stats)}개 로드")
    logger.info(f"예측 대상(가격 보유·미예측): {len(targets)}건")

    new_predictions: list[dict] = []
    counts    = [0] * len(GEMINI_MODELS)
    skipped   = 0

    for _, row in targets.iterrows():
        rcept_no = str(row.get("rcept_no", "")).strip()
        detail   = load_detail(rcept_no)

        # 1차 정량 필터
        if not should_predict(row, detail):
            skipped += 1
            continue

        # 사용 가능한 모델 선택
        model_idx = next(
            (i for i, (_, _, mx) in enumerate(GEMINI_MODELS) if counts[i] < mx),
            None,
        )
        if model_idx is None:
            logger.info(f"모든 모델 할당량 소진 - 중단 (완료 {sum(counts)}건, 잔여는 내일 처리)")
            break

        model_id, rpm, _ = GEMINI_MODELS[model_idx]
        time.sleep(60.0 / rpm + 1)

        try:
            result = predict_ipo(row, model_id, detail, uw_stats, market_ctx)
            if result:
                new_predictions.append(result)
                counts[model_idx] += 1
        except Exception:
            logger.warning(f"[{model_id}] 쿼터 소진 - 다음 모델로 전환")
            counts[model_idx] = GEMINI_MODELS[model_idx][2]

    if skipped:
        logger.info(f"1차 필터 제외: {skipped}건")

    if new_predictions:
        new_df  = pd.DataFrame(new_predictions, columns=PREDICTIONS_COLUMNS)
        pred_df = pd.concat([pred_df, new_df], ignore_index=True)
        pred_df.to_csv(PREDICTIONS_PATH, index=False, encoding="utf-8-sig")
        model_summary = ", ".join(
            f"{GEMINI_MODELS[i][0].split('-')[1]}:{counts[i]}건"
            for i in range(len(GEMINI_MODELS)) if counts[i] > 0
        )
        logger.info(
            f"predictions.csv 저장: 총 {len(pred_df)}건 "
            f"(신규 {len(new_df)}건 [{model_summary}])"
        )

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

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str).fillna("")
    logger.info(f"ipo_list.csv 로드: {len(ipo_df)}건")

    run_predictions(ipo_df)

    logger.info("=" * 50)
    logger.info("predict.py 완료")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run())
