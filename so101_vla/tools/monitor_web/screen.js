const byId = (id) => document.getElementById(id);
const joints = {
  shoulder_pan: "어깨 회전", shoulder_lift: "어깨 들기", elbow_flex: "팔꿈치",
  wrist_flex: "손목 굽힘", wrist_roll: "손목 회전", gripper: "그리퍼",
};
const rows = Object.fromEntries(Object.entries(joints).map(([key, name]) => {
  const row = document.createElement("tr");
  const header = document.createElement("th");
  header.scope = "row";
  header.textContent = name;
  row.append(header);
  const cells = Array.from({length: 8}, () => {
    const cell = document.createElement("td");
    cell.textContent = "—";
    row.append(cell);
    return cell;
  });
  byId("joints").append(row);
  return [key, cells];
}));
const cameras = [...document.querySelectorAll("[data-camera]")];
const labels = {idle: "실행 대기", warming: "모델 준비 중", connecting: "장치 확인 중",
  observing: "카메라 입력 대기", predicting: "추론 중", moving: "동작 중",
  run_complete: "실행 종료", stopped: "운영자 정지", hold: "중단", correcting: "교정 중",
  saving_correction: "교정 영상 저장 중", operator_ready: "운영자 교정 대기"};
let run, observation;
let latest, marked, pendingHold, busy;
let correctionPending, correctionTimer;
let viewingAlignment = false;

function displayStatus(text, tone) {
  byId("status").textContent = text;
  byId("status").dataset.tone = tone;
}

function displayImages(images) {
  for (const figure of cameras) {
    const data = images?.[figure.dataset.camera];
    const image = figure.querySelector("img");
    image.hidden = !data;
    figure.querySelector(".frame span").hidden = !!data;
    if (data) image.src = "data:image/jpeg;base64," + data;
    else image.removeAttribute("src");
  }
}

function number(value, digits = 2, suffix = "") {
  return Number.isFinite(value) ? value.toFixed(digits) + suffix : "—";
}

async function fetchJson(path) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(4000)});
  if (!response.ok) throw new Error(response.status);
  return response.json();
}

async function command(path, data) {
  const response = await fetch(path, {
    method: "POST", signal: AbortSignal.timeout(4000),
    headers: {"Content-Type": "application/json", "X-VLA-Token": latest?.control_token ?? ""},
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "요청을 처리하지 못했습니다");
  return result;
}

function updateControls() {
  const operator = !!latest?.operator;
  byId("operator-access").textContent = operator ? "이 노트북에서 입력 가능" : "보기 전용";
  byId("hold").disabled = !latest?.can_hold || pendingHold === latest?.run;
  byId("mark").disabled = !operator || !observation || observation.preview || viewingAlignment || !!busy;
  byId("instruction").disabled = !operator || !marked || !!busy;
  byId("save").disabled = !operator || !marked || !!busy || !byId("instruction").value.trim();
  const kinds = latest?.correction_kinds ?? [];
  const selected = byId("correction-text").value.trim().replace(/[.!]+$/, "").replace(/\s+/g, " ");
  const kind = {"그리퍼 열어": "gripper_open", "조금 더 앞으로": "forward"}[selected];
  const available = latest?.can_correct && !correctionPending && pendingHold !== latest.run;
  byId("correction-text").disabled = !available;
  byId("supervised").disabled = !available;
  byId("send-correction").disabled = !available || !kinds.includes(kind) || !byId("supervised").checked;
  byId("correction-access").textContent = kinds.includes("forward")
    ? "그리퍼 열어 · 조금 더 앞으로"
    : kinds.includes("gripper_open")
      ? "그리퍼 열어 · 앞으로 경로는 현장 시연을 등록하면 사용 가능"
      : "Jetson에서 현장 교정 모드를 켠 뒤 사용 가능";
}

byId("correction-text").addEventListener("input", updateControls);
byId("supervised").addEventListener("change", updateControls);
byId("correction").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (byId("send-correction").disabled) return;
  const state = latest;
  correctionPending = "sending";
  updateControls();
  try {
    const result = await command("/api/correct", {run: state.run,
      text: byId("correction-text").value, supervised: byId("supervised").checked});
    correctionPending = result.id;
    byId("supervised").checked = false;
    byId("correction-message").textContent = "요청 전달됨 · 동작 확인 중";
    correctionTimer = setTimeout(() => {
      correctionPending = undefined;
      byId("correction-message").textContent = "처리 확인 지연 · 실행 상태를 확인하세요";
      updateControls();
    }, 6000);
  } catch (error) {
    correctionPending = undefined;
    byId("correction-message").textContent = error.message;
  }
  updateControls();
});

function showDiagnosis(status, stale) {
  const recovered = status.motor_recovered_at;
  const diagnosis = stale ? {
    title: "실행 상태 갱신 지연",
    followup: "웹 서버에는 연결되어 있습니다. Jetson 실행기 로그와 장치 응답을 확인하세요.",
  } : recovered ? {
    title: "모터 응답 복구 확인",
    followup: "LeLab에서 팔로워 6축 위치를 읽었습니다 · " + new Date(recovered * 1000).toLocaleString(),
  } : status.diagnosis;
  byId("diagnosis").hidden = !diagnosis;
  byId("diagnosis").dataset.tone = recovered ? "ok" : "warning";
  byId("reason-title").textContent = diagnosis?.title || "";
  byId("reason-tip").textContent = diagnosis?.followup || "";
  byId("reason").textContent = status.reason || status.previous_reason || "";
  byId("raw-reason").hidden = !(status.reason || status.previous_reason);
  byId("raw-reason").querySelector("summary").textContent = recovered ? "이전 오류 원문" : "오류 원문";
  const holdStatus = status.hold?.status;
  byId("hold-result").hidden = !holdStatus;
  byId("hold-result").textContent = holdStatus === "CURRENT_POSITION_GOAL_TRANSMITTED"
    ? "위치 유지 목표를 전송했습니다" : (holdStatus ? "위치 유지 결과 " + holdStatus : "");
}

byId("hold").addEventListener("click", async () => {
  if (!latest?.can_hold || pendingHold === latest.run) return;
  const requestedRun = latest.run;
  pendingHold = requestedRun;
  byId("hold-message").textContent = "정지 요청 전송 중";
  updateControls();
  try {
    await command("/api/hold", {run: requestedRun});
    if (latest?.phase !== "hold") byId("hold-message").textContent = "요청 전달됨 · 실행기 중단 확인 중";
  } catch (error) {
    pendingHold = undefined;
    byId("hold-message").textContent = "정지 확인 실패 · " + error.message;
    updateControls();
  }
});

byId("mark").addEventListener("click", async () => {
  if (!observation || busy) return;
  busy = true;
  updateControls();
  try {
    marked = await command("/api/mark", {run: observation.run, capture: observation.capture});
    byId("marked").textContent = "고정됨 · 입력 " + marked.capture + " · 전송 스텝 " + (marked.step ?? "—");
    byId("feedback-message").textContent = "";
    byId("download").hidden = true;
  } catch (error) {
    byId("feedback-message").textContent = error.message;
  } finally {
    busy = false;
    updateControls();
  }
});

byId("instruction").addEventListener("input", updateControls);
byId("feedback").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!marked || busy) return;
  busy = true;
  updateControls();
  try {
    const result = await command("/api/feedback", {id: marked.id, text: byId("instruction").value});
    byId("download").href = "/api/feedback/" + result.id;
    byId("download").hidden = false;
    byId("feedback-message").textContent = "Jetson에 저장됨 · 다음 시연에 쓸 교정 메모";
    byId("instruction").value = "";
    marked = undefined;
  } catch (error) {
    byId("feedback-message").textContent = error.message;
  } finally {
    busy = false;
    updateControls();
  }
});

async function refresh() {
  try {
    const status = await fetchJson("/api/status");
    latest = status;
    if (status.run !== run) {
      run = status.run;
      observation = undefined;
      displayImages();
      byId("hold-message").textContent = "";
      byId("correction-message").textContent = "";
      correctionPending = undefined;
      clearTimeout(correctionTimer);
    }
    const finished = ["hold", "run_complete", "stopped"].includes(status.phase);
    const age = status.server_time - status.updated;
    const stale = !finished && status.phase !== "idle" && age > 3;
    displayStatus(status.historical ? "대기 · 이전 실행 기록" : stale ? "갱신 지연" : labels[status.phase] ?? "상태 확인 중",
      stale || status.phase === "hold" ? "warning" : (finished || status.phase === "idle" ? "" : "active"));
    byId("task").textContent = status.task || "VLA 실행을 기다리는 중";
    byId("step").textContent = number(status.step, 0);
    byId("inference").textContent = number(status.inference_seconds, 2, "초");
    byId("joint-source").textContent = finished || stale || status.historical
      ? "아래 관절값은 지난 실행의 기록입니다. 정렬 미리보기는 현재 관절값을 읽지 않습니다."
      : "실행기가 마지막으로 게시한 관절값입니다.";
    showDiagnosis(status, stale);
    if (pendingHold && pendingHold === run && finished) {
      byId("hold-message").textContent = status.reason?.includes("관제 화면") ?
        "실행기가 정지를 처리했습니다" : "실행 종료 · 중단 사유를 확인하세요";
    }
    const correction = status.correction_result;
    if (correction && correction.id === correctionPending) {
      correctionPending = undefined;
      clearTimeout(correctionTimer);
      byId("correction-message").textContent = correction.reason || "교정 동작 완료";
    }
    const clip = status.latest_clip;
    byId("clip-links").hidden = !clip || !status.operator;
    if (clip && status.operator) {
      byId("clip-status").textContent = "최근 교정 녹화 · " + clip.frames + "프레임";
      for (const link of document.querySelectorAll("[data-clip]")) {
        link.href = "/api/corrections/" + clip.id + "/" + link.dataset.clip;
      }
    }
    if (status.capture && (observation?.capture ?? 0) < status.capture) {
      const candidate = await fetchJson("/api/observation");
      if (candidate.run === run && candidate.capture >= status.capture) {
        observation = candidate;
        displayImages(candidate.images);
      }
    }
    byId("capture-age").textContent = observation ?
      (observation.preview ? "카메라 확인 영상 · " : finished || stale ? "마지막 입력 · " : "") +
      number(Math.max(0, status.server_time - observation.captured), 1, "초 전") : "—";
    if (window.alignmentUi) {
      try {
        const alignment = await fetchJson("/api/alignment");
        const wasViewing = viewingAlignment;
        const running = !finished && status.phase !== "idle" && !stale;
        viewingAlignment = alignment.fresh && !running;
        window.alignmentUi.update(alignment, viewingAlignment);
        if (viewingAlignment) {
          displayImages(alignment.images);
          byId("capture-age").textContent = "정렬 미리보기 · " + number(alignment.age_s, 1, "초 전");
        } else if (wasViewing) displayImages(observation?.images);
      } catch {
        window.alignmentUi.unavailable();
        if (viewingAlignment) displayImages(observation?.images);
        viewingAlignment = false;
      }
    }
    for (const [joint, cells] of Object.entries(rows)) {
      const i = status.joints?.indexOf(joint);
      const unit = joint === "gripper" ? "점" : "°";
      const values = [
        number(observation?.state?.[i], 2, unit), number(status.state?.[i], 2, unit),
        number(status.ticks?.[joint], 0), number(status.model?.[i], 2, unit),
        number(status.sent?.[i], 2, unit), number(status.temperature?.[joint], 0, "°C"),
        number(status.thermal_diagnostics?.[joint]?.filtered_c, 0, "°C"),
        number(status.voltage_v?.[joint], 1, "V"),
      ];
      cells.forEach((cell, index) => { cell.textContent = values[index]; });
    }
  } catch {
    window.alignmentUi?.unavailable();
    latest = undefined;
    displayStatus("연결 끊김", "warning");
    byId("capture-age").textContent = observation ? "마지막 입력 · 연결 끊김" : "—";
    showDiagnosis({diagnosis: {title: "관제 서버 응답 없음",
      followup: "노트북과 Jetson의 Tailscale 연결 또는 Jetson 관제 서비스를 확인하세요. 팔 동작 여부는 현장에서 확인해야 합니다."}}, false);
  } finally {
    updateControls();
    setTimeout(refresh, 500);
  }
}

refresh();
