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
    if (!prev || (row.rcept_dt || "") > (prev.rcept_dt || "")) {
      map.set(key, row);
    }
  }
  return Array.from(map.values());
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
  const rank = {"청약중": 0, "청약예정": 1, "상장예정": 2, "청약종료": 3, "상장완료": 4}[r.status] ?? 5;
  const lt = r.listing_dt || "99999999";
  // 상장완료는 내림차순 → 날짜를 반전
  const dateKey = rank === 4
    ? String(99999999 - parseInt(lt, 10)).padStart(8, "0")
    : lt;
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
    unique.filter(r => r.status === "청약중").length;
  document.getElementById("stat-upcoming").textContent =
    unique.filter(r => r.status === "청약예정").length;
  document.getElementById("stat-listing").textContent =
    unique.filter(r => r.status === "상장예정").length;

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

    // 30일 이내 상장 예정 → 행 강조
    const lt = r.listing_dt || "";
    const isSoon = lt && lt >= today && lt <= in30;
    const rowCls = isSoon ? ' style="background:rgba(99,179,237,0.06)"' : "";

    return `
    <tr${rowCls}>
      <td><strong>${r.corp_name || "—"}</strong></td>
      <td>${r.market || "—"}</td>
      <td class="text-mono">${formatDate(r.listing_dt)}</td>
      <td class="text-right text-mono">${price}</td>
      <td class="text-mono" style="font-size:0.75rem">${subPeriod}</td>
      <td class="text-mono" style="font-size:0.75rem">${r.underwriter || "—"}</td>
      <td>${statusBadge(r.status)}</td>
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
    return `
    <tr>
      <td><strong>${r.corp_name || "—"}</strong></td>
      <td class="text-right text-mono">${formatNumber(r.predicted_first_day_close)}</td>
      <td class="text-right text-mono">${formatNumber(r.predicted_first_day_high)}</td>
      <td class="text-right"><span class="${cls}">${upside}</span></td>
      <td>${confidenceLabel(r.confidence)}</td>
      <td style="max-width:300px;font-size:0.75rem;color:var(--color-text-secondary)">${r.reasoning || "—"}</td>
      <td class="text-mono" style="font-size:0.75rem">${r.predicted_at || "—"}</td>
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
      <td class="text-mono">${r.stock_code || "—"}</td>
      <td class="text-mono">${formatDate(r.listing_dt)}</td>
      <td class="text-right text-mono">${formatNumber(r.offering_price_final)}</td>
      <td class="text-right text-mono">${formatNumber(r.first_day_open)}</td>
      <td class="text-right text-mono">${formatNumber(r.first_day_high)}</td>
      <td class="text-right text-mono">${formatNumber(r.first_day_close)}</td>
      <td class="text-right"><span class="${cls}">${chg}</span></td>
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
      <td class="text-mono">${r.log_dt || "—"}</td>
      <td class="text-right">${r.total_predictions || "—"}</td>
      <td class="text-right">${r.evaluated || "—"}</td>
      <td class="text-right">${r.within_10pct || "—"}</td>
      <td class="text-right">${r.within_20pct || "—"}</td>
      <td class="text-right text-mono">${r.mean_error_pct ? r.mean_error_pct + "%" : "—"}</td>
      <td class="text-right"><span class="change ${scoreCls}">${r.accuracy_score ? r.accuracy_score + "/100" : "—"}</span></td>
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
