const state = {
  items: [],
  filtered: [],
  summary: {},
  index: 0,
  medicine: null,
  orientation: null,
  graspRegion: null,
};

const $ = (id) => document.getElementById(id);
const binMap = { A: "BIN_A", B: "BIN_B", C: "BIN_C", unknown: "NONE" };
const statusLabels = { pending: "대기", approved: "승인", excluded: "제외" };
const splitLabels = { train: "학습", validation: "검증", test: "테스트", incoming: "신규 촬영" };
const phaseLabels = { before_grasp: "잡기 직전", after_grasp: "잡은 직후", after_place: "놓은 후" };
const resultLabels = { running: "동작 중", success: "성공", failed: "실패" };

async function loadItems(preferredId = null) {
  const response = await fetch("/api/items");
  if (!response.ok) throw new Error("검수 목록을 불러오지 못했습니다.");
  const data = await response.json();
  state.items = data.items;
  state.summary = data.summary;
  updateSummary();
  applyFilters(preferredId);
}

function applyFilters(preferredId = null) {
  const status = $("statusFilter").value;
  const split = $("splitFilter").value;
  const medicine = $("medicineFilter").value;
  const hideDuplicates = $("hideDuplicates").checked;
  state.filtered = state.items.filter((item) => {
    if (status !== "all" && item.review_status !== status) return false;
    if (split !== "all" && item.split !== split) return false;
    if (medicine !== "all" && item.original_completion.medicine_id !== medicine) return false;
    if (hideDuplicates && item.near_duplicate_of) return false;
    return true;
  });
  if (preferredId) {
    const found = state.filtered.findIndex((item) => item.id === preferredId);
    state.index = found >= 0 ? found : Math.min(state.index, Math.max(0, state.filtered.length - 1));
  } else {
    state.index = Math.min(state.index, Math.max(0, state.filtered.length - 1));
  }
  renderCurrent();
}

function updateSummary() {
  const s = state.summary;
  $("statTotal").textContent = s.total ?? 0;
  $("statPending").textContent = s.pending ?? 0;
  $("statApproved").textContent = s.approved ?? 0;
  $("statExcluded").textContent = s.excluded ?? 0;
  const percent = s.total ? Math.round((s.reviewed / s.total) * 100) : 0;
  $("progressPercent").textContent = `${percent}%`;
  $("progressBar").style.width = `${percent}%`;
}

function renderCurrent() {
  const item = state.filtered[state.index];
  const empty = !item;
  $("emptyState").classList.toggle("hidden", !empty);
  $("reviewContent").classList.toggle("hidden", empty);
  $("positionLabel").textContent = empty ? "0 / 0" : `${state.index + 1} / ${state.filtered.length}`;
  $("sampleId").textContent = empty ? "검수할 항목 없음" : item.id;
  $("prevButton").disabled = empty || state.index === 0;
  $("nextButton").disabled = empty || state.index >= state.filtered.length - 1;
  if (empty) return;

  $("reviewImage").src = item.image_url;
  $("splitBadge").textContent = splitLabels[item.split] || item.split;
  $("cameraBadge").textContent = compactCamera(item.camera);
  $("sessionBadge").textContent = item.session_id;
  $("phaseBadge").textContent = phaseLabels[item.capture_phase] || item.capture_phase || "촬영 단계 없음";
  $("resultBadge").textContent = resultLabels[item.action_result] || item.action_result || "";
  $("resultBadge").classList.toggle("hidden", !item.action_result);
  $("duplicateBadge").classList.toggle("hidden", !item.near_duplicate_of);
  const hasVideo = Boolean(item.video_url);
  $("trialVideoPanel").classList.toggle("hidden", !hasVideo);
  if (hasVideo) {
    $("reviewVideo").src = item.video_url;
    const selection = item.frame_selection === "manual_marker" ? "직접 표시" : "자동 추출";
    $("videoOffset").textContent = `${item.trial_id || "영상"} · ${Number(item.video_offset_seconds || 0).toFixed(1)}초 · ${selection}`;
  } else {
    $("reviewVideo").removeAttribute("src");
    $("reviewVideo").load();
  }
  const ribbon = $("statusRibbon");
  ribbon.className = `status-ribbon ${item.review_status}`;
  ribbon.textContent = statusLabels[item.review_status] || item.review_status;

  state.medicine = item.completion.medicine_id;
  state.orientation = item.completion.orientation;
  state.graspRegion = item.grasp_region || null;
  $("reviewNote").value = item.note || "";
  updateSelections();
  $("saveStatus").textContent = item.decided_at ? `마지막 저장: ${formatTime(item.decided_at)}` : "아직 저장된 결정이 없습니다.";
}

function compactCamera(camera) {
  if (!camera) return "카메라 정보 없음";
  if (camera.includes("astra")) return "아스트라 천장 카메라";
  if (camera.includes("realsense")) return "리얼센스 천장 카메라";
  if (camera.startsWith("/dev/video")) return `손목 카메라 · ${camera}`;
  return `카메라 · ${camera}`;
}

function formatTime(value) {
  try { return new Date(value).toLocaleString("ko-KR"); } catch { return value; }
}

function updateSelections() {
  document.querySelectorAll("#medicineButtons button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.value === state.medicine);
  });
  document.querySelectorAll("#orientationButtons button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.value === state.orientation);
  });
  document.querySelectorAll("#graspButtons button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.value === state.graspRegion);
  });
  $("targetBin").textContent = binMap[state.medicine] || "—";
  const item = state.filtered[state.index];
  const changed = item && (
    state.medicine !== item.original_completion.medicine_id ||
    state.orientation !== item.original_completion.orientation
  );
  $("changedBadge").classList.toggle("hidden", !changed);
}

async function saveDecision(status) {
  const item = state.filtered[state.index];
  if (!item || !state.medicine || !state.orientation) return;
  if (status === "approved" && !state.graspRegion) {
    $("saveStatus").textContent = "승인하려면 뚜껑, 몸통, 판단 불가 중 하나를 선택하세요.";
    return;
  }
  setBusy(true);
  $("saveStatus").textContent = "저장 중…";
  try {
    const response = await fetch(`/api/reviews/${encodeURIComponent(item.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status,
        medicine_id: state.medicine,
        orientation: state.orientation,
        grasp_region: state.graspRegion,
        note: $("reviewNote").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "저장 실패");
    const oldIndex = state.index;
    const localIndex = state.items.findIndex((entry) => entry.id === item.id);
    state.items[localIndex] = data.item;
    state.summary = data.summary;
    updateSummary();
    applyFilters();
    state.index = Math.min(oldIndex, Math.max(0, state.filtered.length - 1));
    renderCurrent();
    $("saveStatus").textContent = status === "approved" ? "승인했습니다." : status === "excluded" ? "제외했습니다." : "대기 상태로 되돌렸습니다.";
  } catch (error) {
    $("saveStatus").textContent = error.message;
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  ["approveButton", "excludeButton", "pendingButton", "exportButton"].forEach((id) => $(id).disabled = busy);
}

function move(delta) {
  if (!state.filtered.length) return;
  state.index = Math.max(0, Math.min(state.filtered.length - 1, state.index + delta));
  renderCurrent();
}

async function exportApproved() {
  setBusy(true);
  $("exportStatus").textContent = "내보내는 중…";
  try {
    const response = await fetch("/api/export", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "내보내기 실패");
    $("exportStatus").textContent = `승인 ${data.export.approved_total}개를 reviewed_*.jsonl로 저장했습니다.`;
  } catch (error) {
    $("exportStatus").textContent = error.message;
  } finally {
    setBusy(false);
  }
}

document.querySelectorAll("#medicineButtons button").forEach((button) => button.addEventListener("click", () => {
  state.medicine = button.dataset.value;
  updateSelections();
  button.blur();
}));
document.querySelectorAll("#orientationButtons button").forEach((button) => button.addEventListener("click", () => {
  state.orientation = button.dataset.value;
  updateSelections();
  button.blur();
}));
document.querySelectorAll("#graspButtons button").forEach((button) => button.addEventListener("click", () => {
  state.graspRegion = button.dataset.value;
  updateSelections();
  button.blur();
}));

$("approveButton").addEventListener("click", () => saveDecision("approved"));
$("excludeButton").addEventListener("click", () => saveDecision("excluded"));
$("pendingButton").addEventListener("click", () => saveDecision("pending"));
$("prevButton").addEventListener("click", () => move(-1));
$("nextButton").addEventListener("click", () => move(1));
$("exportButton").addEventListener("click", exportApproved);
["statusFilter", "splitFilter", "medicineFilter", "hideDuplicates"].forEach((id) => $(id).addEventListener("change", () => {
  state.index = 0;
  applyFilters();
}));
$("resetFilters").addEventListener("click", () => {
  $("statusFilter").value = "pending";
  $("splitFilter").value = "all";
  $("medicineFilter").value = "all";
  $("hideDuplicates").checked = true;
  state.index = 0;
  applyFilters();
});

document.addEventListener("keydown", (event) => {
  if (["TEXTAREA", "SELECT", "INPUT", "BUTTON"].includes(document.activeElement.tagName)) return;
  if (event.key === "ArrowLeft") move(-1);
  if (event.key === "ArrowRight") move(1);
  if (event.code === "Space") {
    event.preventDefault();
    saveDecision("approved");
  }
  if (event.key.toLowerCase() === "x") saveDecision("excluded");
  if ({ "1": "A", "2": "B", "3": "C" }[event.key]) {
    state.medicine = { "1": "A", "2": "B", "3": "C" }[event.key];
    updateSelections();
  }
  if (event.key.toLowerCase() === "u") { state.orientation = "upright"; updateSelections(); }
  if (event.key.toLowerCase() === "f") { state.orientation = "fallen"; updateSelections(); }
  if (event.key.toLowerCase() === "l") { state.graspRegion = "lid"; updateSelections(); }
  if (event.key.toLowerCase() === "b") { state.graspRegion = "body"; updateSelections(); }
});

loadItems().catch((error) => {
  $("sampleId").textContent = "불러오기 실패";
  $("saveStatus").textContent = error.message;
});
