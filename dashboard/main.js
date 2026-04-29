/**
 * main.js - IPO 대시보드 데이터 로딩 및 렌더링
 *
 * GitHub Pages 환경에서 data/ 디렉터리의 CSV 파일을 fetch로 로드합니다.
 * 경로는 루트 기준: /data/ipo_list.csv 등
 */

"use strict";

// ---------------------------------------------------------------------------
// 설정
// ---------------------------------------------------------------------------

const DATA_BASE = "../data";

const CSV_FILES = {
  ipo:        `${DATA_BASE}/ipo_list.csv`,
  predictions:`${DATA_BASE}/predictions.csv`,
  results:    `${DATA_BASE}/results.csv`,
  accuracy:   `${DATA_BASE}/accuracy_log.csv`,
};

// ---------------------------------------------------------------------------
// CSV 파서 (외부 라이브러리 없이 구현)
// ---------------------------------------------------------------------------

/**
 * CSV 문자열을 { columns: string[], rows: Object[] } 형태로 파싱합니다.
 * RFC 4180 준수 (쌍따옴표 이스케이프 지원).
 * @param {string} text
 * @returns {{ columns: string[], rows: Object[] }}
 */
function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) return { columns: [], rows: [] };

  const columns = splitCsvLine(lines[0]);
  const rows = [];

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const values = splitCsvLine(line);
    const row = {};
    columns.forEach((col, idx) => {
      row[col] = (values[idx] ?? "").trim();
    });
    rows.push(row);
  }

  return { columns, rows };
}

/**
 * CSV의 단일 행을 필드 배열로 분리합니다.
 * @param {string} line
 * @returns {string[]}
 */
function splitCsvLine(line) {
  const fields = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === "," && !inQuotes) {
      fields.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields;
}

// ---------------------------------------------------------------------------
// 데이터 로딩
// ---------------------------------------------------------------------------

/**
 * CSV 파일을 fetch하여 파싱 결과를 반환합니다.
 * 실패 시 빈 결과를 반환합니다.
 * @param {string} url
 * @returns {Promise<{ columns: string[], rows: Object[] }>}
 */
async function fetchCsv(url) {
  try {
    const resp = await fetch(url, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    return parseCsv(text);
  } catch (err) {
    console.warn(`CSV 로드 실패: ${url}`, err.message);
    return { columns: [], rows: [] };
  }
}

// ---------------------------------------------------------------------------
// 유틸리티
// ---------------------------------------------------------------------------

/**
 * YYYYMMDD → YYYY-MM-DD 변환. 빈 값은 "—" 반환.
 * @param {string} dt
 * @returns {string}
 */
function formatDate(dt) {
  if (!dt || dt.length !== 8) return "—";
  return `${dt.slice(0, 4)}-${dt.slice(4, 6)}-${dt.slice(6, 8)}`;
}

/**
 * 숫자 문자열을 천단위 콤마 형식으로 변환.
 * @param {string} val
 * @returns {string}
 */
function formatNumber(val) {
  if (!val || val === "") return "—";
  const num = Number(val.replace(/,/g, ""));
  if (isNaN(num)) return val;
  return num.toLocaleString("ko-KR");
}

/**
 * 상태값에 따라 badge HTML을 반환합니다.
 * @param {string} status
 * @returns {string}
 */
function statusBadge(status) {
  const map = {
    "청약중":    ["badge--warning", "청약중"],
    "청약예정":  ["badge--info",    "청약예정"],
    "청약종료":  ["badge--neutral", "청약종료"],
    "상장예정":  ["badge--info",    "상장예정"],
    "상장완료":  ["badge--success", "상장완료"],
    "정보수집중":["badge--neutral", "수집중"],
  };
  const [cls, label] = map[status] ?? ["badge--neutral", status || "—"];
  return `<span class="badge ${cls}">${label}</span>`;
}

/**
 * 수익률에 따라 색상 클래스를 반환합니다.
 * @param {string} pct
 * @returns {string}
 */
function changeCls(pct) {
  const n = parseFloat(pct);
  if (isNaN(n) || n === 0) return "change change--flat";
  return n > 0 ? "change change--up" : "change change--down";
}

/**
 * 신뢰도 숫자를 텍스트로 변환합니다.
 * @param {string} conf
 * @returns {string}
 */
function confidenceLabel(conf) {
  return { "1": "낮음", "2": "보통", "3": "높음" }[conf] ?? conf ?? "—";
}

// ---------------------------------------------------------------------------
// 렌더러 유틸
// ---------------------------------------------------------------------------

/**
 * corp_code(있으면) 또는 corp_name 기준으로 중복 제거.
 * 같은 회사의 여러 공시 중 rcept_dt가 가장 최근인 행을 남긴다.
 * @param {Object[]} rows
 * @returns {Object[]}
 */
function deduplicateRows(rows) {
  const map = new Map();
  for (const row of rows) {
    const key = row.corp_code || row.corp_name;
    const prev = map.get(key);
    if (!prev) { map.set(key, row); continue; }
    // 청약일 있는 행 우선, 동순위면 최신 rcept_dt
    const rowHasSub  = (row.subscription_start_dt  || "") !== "";
    const prevHasSub = (prev.subscription_start_dt || "") !== "";
    if (rowHasSub && !prevHasSub) { map.set(key, row); continue; }
    if (!rowHasSub && prevHasSub) continue;
    if ((row.rcept_dt || "") > (prev.rcept_dt || "")) map.set(key, row);
  }
  return Array.from(map.values());
}

/**
 * CSV status가 stale할 수 있으므로 날짜로 직접 상태를 계산합니다.
 * @param {Object} r
 * @returns {string}
 */
function computeStatus(r) {
  const today   = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const lst     = (r.listing_dt              || "").trim();
  const subS    = (r.subscription_start_dt   || "").trim();
  const subE    = (r.subscription_end_dt     || "").trim();

  if (lst && lst <= today)                    return "상장완료";
  if (subS && subE && subS <= today && today <= subE) return "청약중";
  if (subE && subE < today) return lst && lst > today ? "상장예정" : "청약종료";
  if (subS && subS > today)                   return "청약예정";
  if (lst  && lst > today)                    return "상장예정";
  return "정보수집중";
}

/**
 * IPO 정렬 키 생성.
 * 그룹 순서: 청약중(0) → 청약예정(1) → 상장예정(2) → 청약종료(3) → 상장완료(4) → 기타(5)
 * 그룹 0-2: listing_dt 오름차순 (가까운 날짜 우선)
 * 그룹 4:   listing_dt 내림차순 (최근 상장 우선)
 * @param {Object} r
 * @returns {string}
 */
function ipoSortKey(r) {
  const status = computeStatus(r);
  const rank = {"청약중": 0, "청약예정": 1, "상장예정": 2, "청약종료": 3, "상장완료": 4}[status] ?? 5;
  const subS = r.subscription_start_dt || "99999999";
  const subE = r.subscription_end_dt   || "99999999";
  const lt   = r.listing_dt            || "99999999";

  let dateKey;
  if      (rank === 0) dateKey = subE;  // 청약중: 마감 임박 순
  else if (rank === 1) dateKey = subS;  // 청약예정: 시작일 빠른 순
  else if (rank === 2) dateKey = lt;    // 상장예정: 상장일 빠른 순
  else if (rank === 3) dateKey = String(99999999 - parseInt(subE, 10)).padStart(8, "0"); // 청약종료: 최근 순
  else if (rank === 4) dateKey = String(99999999 - parseInt(lt,   10)).padStart(8, "0"); // 상장완료: 최근 순
  else                 dateKey = "99999999";

  return `${rank}${dateKey}`;
}

// ---------------------------------------------------------------------------
// 렌더러
// ---------------------------------------------------------------------------

/** IPO 목록 탭 렌더링 */
function renderIpoTable(rows) {
  const tbody = document.getElementById("ipoTableBody");

  // 중복 제거 후 통계/정렬에 사용
  const unique = deduplicateRows(rows);
  document.getElementById("ipoCount").textContent = `${unique.length}건`;

  // Stats
  document.getElementById("stat-total").textContent = unique.length;
  document.getElementById("stat-subscribing").textContent =
    unique.filter(r => computeStatus(r) === "청약중").length;
  document.getElementById("stat-upcoming").textContent =
    unique.filter(r => computeStatus(r) === "청약예정").length;
  document.getElementById("stat-listing").textContent =
    unique.filter(r => computeStatus(r) === "상장예정").length;

  if (unique.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <div class="empty-state__icon">📂</div>
          <div class="empty-state__text">수집된 공모주 데이터가 없습니다.<br>
          <code>python scripts/collect.py</code> 를 실행하세요.</div>
        </div>
      </td></tr>`;
    return;
  }

  // 상장 예정일 기준 스마트 정렬
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, ""); // YYYYMMDD
  const in30  = (() => {
    const d = new Date(); d.setDate(d.getDate() + 30);
    return d.toISOString().slice(0, 10).replace(/-/g, "");
  })();

  const sorted = [...unique].sort((a, b) =>
    ipoSortKey(a).localeCompare(ipoSortKey(b))
  );

  tbody.innerHTML = sorted.map(r => {
    const price = r.offering_price_final
      ? formatNumber(r.offering_price_final)
      : r.offering_price_high
        ? `~${formatNumber(r.offering_price_high)}`
        : "—";
    const subPeriod =
      r.subscription_start_dt && r.subscription_end_dt
        ? `${formatDate(r.subscription_start_dt)} ~ ${formatDate(r.subscription_end_dt)}`
        : "—";

    const lt = r.listing_dt || "";
    const isSoon = lt && lt >= today && lt <= in30;
    const bgStyle = isSoon ? "background:rgba(0,82,255,0.04)" : "";

    const rowJson = JSON.stringify(r).replace(/"/g, "&quot;");

    return `
    <tr class="clickable" style="${bgStyle}" onclick="openDetail(JSON.parse(this.dataset.row))" data-row="${rowJson}">
      <td><strong>${r.corp_name || "—"}</strong></td>
      <td data-label="시장">${r.market || "—"}</td>
      <td data-label="상장예정일" class="text-mono">${formatDate(r.listing_dt)}</td>
      <td data-label="공모가" class="text-right text-mono">${price}</td>
      <td data-label="청약기간" class="text-mono" style="font-size:0.75rem">${subPeriod}</td>
      <td data-label="주관사" class="text-mono" style="font-size:0.75rem">${r.underwriter || "—"}</td>
      <td data-label="상태">${statusBadge(computeStatus(r))}</td>
    </tr>`;
  }).join("");
}

/** 예측 탭 렌더링 */
function renderPredTable(rows) {
  const tbody = document.getElementById("predTableBody");
  document.getElementById("predCount").textContent = `${rows.length}건`;

  if (rows.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <div class="empty-state__icon">🤖</div>
          <div class="empty-state__text">아직 예측 데이터가 없습니다.<br>
          <code>python scripts/predict.py</code> 를 실행하세요.</div>
        </div>
      </td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(r => {
    const upside = r.upside_pct ? `${r.upside_pct}%` : "—";
    const cls = changeCls(r.upside_pct);
    const signalHtml = r.listing_signal
      ? `<div style="margin-top:2px;font-size:0.7rem;font-weight:700;color:var(--color-accent)">${r.listing_signal}</div>`
      : "";
    return `
    <tr>
      <td><strong>${r.corp_name || "—"}</strong>${signalHtml}</td>
      <td data-label="예측종가" class="text-right text-mono">${formatNumber(r.predicted_first_day_close)}</td>
      <td data-label="예측고가" class="text-right text-mono">${formatNumber(r.predicted_first_day_high)}</td>
      <td data-label="상승률" class="text-right"><span class="${cls}">${upside}</span></td>
      <td data-label="신뢰도">${confidenceLabel(r.confidence)}</td>
      <td data-label="근거" style="max-width:300px;font-size:0.75rem;color:var(--color-text-secondary)">${r.reasoning || "—"}</td>
      <td data-label="예측일" class="text-mono" style="font-size:0.75rem">${r.predicted_at || "—"}</td>
    </tr>`;
  }).join("");
}

/** 결과 탭 렌더링 */
function renderResultTable(rows) {
  const tbody = document.getElementById("resultTableBody");
  document.getElementById("resultCount").textContent = `${rows.length}건`;

  if (rows.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="8">
        <div class="empty-state">
          <div class="empty-state__icon">📊</div>
          <div class="empty-state__text">아직 상장 결과 데이터가 없습니다.</div>
        </div>
      </td></tr>`;
    return;
  }

  // stock_code 기준 중복 제거 (같은 회사 여러 공시 → 최신 listing_dt만 표시)
  const deduped = deduplicateRows(
    [...rows].sort((a, b) => (b.listing_dt || "").localeCompare(a.listing_dt || ""))
  );
  const sorted = deduped.sort((a, b) =>
    (b.listing_dt || "").localeCompare(a.listing_dt || "")
  );

  tbody.innerHTML = sorted.map(r => {
    const chg = r.first_day_change_pct
      ? `${parseFloat(r.first_day_change_pct) >= 0 ? "+" : ""}${r.first_day_change_pct}%`
      : "—";
    const cls = changeCls(r.first_day_change_pct);
    return `
    <tr>
      <td><strong>${r.corp_name || "—"}</strong></td>
      <td data-label="종목코드" class="text-mono">${r.stock_code || "—"}</td>
      <td data-label="상장일" class="text-mono">${formatDate(r.listing_dt)}</td>
      <td data-label="공모가" class="text-right text-mono">${formatNumber(r.offering_price_final)}</td>
      <td data-label="시가" class="text-right text-mono">${formatNumber(r.first_day_open)}</td>
      <td data-label="고가" class="text-right text-mono">${formatNumber(r.first_day_high)}</td>
      <td data-label="종가" class="text-right text-mono">${formatNumber(r.first_day_close)}</td>
      <td data-label="수익률" class="text-right"><span class="${cls}">${chg}</span></td>
    </tr>`;
  }).join("");
}

/** 정확도 탭 렌더링 */
function renderAccTable(rows) {
  const tbody = document.getElementById("accTableBody");
  document.getElementById("accCount").textContent = `${rows.length}건`;

  if (rows.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <div class="empty-state__icon">🎯</div>
          <div class="empty-state__text">아직 정확도 데이터가 없습니다.</div>
        </div>
      </td></tr>`;
    return;
  }

  const sorted = [...rows].sort((a, b) =>
    (b.log_dt || "").localeCompare(a.log_dt || "")
  );

  tbody.innerHTML = sorted.map(r => {
    const score = parseFloat(r.accuracy_score);
    const scoreCls = isNaN(score) ? "" : score >= 80 ? "change--up" : score >= 60 ? "" : "change--down";
    return `
    <tr>
      <td class="text-mono"><strong>${r.log_dt || "—"}</strong></td>
      <td data-label="총예측" class="text-right">${r.total_predictions || "—"}</td>
      <td data-label="평가완료" class="text-right">${r.evaluated || "—"}</td>
      <td data-label="10%이내" class="text-right">${r.within_10pct || "—"}</td>
      <td data-label="20%이내" class="text-right">${r.within_20pct || "—"}</td>
      <td data-label="평균오차" class="text-right text-mono">${r.mean_error_pct ? r.mean_error_pct + "%" : "—"}</td>
      <td data-label="정확도" class="text-right"><span class="change ${scoreCls}">${r.accuracy_score ? r.accuracy_score + "/100" : "—"}</span></td>
    </tr>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// 탭 전환
// ---------------------------------------------------------------------------

function initTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  const panels = document.querySelectorAll(".tab-panel");

  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;

      buttons.forEach(b => b.classList.toggle("active", b === btn));
      panels.forEach(p => {
        p.classList.toggle("active", p.id === `tab-${target}`);
      });
    });
  });
}

// ---------------------------------------------------------------------------
// 초기화
// ---------------------------------------------------------------------------

async function init() {
  initTabs();
  initModal();

  const [ipoData, predData, resultData, accData] = await Promise.all([
    fetchCsv(CSV_FILES.ipo),
    fetchCsv(CSV_FILES.predictions),
    fetchCsv(CSV_FILES.results),
    fetchCsv(CSV_FILES.accuracy),
  ]);

  renderIpoTable(ipoData.rows);
  renderPredTable(predData.rows);
  renderResultTable(resultData.rows);
  renderAccTable(accData.rows);

  // 최근 갱신 시간 (ipo_list의 마지막 updated_at)
  const lastRow = ipoData.rows[ipoData.rows.length - 1];
  const updatedEl = document.getElementById("lastUpdated");
  if (lastRow && lastRow.updated_at) {
    updatedEl.textContent = `최종 갱신: ${lastRow.updated_at}`;
  } else {
    updatedEl.textContent = `최종 갱신: 데이터 없음`;
  }
}

document.addEventListener("DOMContentLoaded", init);

// ---------------------------------------------------------------------------
// 상세 모달
// ---------------------------------------------------------------------------

function dt(label, value, mono = true) {
  if (!value || value === "nan") return "";
  return `<dt>${label}</dt><dd class="${mono ? "" : "text-normal"}">${value}</dd>`;
}

/**
 * IPO 단계 타임라인 HTML 생성
 * 단계: 수요예측 → 공모청약 → 배정/환불 → 상장
 */
function buildTimeline(row, detail) {
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");

  // 날짜 포맷 (YYYYMMDD → MM/DD)
  const fmtShort = d => d && d.length === 8 ? `${d.slice(4,6)}/${d.slice(6,8)}` : "";

  // 수요예측 기간
  const dfp   = detail && detail.demand_forecast_period ? detail.demand_forecast_period.split("~") : [];
  const dfS   = dfp[0] ? dfp[0].trim() : "";
  const dfE   = dfp[1] ? dfp[1].trim() : "";

  const subS  = (row.subscription_start_dt || "").trim();
  const subE  = (row.subscription_end_dt   || "").trim();
  const lst   = (row.listing_dt            || "").trim();

  // 배정공고일: 통상 청약종료 다음날~2일 후 (표시용 추정, 정확한 데이터 없음)
  let allocDate = "";
  if (subE && subE.length === 8) {
    const d = new Date(subE.slice(0,4), subE.slice(4,6)-1, parseInt(subE.slice(6,8)) + 2);
    allocDate = d.toISOString().slice(0,10).replace(/-/g,"");
  }

  /**
   * 단계 상태 결정
   * done: 이미 지남 / active: 현재 진행 중 / upcoming: 아직 시작 전
   */
  function stepState(start, end) {
    if (!start) return "upcoming";
    const s = start, e = end || start;
    if (today > e)              return "done";
    if (today >= s && today <= e) return "active";
    return "upcoming";
  }

  const steps = [
    {
      label: "수요예측",
      state: stepState(dfS, dfE),
      date:  dfS ? (dfE && dfS !== dfE ? `${fmtShort(dfS)}~${fmtShort(dfE)}` : fmtShort(dfS)) : "",
    },
    {
      label: "공모청약",
      state: stepState(subS, subE),
      date:  subS ? (subE && subS !== subE ? `${fmtShort(subS)}~${fmtShort(subE)}` : fmtShort(subS)) : "",
    },
    {
      label: "배정·환불",
      state: stepState(allocDate, allocDate),
      date:  allocDate ? `${fmtShort(allocDate)} 추정` : "",
    },
    {
      label: "상장",
      state: stepState(lst, lst),
      date:  lst ? fmtShort(lst) : "",
    },
  ];

  const stepsHtml = steps.map(s => `
    <div class="timeline-step ${s.state}">
      <div class="timeline-dot"></div>
      <div class="timeline-label">${s.label}</div>
      <div class="timeline-date">${s.date || "미정"}</div>
    </div>`).join("");

  return `<div class="ipo-timeline">${stepsHtml}</div>`;
}

function formatDateRange(start, end) {
  if (!start) return "—";
  return end ? `${formatDate(start)} ~ ${formatDate(end)}` : formatDate(start);
}

async function openDetail(row) {
  const rcept_no = row.rcept_no || "";
  const backdrop = document.getElementById("detailBackdrop");

  // 헤더 설정
  document.getElementById("modalTitle").textContent = row.corp_name || "—";
  const metaParts = [
    row.market || "",
    statusBadge(computeStatus(row)),
    row.stock_code ? `코드 ${row.stock_code}` : "",
  ].filter(Boolean);
  document.getElementById("modalMeta").innerHTML = metaParts.join(" · ");

  // 기업 개요 탭 — 타임라인 + 상세 정보
  const price = row.offering_price_final
    ? `${formatNumber(row.offering_price_final)}원 (확정)`
    : row.offering_price_high
      ? `${formatNumber(row.offering_price_low)}원 ~ ${formatNumber(row.offering_price_high)}원 (희망)`
      : "—";

  // 경쟁률 기반 상장일 전략 시그널 (JS 규칙, Python과 동일 기준)
  function listingSignalFromRatio(cr) {
    if (!cr) return "";
    const r = parseFloat(String(cr).replace(/,/g, ""));
    if (isNaN(r)) return "";
    if (r >= 1000) return "🚀 상장일 즉시 매도";
    if (r >= 700)  return "📈 상장일 매도 유리";
    if (r >= 300)  return "🟢 적극 청약";
    if (r >= 100)  return "🟡 청약 검토";
    return "";
  }

  // 타임라인 먼저 렌더링 (수요예측 기간은 detail 로드 후 업데이트)
  function renderOverview(d) {
    const timeline = buildTimeline(row, d || {});
    const signal = d ? listingSignalFromRatio(d.competition_ratio) : "";
    const signalDt = signal
      ? `<dt style="font-weight:700;font-size:var(--text-xs);color:var(--color-text-muted);text-transform:uppercase;letter-spacing:.06em">전략 시그널</dt>
         <dd style="font-weight:700;color:var(--color-accent);font-size:var(--text-sm)">${signal}</dd>`
      : "";
    const grid = [
      dt("시장",       row.market),
      dt("공모가",     price, false),
      dt("청약기간",   formatDateRange(row.subscription_start_dt, row.subscription_end_dt), false),
      dt("상장예정일", row.listing_dt ? formatDate(row.listing_dt) : "미정", false),
      dt("주관사",     row.underwriter || "미정", false),
      dt("공모주식수", row.total_shares ? `${Number(row.total_shares).toLocaleString("ko-KR")}주` : "—"),
      d && d.competition_ratio ? dt("수요예측 경쟁률", `${Number(d.competition_ratio).toLocaleString("ko-KR")} : 1`, false) : "",
      d && d.lock_up_ratio     ? dt("의무보유확약",   `${d.lock_up_ratio}%`, false) : "",
      signalDt,
      dt("접수번호",   rcept_no),
    ].filter(Boolean).join("");
    document.getElementById("detailOverview").innerHTML = timeline + `<dl class="detail-grid">${grid}</dl>`;
  }

  renderOverview(null);

  // 초기 상태로 모달 탭 첫 번째 선택
  document.querySelectorAll(".modal-tab").forEach((t, i) => t.classList.toggle("active", i === 0));
  document.querySelectorAll(".modal-panel").forEach((p, i) => p.classList.toggle("active", i === 0));

  // 모달 열기
  backdrop.classList.add("open");
  document.body.style.overflow = "hidden";

  // 수요예측·뉴스 탭은 detail JSON 로드 후 채움
  const detailUrl = `../data/details/${rcept_no}.json`;
  try {
    const resp = await fetch(detailUrl, { cache: "no-cache" });
    if (resp.ok) {
      const d = await resp.json();
      renderOverview(d);           // 수요예측 기간 포함해서 타임라인 재렌더
      fillForecastTab(d);
      fillNewsTab(d.news || [], d.community_news || []);
    } else {
      fillForecastTab(null, rcept_no);
      fillNewsTab([], []);
    }
  } catch (_) {
    fillForecastTab(null, rcept_no);
    fillNewsTab([], []);
  }
}

function fillForecastTab(d, rcept_no = "") {
  const el = document.getElementById("detailForecast");
  const linkEl = document.getElementById("detailDartLink");

  if (!d) {
    el.innerHTML = `<dt style="grid-column:1/-1;color:var(--color-text-muted);font-size:var(--text-sm)">
      수요예측 데이터가 아직 수집되지 않았습니다.<br>다음 daily 업데이트 후 표시됩니다.</dt>`;
    linkEl.innerHTML = rcept_no
      ? `<a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${rcept_no}" target="_blank" rel="noopener">DART 공시 원문 보기 →</a>`
      : "";
    return;
  }

  el.innerHTML = [
    dt("수요예측 기간", d.demand_forecast_period
        ? formatDateRange(d.demand_forecast_period.split("~")[0], d.demand_forecast_period.split("~")[1])
        : ""),
    dt("경쟁률",        d.competition_ratio ? `${Number(d.competition_ratio).toLocaleString("ko-KR")} : 1` : ""),
    dt("의무보유확약",  d.lock_up_ratio ? `${d.lock_up_ratio}%` : ""),
    dt("공모주식수",    d.total_shares_offered ? `${Number(d.total_shares_offered).toLocaleString("ko-KR")}주` : ""),
    d.min_bid_price    ? dt("최저입찰공모가", `${Number(d.min_bid_price).toLocaleString("ko-KR")}원`) : "",
    d.business_summary
      ? `<dt style="grid-column:1/-1;font-size:var(--text-xs);color:var(--color-text-muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:var(--space-4)">사업 개요</dt>
         <dd class="text-normal" style="grid-column:1/-1;line-height:1.6;font-size:var(--text-sm);color:var(--color-text-secondary)">${d.business_summary}</dd>`
      : "",
  ].filter(Boolean).join("");

  linkEl.innerHTML = d.dart_link
    ? `<a href="${d.dart_link}" target="_blank" rel="noopener">DART 공시 원문 보기 →</a>`
    : "";
}

function fillNewsTab(news, communityNews) {
  const el = document.getElementById("detailNews");
  let html = "";

  if (communityNews && communityNews.length > 0) {
    html += `<p class="community-section-title">커뮤니티 · 블로그</p>`;
    html += communityNews.map(n => {
      const date = n.pubDate ? new Date(n.pubDate).toLocaleDateString("ko-KR") : "";
      const source = n.source || "";
      const desc = n.description ? `<div class="news-desc">${n.description.replace(/<[^>]+>/g, "")}</div>` : "";
      return `<li>
        ${source ? `<span class="news-source">${source}</span>` : ""}
        <a href="${n.link}" target="_blank" rel="noopener noreferrer">${n.title.replace(/<[^>]+>/g, "")}</a>
        ${desc}
        <span class="news-date">${date}</span>
      </li>`;
    }).join("");
  }

  if (news && news.length > 0) {
    if (communityNews && communityNews.length > 0) {
      html += `<p class="community-section-title" style="margin-top:var(--space-6)">언론 뉴스</p>`;
    }
    html += news.map(n => `
      <li>
        <a href="${n.link}" target="_blank" rel="noopener noreferrer">${n.title}</a>
        <span class="news-date">${n.pubDate ? new Date(n.pubDate).toLocaleDateString("ko-KR") : ""}</span>
      </li>`).join("");
  }

  if (!html) {
    html = `<li style="color:var(--color-text-muted);font-size:var(--text-sm)">관련 뉴스가 없습니다.</li>`;
  }

  el.innerHTML = html;
}

function closeDetail() {
  document.getElementById("detailBackdrop").classList.remove("open");
  document.body.style.overflow = "";
}

function initModal() {
  document.getElementById("modalClose").addEventListener("click", closeDetail);
  document.getElementById("detailBackdrop").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeDetail();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeDetail();
  });

  // 모달 내부 탭 전환
  document.querySelectorAll(".modal-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.mtab;
      document.querySelectorAll(".modal-tab").forEach(b => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".modal-panel").forEach(p => p.classList.toggle("active", p.id === `mtab-${target}`));
    });
  });
}
