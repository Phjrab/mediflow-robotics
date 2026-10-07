const $ = (selector) => document.querySelector(selector);
const messages = $('#messages');
let videoActive = false;
let cameraSignature = '';
let renderedLatestImage = null;
let renderedLatestTrial = null;
const phaseNames = {before_grasp:'접근 전', grasping:'접촉 순간', after_grasp:'파지 완료'};
const medicineNames = {medicine_a:'A 약통', medicine_b:'B 약통', medicine_c:'C 약통', none:'약통 없음', multiple:'여러 약통'};
const stateNames = {upright:'세워짐', fallen:'쓰러짐', tilted:'기울어짐', occluded:'가려짐', not_visible:'보이지 않음', mixed:'혼합'};
const graspNames = {lid_grasp:'뚜껑 집기', body_grasp:'몸통 집기', rescan:'다시 확인', stop:'중지'};
const resultNames = {success:'성공', failure:'실패', human_intervention:'사람 개입', not_recorded:'미기록'};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

function showMessage(text, ok = false) {
  messages.className = ok ? 'messages-ok' : 'messages-error';
  messages.textContent = text;
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error((data.errors || [data.error || response.statusText]).join(' '));
  }
  return data;
}

function formPayload() {
  const data = Object.fromEntries(new FormData($('#capture-form')).entries());
  data.needs_approval = $('#capture-form [name=needs_approval]').checked;
  return data;
}

function cameraCard(camera) {
  const state = camera.connected
    ? '<span class="badge good">연결됨</span>'
    : '<span class="badge bad">연결 안 됨</span>';
  const image = `<img src="/video_feed/${camera.id}" alt="${camera.name} 미리보기">`;
  return `<article class="camera-tile"><h3>${camera.name}</h3>${image}<div>${state} <span>${camera.device}</span></div><small>${camera.error || `${camera.width}×${camera.height} · ${camera.fps} FPS`}</small></article>`;
}

function renderLatest(latest) {
  const card = $('#latest-card');
  const identity = latest ? `${latest.image}:${latest.captured_at}` : null;
  if (identity === renderedLatestImage) return;
  renderedLatestImage = identity;
  if (!latest) {
    card.innerHTML = '<strong>최근 저장 이미지</strong><span>없음</span>';
    return;
  }
  const name = latest.image.split('/').pop();
  card.innerHTML = `<strong>최근 저장 이미지</strong><img src="/data/pilot/images/${encodeURIComponent(name)}?t=${Date.now()}" alt="${name}"><span>${name} · ${latest.captured_at}</span>`;
}

function renderLatestTrial(trial) {
  const card = $('#latest-trial-card');
  const identity = trial ? `${trial.video}:${trial.ended_at}` : null;
  if (identity === renderedLatestTrial) return;
  renderedLatestTrial = identity;
  if (!trial) {
    card.innerHTML = '<strong>최근 저장 영상</strong><span>없음</span>';
    return;
  }
  const name = trial.video.split('/').pop();
  card.innerHTML = `<strong>최근 저장 영상</strong><video controls preload="metadata" src="/data/pilot/videos/${encodeURIComponent(name)}"></video><span>${name} · ${trial.duration_seconds}초 · 핵심 프레임 ${trial.keyframes.length}장</span>`;
}

function renderVideoStatus(video) {
  videoActive = Boolean(video && video.active);
  const state = $('#video-state');
  const marks = new Set((video && video.marked_phases) || []);
  state.textContent = videoActive ? '● 녹화 중' : '대기 중';
  state.className = `recording-state ${videoActive ? 'live' : 'idle'}`;
  $('#video-start').disabled = videoActive;
  $('#video-stop').disabled = !videoActive;
  $('#capture').disabled = videoActive;
  document.querySelectorAll('.video-mark').forEach((button) => {
    button.disabled = !videoActive;
    const base = button.dataset.phase === 'before_grasp'
      ? '접근 전 표시'
      : button.dataset.phase === 'grasping'
        ? '접촉 순간 표시'
        : '파지 완료 표시';
    button.textContent = `${marks.has(button.dataset.phase) ? '✓ ' : ''}${base}`;
  });
  document.querySelectorAll('#capture-form select, #capture-form input').forEach((control) => {
    control.disabled = videoActive;
  });
  document.querySelectorAll('.recording-delete').forEach((button) => {
    button.disabled = videoActive;
  });
  if (!videoActive) {
    $('#video-progress').textContent = '아직 녹화 중이 아닙니다.';
    return;
  }
  const names = {before_grasp:'접근 전', grasping:'접촉', after_grasp:'파지 완료'};
  const markedText = [...marks].map((phase) => names[phase]).join(', ') || '없음';
  $('#video-progress').textContent = `${video.trial_id} · ${video.elapsed_seconds}초 · ${video.frame_count}프레임 · 표시: ${markedText}${video.error ? ` · ${video.error}` : ''}`;
}

function renderTrials(trials) {
  const list = $('#recordings-list');
  if (!trials.length) {
    list.innerHTML = '<p class="recordings-empty">아직 저장된 동작 영상이 없습니다.</p>';
    return;
  }
  list.innerHTML = trials.map((trial) => {
    const answer = trial.answer || {};
    const ended = trial.ended_at ? new Date(trial.ended_at).toLocaleString('ko-KR') : '';
    const keyframes = (trial.keyframes || []).map((frame) => `<figure class="keyframe"><img loading="lazy" src="${escapeHtml(frame.image_url)}" alt="${escapeHtml(phaseNames[frame.capture_phase] || frame.capture_phase)}"><figcaption><strong>${escapeHtml(phaseNames[frame.capture_phase] || frame.capture_phase)}</strong><br>${Number(frame.offset_seconds || 0).toFixed(1)}초 · ${frame.selection === 'manual_marker' ? '직접 표시' : '자동 추출'}</figcaption></figure>`).join('');
    return `<article class="recording-card"><div class="recording-card-head"><div><h3>${escapeHtml(trial.trial_id)}</h3><p>${escapeHtml(ended)} · ${Number(trial.duration_seconds || 0).toFixed(1)}초 · ${escapeHtml(trial.camera)}</p></div><button class="recording-delete" data-trial-id="${escapeHtml(trial.trial_id)}" data-video="${escapeHtml(trial.video)}" ${videoActive ? 'disabled' : ''}>영상 삭제</button></div><video controls preload="metadata" src="${escapeHtml(trial.video_url)}"></video><div class="recording-tags"><span class="recording-tag">${escapeHtml(medicineNames[answer.target_class] || answer.target_class)}</span><span class="recording-tag">${escapeHtml(stateNames[answer.object_state] || answer.object_state)}</span><span class="recording-tag">${escapeHtml(graspNames[answer.grasp_strategy] || answer.grasp_strategy)}</span><span class="recording-tag">${escapeHtml(resultNames[answer.action_result] || answer.action_result)}</span></div><div class="keyframe-grid">${keyframes}</div></article>`;
  }).join('');
  document.querySelectorAll('.recording-delete').forEach((button) => {
    button.addEventListener('click', () => deleteTrial(button.dataset.trialId, button.dataset.video));
  });
}

async function loadTrials() {
  try {
    const data = await jsonFetch('/api/video/trials');
    renderTrials(data.trials || []);
  } catch (error) {
    $('#recordings-list').innerHTML = `<p class="recordings-empty">목록을 불러오지 못했습니다: ${escapeHtml(error.message)}</p>`;
  }
}

async function deleteTrial(trialId, video) {
  const confirmed = window.confirm(`${trialId} 영상과 연결된 핵심 프레임 3장을 촬영 목록에서 삭제합니다.\n\n원본은 data/pilot/trash에 보관되어 복구할 수 있습니다.\n계속할까요?`);
  if (!confirmed) return;
  try {
    const data = await jsonFetch(`/api/video/trials/${encodeURIComponent(trialId)}`, {
      method: 'DELETE',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({video}),
    });
    showMessage(`${trialId}을 촬영 목록에서 삭제했습니다. 원본은 ${data.deleted.archived_to}에 보관했습니다.`, true);
    renderedLatestImage = undefined;
    renderedLatestTrial = undefined;
    await Promise.all([loadTrials(), refreshStatus()]);
  } catch (error) {
    showMessage(error.message);
  }
}

async function refreshStatus() {
  try {
    const data = await jsonFetch('/api/status');
    const signature = JSON.stringify(data.cameras.map((camera) => [camera.id, camera.connected, camera.error]));
    if (signature !== cameraSignature) {
      cameraSignature = signature;
      $('#camera-grid').innerHTML = data.cameras.map(cameraCard).join('');
      const select = $('#capture-camera');
      const selected = select.value || 'wrist';
      select.innerHTML = data.cameras.map((camera) => `<option value="${camera.id}" ${camera.id === selected ? 'selected' : ''}>${camera.name} (${camera.connected ? '연결됨' : '연결 안 됨'})</option>`).join('');
    }
    $('#camera-error').textContent = data.cameras.filter((camera) => !camera.connected).map((camera) => `${camera.name}: ${camera.error}`).join(' | ');
    renderLatest(data.latest);
    renderLatestTrial(data.latest_trial);
    renderVideoStatus(data.video);
  } catch (error) {
    $('#camera-error').textContent = `상태 확인 실패: ${error.message}`;
  }
}

function updateHelpAlert() {
  const stop = $('[name=grasp_strategy]').value === 'stop'
    || $('[name=required_skill]').value === 'stop_and_request_help'
    || $('[name=action_result]').value === 'human_intervention';
  $('#help-alert').hidden = !stop;
}

$('#capture-form').addEventListener('change', updateHelpAlert);
updateHelpAlert();

$('#video-start').addEventListener('click', async () => {
  const button = $('#video-start');
  button.disabled = true;
  try {
    const data = await jsonFetch('/api/video/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(formPayload()),
    });
    renderVideoStatus(data.video);
    showMessage(`${data.video.trial_id} 녹화를 시작했습니다. 접근·접촉·파지 완료 순간을 표시하세요.`, true);
  } catch (error) {
    showMessage(error.message);
    button.disabled = false;
  }
});

document.querySelectorAll('.video-mark').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      const data = await jsonFetch('/api/video/mark', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({capture_phase: button.dataset.phase}),
      });
      renderVideoStatus(data.video);
      showMessage(`${button.textContent.replace('✓ ', '')} 시점을 저장했습니다.`, true);
    } catch (error) {
      showMessage(error.message);
    }
  });
});

$('#video-stop').addEventListener('click', async () => {
  const button = $('#video-stop');
  button.disabled = true;
  try {
    const data = await jsonFetch('/api/video/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action_result: $('#video-result').value}),
    });
    renderVideoStatus({active: false});
    renderLatestTrial(data.trial);
    renderLatest(data.annotations[data.annotations.length - 1]);
    showMessage(`${data.trial.video}와 핵심 프레임 ${data.annotations.length}장을 저장했습니다.`, true);
    await loadTrials();
  } catch (error) {
    showMessage(error.message);
    button.disabled = !videoActive;
  }
});

$('#capture').addEventListener('click', async () => {
  const button = $('#capture');
  button.disabled = true;
  try {
    const data = await jsonFetch('/api/capture', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(formPayload()),
    });
    renderLatest(data.annotation);
    showMessage(`${data.annotation.image} 한 장을 저장했습니다.`, true);
  } catch (error) {
    showMessage(error.message);
  } finally {
    button.disabled = videoActive;
  }
});

$('#retake').addEventListener('click', () => showMessage('라벨을 확인한 뒤 사진 저장 또는 영상 녹화를 시작하세요. 기존 촬영은 유지됩니다.', true));
$('#refresh-trials').addEventListener('click', loadTrials);

$('#delete').addEventListener('click', async () => {
  try {
    const data = await jsonFetch('/api/latest');
    if (!data.latest) return showMessage('삭제할 촬영이 없습니다.');
    if (!window.confirm(`최근 사진 한 건만 삭제합니다.\n\n${data.latest.image}\n계속할까요?`)) return;
    await jsonFetch('/api/latest', {
      method:'DELETE',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:data.latest.image}),
    });
    showMessage(`${data.latest.image}를 삭제했습니다.`, true);
    await refreshStatus();
  } catch (error) {
    showMessage(error.message);
  }
});

$('#stats').addEventListener('click', async () => {
  try {
    const data = await jsonFetch('/api/stats');
    const rows = Object.entries(data.counts).sort().map(([key, value]) => `<div class="stat-row"><span>${key}</span><strong>${value}</strong></div>`).join('');
    const healthy = !data.missing_images.length && !data.orphaned_images.length && !data.missing_videos.length && !data.orphaned_videos.length;
    $('#stats-content').innerHTML = `${rows}<div class="stat-row"><span>총 학습용 이미지</span><strong>${data.total}</strong></div><div class="stat-row"><span>총 동작 영상</span><strong>${data.video_trials}</strong></div><p class="${healthy?'integrity-ok':'integrity-bad'}">${healthy?'✓ JSONL과 이미지·영상 경로가 일치합니다.':'이미지 또는 영상 경로 불일치가 있습니다.'}</p>`;
    $('#stats-dialog').showModal();
  } catch (error) {
    showMessage(error.message);
  }
});

$('#shutdown').addEventListener('click', async () => {
  if (!window.confirm('카메라를 해제하고 촬영 서버를 종료할까요?')) return;
  try {
    const data = await jsonFetch('/api/shutdown', {method:'POST'});
    showMessage(data.message, true);
  } catch (error) {
    showMessage(error.message);
  }
});

async function loadMapping() {
  try {
    const mapping = await jsonFetch('/api/mapping');
    $('#mapping').textContent = `약통-바구니 설정 · ${Object.entries(mapping).map(([medicine, basket]) => `${medicine}: ${basket || '미확정 — 촬영 시 직접 선택'}`).join(' · ')}`;
  } catch (error) {
    $('#mapping').textContent = `매핑 설정 읽기 실패: ${error.message}`;
  }
}

loadMapping();
refreshStatus();
loadTrials();
setInterval(refreshStatus, 3000);
