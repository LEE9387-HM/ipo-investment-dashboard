"""
notify.py - Telegram 봇을 통한 공모주 일정 알림 전송

Usage:
    python scripts/notify.py

Required Environment Variables (.env.local):
    TELEGRAM_BOT_TOKEN: 텔레그램 봇 토큰
    TELEGRAM_CHAT_ID:   텔레그램 채팅 ID
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

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
logger = logging.getLogger("notify")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR = PROJECT_ROOT / "data"
IPO_LIST_PATH = DATA_DIR / "ipo_list.csv"
PREDICTIONS_PATH = DATA_DIR / "predictions.csv"
ACCURACY_LOG_PATH = DATA_DIR / "accuracy_log.csv"

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# Telegram 전송
# ---------------------------------------------------------------------------

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Telegram 메시지를 전송합니다.

    Args:
        text:       전송할 메시지 (HTML 마크업 지원)
        parse_mode: "HTML" 또는 "Markdown"

    Returns:
        성공 여부
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 - 알림 건너뜀")
        return True

    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram 메시지 전송 성공")
        return True
    except requests.exceptions.RequestException as exc:
        logger.error(f"Telegram 메시지 전송 실패: {exc}")
        return False


# ---------------------------------------------------------------------------
# 메시지 포맷터
# ---------------------------------------------------------------------------

def format_daily_briefing(
    ipo_df: pd.DataFrame,
    pred_df: pd.DataFrame,
) -> str:
    """
    일일 공모주 브리핑 메시지를 생성합니다.
    """
    today = datetime.now().strftime("%Y%m%d")
    today_display = datetime.now().strftime("%Y년 %m월 %d일")

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
    in_3days = (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")

    lines = [
        f"📊 <b>공모주 일일 브리핑</b>",
        f"<i>{today_display}</i>",
        "",
    ]

    # 오늘 청약 시작
    today_sub_start = ipo_df[ipo_df["subscription_start_dt"] == today]
    if not today_sub_start.empty:
        lines.append("🔔 <b>오늘 청약 시작</b>")
        for _, row in today_sub_start.iterrows():
            price = row.get("offering_price_final") or row.get("offering_price_high") or "미정"
            lines.append(
                f"  • {row['corp_name']} | 공모가 {price}원 "
                f"({row.get('market', '')})"
            )
        lines.append("")

    # 오늘 청약 마감
    today_sub_end = ipo_df[ipo_df["subscription_end_dt"] == today]
    if not today_sub_end.empty:
        lines.append("⏰ <b>오늘 청약 마감</b>")
        for _, row in today_sub_end.iterrows():
            lines.append(f"  • {row['corp_name']}")
        lines.append("")

    # 오늘 상장
    today_listing = ipo_df[ipo_df["listing_dt"] == today]
    if not today_listing.empty:
        lines.append("🚀 <b>오늘 상장</b>")
        for _, row in today_listing.iterrows():
            rcept_no = str(row.get("rcept_no", ""))
            pred_row = pred_df[pred_df["rcept_no"] == rcept_no]
            pred_text = ""
            if not pred_row.empty:
                close = pred_row.iloc[0].get("predicted_first_day_close", "")
                upside = pred_row.iloc[0].get("upside_pct", "")
                pred_text = f" | 예측 종가 {close}원 ({upside}%↑)"
            offering = row.get("offering_price_final", "미정")
            lines.append(
                f"  • {row['corp_name']} | 공모가 {offering}원{pred_text}"
            )
        lines.append("")

    # 3일 내 청약 예정
    upcoming_sub = ipo_df[
        (ipo_df["subscription_start_dt"] > today)
        & (ipo_df["subscription_start_dt"] <= in_3days)
    ]
    if not upcoming_sub.empty:
        lines.append("📅 <b>3일 내 청약 예정</b>")
        for _, row in upcoming_sub.iterrows():
            sub_start = row.get("subscription_start_dt", "")
            lines.append(f"  • {row['corp_name']} | 청약 {sub_start}")
        lines.append("")

    if len(lines) <= 4:
        lines.append("오늘은 특별한 공모주 일정이 없습니다. 🌿")

    return "\n".join(lines)


def format_weekly_summary(
    ipo_df: pd.DataFrame,
    accuracy_df: pd.DataFrame,
) -> str:
    """
    주간 공모주 요약 메시지를 생성합니다.
    """
    lines = [
        "📈 <b>공모주 주간 요약</b>",
        f"<i>{datetime.now().strftime('%Y년 %m월 %d일')} 기준</i>",
        "",
    ]

    status_counts = ipo_df["status"].value_counts()
    lines.append("📋 <b>현황</b>")
    for status, count in status_counts.items():
        lines.append(f"  • {status}: {count}건")
    lines.append("")

    if not accuracy_df.empty:
        latest = accuracy_df.iloc[-1]
        lines.append("🎯 <b>예측 정확도</b>")
        lines.append(f"  • 총 예측: {latest.get('total_predictions', 0)}건")
        lines.append(f"  • 평가 완료: {latest.get('evaluated', 0)}건")
        lines.append(f"  • 평균 오차: {latest.get('mean_error_pct', '-')}%")
        lines.append(f"  • 정확도 점수: {latest.get('accuracy_score', '-')}/100")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def run(mode: str = "daily") -> int:
    """
    Args:
        mode: "daily" (일일 브리핑) 또는 "weekly" (주간 요약)
    """
    logger.info("=" * 50)
    logger.info(f"notify.py 시작 (mode={mode})")
    logger.info("=" * 50)

    if not IPO_LIST_PATH.exists():
        logger.error("ipo_list.csv가 없습니다. collect.py → analyze.py 를 먼저 실행하세요.")
        return 1

    ipo_df = pd.read_csv(IPO_LIST_PATH, dtype=str)
    pred_df = (
        pd.read_csv(PREDICTIONS_PATH, dtype=str)
        if PREDICTIONS_PATH.exists()
        else pd.DataFrame()
    )
    acc_df = (
        pd.read_csv(ACCURACY_LOG_PATH, dtype=str)
        if ACCURACY_LOG_PATH.exists()
        else pd.DataFrame()
    )

    if mode == "weekly":
        message = format_weekly_summary(ipo_df, acc_df)
    else:
        message = format_daily_briefing(ipo_df, pred_df)

    success = send_message(message)

    logger.info("=" * 50)
    logger.info("notify.py 완료")
    logger.info("=" * 50)
    return 0 if success else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    sys.exit(run(mode=mode))
