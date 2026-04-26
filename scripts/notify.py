"""
notify.py - Telegram 봇을 통한 공모주 청약 판단 알림

Usage:
    python scripts/notify.py [daily|weekly]

메시지 구조:
  daily  - 청약 예정 종목별 AI 예측·경쟁률 기반 청약 추천/보류 판단 카드
  weekly - 이번 주 청약 예정 전체 + 정확도 현황
"""

import json
import logging
import os
import sys
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
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
        if hasattr(sys.stdout, "fileno") else sys.stdout
    )],
)
logger = logging.getLogger("notify")

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env.local")

DATA_DIR     = PROJECT_ROOT / "data"
DETAILS_DIR  = DATA_DIR / "details"
IPO_LIST_PATH     = DATA_DIR / "ipo_list.csv"
PREDICTIONS_PATH  = DATA_DIR / "predictions.csv"
ACCURACY_LOG_PATH = DATA_DIR / "accuracy_log.csv"

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID:   str = os.environ.get("TELEGRAM_CHAT_ID", "")

DASHBOARD_URL = "https://lee9387-hm.github.io/ipo-investment-dashboard/dashboard/"


# ---------------------------------------------------------------------------
# Telegram 전송
# ---------------------------------------------------------------------------

def send_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 - 알림 건너뜀")
        return True
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Telegram 전송 성공")
        return True
    except Exception as exc:
        logger.error(f"Telegram 전송 실패: {exc}")
        return False


# ---------------------------------------------------------------------------
# 청약 판단 로직
# ---------------------------------------------------------------------------

def get_verdict(
    upside_pct: str,
    confidence: str,
    competition_ratio: str,
    lock_up_ratio: str,
) -> tuple[str, list[str]]:
    """
    AI 예측·수요예측 지표를 종합해 청약 추천 판단을 반환합니다.

    Returns:
        (verdict_emoji, reasons[])
        verdict: 🟢 강력 추천 / 🟡 청약 검토 / ⚪ 중립 / 🔴 보류 / ⚫ 데이터 수집 전
    """
    has_ai     = bool(upside_pct and str(upside_pct).strip())
    has_demand = bool(competition_ratio and str(competition_ratio).strip())

    # 핵심 데이터가 전혀 없으면 판단 불가 — "중립"과 명확히 구분
    if not has_ai and not has_demand:
        return "⚫ 데이터 수집 전", ["AI예측·수요예측 결과 미수집"]

    score = 0
    reasons: list[str] = []

    # AI 예측 점수
    try:
        upside = float(upside_pct or 0)
        conf   = int(confidence or 0)
        if upside >= 50 and conf == 3:
            score += 3; reasons.append(f"AI +{upside:.0f}% (고신뢰)")
        elif upside >= 30 and conf >= 2:
            score += 2; reasons.append(f"AI +{upside:.0f}%")
        elif upside > 0:
            score += 1; reasons.append(f"AI +{upside:.0f}%")
        elif upside <= 0 and upside_pct:
            score -= 1; reasons.append(f"AI {upside:.0f}%")
    except (ValueError, TypeError):
        pass

    # 수요예측 경쟁률
    try:
        ratio = int(str(competition_ratio).replace(",", ""))
        if ratio >= 1000:
            score += 3; reasons.append(f"경쟁률 {ratio:,}:1")
        elif ratio >= 500:
            score += 2; reasons.append(f"경쟁률 {ratio:,}:1")
        elif ratio >= 100:
            score += 1; reasons.append(f"경쟁률 {ratio:,}:1")
        else:
            score -= 1; reasons.append(f"경쟁률 {ratio:,}:1 (낮음)")
    except (ValueError, TypeError):
        pass

    # 의무보유확약 비율
    try:
        lu = float(str(lock_up_ratio).replace("%", ""))
        if lu >= 30:
            score += 2; reasons.append(f"확약 {lu:.0f}%")
        elif lu >= 10:
            score += 1; reasons.append(f"확약 {lu:.0f}%")
    except (ValueError, TypeError):
        pass

    if score >= 5:
        verdict = "🟢 강력 추천"
    elif score >= 2:
        verdict = "🟡 청약 검토"
    elif score >= 0:
        verdict = "⚪ 중립"
    else:
        verdict = "🔴 보류"

    return verdict, reasons


def load_detail(rcept_no: str) -> dict:
    """data/details/{rcept_no}.json을 로드합니다. 없으면 빈 dict 반환."""
    path = DETAILS_DIR / f"{rcept_no}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def ipo_card(row: pd.Series, pred_df: pd.DataFrame) -> str:
    """
    공모주 1건에 대한 판단 카드를 생성합니다.
    """
    corp_name = row.get("corp_name", "—")
    market    = row.get("market", "")
    rcept_no  = str(row.get("rcept_no", ""))

    # 공모가
    p_final = row.get("offering_price_final", "") or ""
    p_high  = row.get("offering_price_high",  "") or ""
    p_low   = row.get("offering_price_low",   "") or ""
    if p_final:
        price_str = f"{int(p_final):,}원 (확정)"
    elif p_high:
        price_str = f"{int(p_low):,}~{int(p_high):,}원" if p_low else f"~{int(p_high):,}원"
    else:
        price_str = "미정"

    # 청약·상장 일정
    sub_s = row.get("subscription_start_dt", "") or ""
    sub_e = row.get("subscription_end_dt",   "") or ""
    lst   = row.get("listing_dt",            "") or ""

    def fmt(d: str) -> str:
        return f"{d[4:6]}/{d[6:8]}" if len(d) == 8 else "—"

    schedule_parts = []
    if sub_s:
        schedule_parts.append(f"청약 {fmt(sub_s)}~{fmt(sub_e)}")
    if lst:
        schedule_parts.append(f"상장 {fmt(lst)}")
    schedule_str = " · ".join(schedule_parts) if schedule_parts else "일정 미정"

    underwriter = row.get("underwriter", "") or ""

    # AI 예측
    pred_row = pred_df[pred_df["rcept_no"] == rcept_no]
    upside_pct = confidence = pred_close = bull_points = bear_points = ""
    if not pred_row.empty:
        p = pred_row.iloc[0]
        upside_pct   = p.get("upside_pct", "")
        confidence   = p.get("confidence", "")
        pred_close   = p.get("predicted_first_day_close", "")
        bull_points  = p.get("bull_points", "")
        bear_points  = p.get("bear_points", "")

    conf_label = {"1": "저신뢰", "2": "보통", "3": "고신뢰"}.get(str(confidence), "")

    # 수요예측 상세 (details JSON)
    detail = load_detail(rcept_no)
    competition_ratio = detail.get("competition_ratio", "")
    lock_up_ratio     = detail.get("lock_up_ratio",     "")

    # 판단
    verdict, reasons = get_verdict(upside_pct, confidence, competition_ratio, lock_up_ratio)

    # 카드 조립
    lines = [f"🏢 <b>{corp_name}</b>  [{market}]"]
    lines.append(f"💰 {price_str}  |  {schedule_str}")

    if underwriter:
        lines.append(f"🏦 주관사: {underwriter}")

    # 핵심 지표
    indicators = []
    if upside_pct and pred_close:
        try:
            indicators.append(
                f"🤖 예측 {int(pred_close):,}원 (+{float(upside_pct):.0f}%)"
                f"{' · ' + conf_label if conf_label else ''}"
            )
        except (ValueError, TypeError):
            pass
    if competition_ratio:
        try:
            indicators.append(f"📊 경쟁률 {int(competition_ratio):,}:1")
        except (ValueError, TypeError):
            pass
    if lock_up_ratio:
        indicators.append(f"🔒 확약 {lock_up_ratio}%")
    if not indicators:
        indicators.append("📊 수요예측 데이터 수집 전")

    lines.extend(indicators)

    # Bull/Bear 근거 (AI 예측이 있을 때만 표시, 각 1건)
    if bull_points:
        pts = [p.strip() for p in bull_points.split("|") if p.strip()]
        if pts:
            lines.append(f"  📈 {pts[0]}")
    if bear_points:
        pts = [p.strip() for p in bear_points.split("|") if p.strip()]
        if pts:
            lines.append(f"  📉 {pts[0]}")

    # 커뮤니티 감성 최신 헤드라인 (1건)
    community_news = detail.get("community_news", []) or []
    if community_news:
        top = community_news[0]
        title = top.get("title", "").replace("<b>", "").replace("</b>", "")
        source = top.get("source", "")
        if title:
            src_tag = f"[{source}] " if source else ""
            lines.append(f"  💬 {src_tag}{title[:60]}{'…' if len(title) > 60 else ''}")

    reason_str = " · ".join(reasons) if reasons else "판단 근거 부족"
    lines.append(f"판단: {verdict}  <i>({reason_str})</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 메시지 포맷터
# ---------------------------------------------------------------------------

def format_daily_briefing(ipo_df: pd.DataFrame, pred_df: pd.DataFrame) -> str:
    today    = datetime.now().strftime("%Y%m%d")
    in_7days = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")
    display  = datetime.now().strftime("%Y년 %m월 %d일")

    sections: list[str] = [
        f"📊 <b>공모주 청약 브리핑</b>",
        f"<i>{display}</i>",
    ]

    # 1) 오늘 상장 — 결과 확인 필요
    today_listing = ipo_df[ipo_df["listing_dt"] == today]
    if not today_listing.empty:
        sections.append("\n🚀 <b>오늘 상장</b>")
        for _, row in today_listing.iterrows():
            rcept_no = str(row.get("rcept_no", ""))
            pred_row = pred_df[pred_df["rcept_no"] == rcept_no]
            offering = row.get("offering_price_final", "미정")
            try:
                offering_fmt = f"{int(offering):,}원" if offering and offering != "미정" else "미정"
            except (ValueError, TypeError):
                offering_fmt = offering
            pred_text = ""
            if not pred_row.empty:
                p = pred_row.iloc[0]
                close   = p.get("predicted_first_day_close", "")
                upside  = p.get("upside_pct", "")
                try:
                    pred_text = f"  →  AI 예측 {int(close):,}원 (+{float(upside):.0f}%)"
                except (ValueError, TypeError):
                    pass
            sections.append(f"  • <b>{row['corp_name']}</b>  공모가 {offering_fmt}{pred_text}")

    # 2) 오늘 청약 시작 / 마감
    today_start = ipo_df[ipo_df["subscription_start_dt"] == today]
    today_end   = ipo_df[ipo_df["subscription_end_dt"]   == today]

    if not today_start.empty:
        sections.append("\n🔔 <b>오늘 청약 시작</b>")
        for _, row in today_start.iterrows():
            sections.append(f"  • {ipo_card(row, pred_df)}")

    if not today_end.empty:
        sections.append("\n⏰ <b>오늘 청약 마감</b>")
        for _, row in today_end.iterrows():
            sections.append(f"  • <b>{row['corp_name']}</b>  — 마지막 청약 기회")

    # 3) 7일 내 청약 예정 (판단 카드 포함)
    upcoming = ipo_df[
        ipo_df["subscription_start_dt"].notna()
        & (ipo_df["subscription_start_dt"] > today)
        & (ipo_df["subscription_start_dt"] <= in_7days)
    ].sort_values("subscription_start_dt")

    if not upcoming.empty:
        sections.append(f"\n📅 <b>7일 내 청약 예정 ({len(upcoming)}건)</b>")
        for _, row in upcoming.iterrows():
            sections.append("")
            sections.append(ipo_card(row, pred_df))

    if len(sections) <= 3:
        sections.append("\n오늘은 특별한 공모주 일정이 없습니다. 🌿")

    # 푸터
    sections.append(f"\n🔗 <a href=\"{DASHBOARD_URL}\">대시보드 바로가기</a>")

    return "\n".join(sections)


def format_weekly_summary(ipo_df: pd.DataFrame, pred_df: pd.DataFrame, accuracy_df: pd.DataFrame) -> str:
    today    = datetime.now().strftime("%Y%m%d")
    in_14days = (datetime.now() + timedelta(days=14)).strftime("%Y%m%d")
    display  = datetime.now().strftime("%Y년 %m월 %d일")

    lines = [
        "📈 <b>공모주 주간 브리핑</b>",
        f"<i>{display} 기준</i>",
    ]

    # 현황 요약 — CSV status는 stale하므로 날짜로 직접 계산
    def _dyn_status(row: pd.Series) -> str:
        lst  = str(row.get("listing_dt", "") or "").strip()
        ss   = str(row.get("subscription_start_dt", "") or "").strip()
        se   = str(row.get("subscription_end_dt",   "") or "").strip()
        if lst and lst <= today:                         return "상장완료"
        if ss and se and ss <= today <= se:              return "청약중"
        if se and se < today: return "상장예정" if (lst and lst > today) else "청약종료"
        if ss and ss > today:                            return "청약예정"
        if lst and lst > today:                          return "상장예정"
        return "정보수집중"

    status_counts = ipo_df.apply(_dyn_status, axis=1).value_counts()
    lines.append("\n<b>📋 현황</b>")
    for status, cnt in status_counts.items():
        lines.append(f"  • {status}: {cnt}건")

    # 이번 주 청약 예정 (판단 카드)
    upcoming = ipo_df[
        ipo_df["subscription_start_dt"].notna()
        & (ipo_df["subscription_start_dt"] >= today)
        & (ipo_df["subscription_start_dt"] <= in_14days)
    ].sort_values("subscription_start_dt")

    if not upcoming.empty:
        lines.append(f"\n<b>📅 2주 내 청약 예정 ({len(upcoming)}건)</b>")
        for _, row in upcoming.iterrows():
            lines.append("")
            lines.append(ipo_card(row, pred_df))

    # 예측 정확도
    if not accuracy_df.empty:
        latest = accuracy_df.iloc[-1]
        lines.append("\n<b>🎯 AI 예측 정확도</b>")
        lines.append(f"  • 총 예측 {latest.get('total_predictions', 0)}건 / 평가 완료 {latest.get('evaluated', 0)}건")
        lines.append(f"  • 평균 오차: {latest.get('mean_error_pct', '-')}%")
        lines.append(f"  • 정확도 점수: {latest.get('accuracy_score', '-')}/100")

    lines.append(f"\n🔗 <a href=\"{DASHBOARD_URL}\">대시보드 바로가기</a>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def run(mode: str = "daily") -> int:
    logger.info("=" * 50)
    logger.info(f"notify.py 시작 (mode={mode})")
    logger.info("=" * 50)

    if not IPO_LIST_PATH.exists():
        logger.error("ipo_list.csv가 없습니다.")
        return 1

    ipo_df  = pd.read_csv(IPO_LIST_PATH, dtype=str).fillna("")
    # 동일 기업 중복 제거: 청약일 있는 행 우선, 없으면 최신 rcept_dt
    if "corp_name" in ipo_df.columns and "rcept_dt" in ipo_df.columns:
        ipo_df = ipo_df.copy()
        ipo_df["_has_sub"] = (ipo_df["subscription_start_dt"].fillna("") != "").astype(int)
        ipo_df = (
            ipo_df.sort_values(["_has_sub", "rcept_dt"], ascending=[False, False])
                  .drop_duplicates(subset=["corp_name"], keep="first")
                  .drop(columns=["_has_sub"])
                  .reset_index(drop=True)
        )
    pred_df = pd.read_csv(PREDICTIONS_PATH, dtype=str).fillna("") if PREDICTIONS_PATH.exists() else pd.DataFrame(columns=["rcept_no"])
    acc_df  = pd.read_csv(ACCURACY_LOG_PATH, dtype=str).fillna("") if ACCURACY_LOG_PATH.exists() else pd.DataFrame()

    message = (
        format_weekly_summary(ipo_df, pred_df, acc_df)
        if mode == "weekly"
        else format_daily_briefing(ipo_df, pred_df)
    )

    logger.info("생성된 메시지:\n" + message)
    success = send_message(message)

    logger.info("=" * 50)
    logger.info("notify.py 완료")
    logger.info("=" * 50)
    return 0 if success else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    sys.exit(run(mode=mode))
