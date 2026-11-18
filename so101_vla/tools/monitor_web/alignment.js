(() => {
  const controls = document.getElementById("alignment-visible");
  const opacity = document.getElementById("alignment-opacity");
  const figures = [...document.querySelectorAll("[data-camera]")];
  let metadata;
  const cards = new Map();
  const fmt = (value, unit, digits=1) => Number.isFinite(value) ? value.toFixed(digits)+unit : "미측정";
  for (const figure of figures) {
    const role = figure.dataset.camera;
    const canvas = document.createElement("canvas");
    canvas.width = 640; canvas.height = 480; canvas.className = "alignment-layer";
    canvas.setAttribute("aria-hidden", "true");
    figure.querySelector(".frame").append(canvas);
    const options = document.createElement("div"); options.className = "alignment-options";
    const label = document.createElement("label");
    const visible = document.createElement("input"); visible.type = "checkbox"; visible.checked = true;
    label.append(visible, " 학습 프레임"); options.append(label);
    const enlarge = document.createElement("button"); enlarge.type = "button"; enlarge.textContent = "확대";
    enlarge.addEventListener("click", () => {
      const active = figure.classList.toggle("is-large"); enlarge.textContent = active ? "축소" : "확대";
    }); options.append(enlarge);
    const contourLabel = document.createElement("label");
    const contours = document.createElement("input"); contours.type = "checkbox"; contours.checked = true;
    if (role === "ceiling_vertical") { contourLabel.append(contours, " 약통·바구니 윤곽"); options.append(contourLabel); }
    const metric = document.createElement("p"); metric.className = "alignment-metric"; metric.textContent = "정렬 수치 대기";
    figure.append(options, metric);
    const reference = new Image(); reference.alt = ""; reference.onload = () => paint(role);
    reference.src = "/api/alignment/reference/"+role+".png";
    cards.set(role, {canvas, visible, contours, metric, reference});
    for (const input of [visible, contours]) input.addEventListener("change", () => paint(role));
  }
  function paint(role) {
    const card = cards.get(role); if (!card) return;
    const context = card.canvas.getContext("2d"); context.clearRect(0, 0, 640, 480);
    card.canvas.hidden = !controls.checked;
    if (!controls.checked) return;
    if (card.visible.checked && card.reference.complete && card.reference.naturalWidth) {
      context.globalAlpha = Number(opacity.value)/100;
      context.drawImage(card.reference, 0, 0, 640, 480);
      context.globalAlpha = 1;
    }
    if (role === "ceiling_vertical" && card.contours.checked) {
      for (const shape of metadata?.vertical_outlines ?? []) {
        context.strokeStyle = shape.color; context.fillStyle = shape.color;
        context.lineWidth = 2; context.font = "bold 12px system-ui";
        context.beginPath(); shape.points.forEach(([x,y], i) => i ? context.lineTo(x,y) : context.moveTo(x,y));
        context.closePath(); context.stroke();
        const [x,y] = shape.label_at; context.strokeStyle = "#10141b"; context.lineWidth = 3;
        context.strokeText(shape.label, x, y); context.fillText(shape.label, x, y);
      }
    }
  }
  function paintAll() {
    document.getElementById("alignment-opacity-value").textContent = opacity.value+"%";
    for (const role of cards.keys()) paint(role);
  }
  controls.addEventListener("change", paintAll); opacity.addEventListener("input", paintAll);
  window.alignmentUi = {
    update(data) {
      metadata = data.reference;
      document.getElementById("alignment-reference").textContent = metadata?.label ?? "학습 기준 프레임 없음";
      const paths = document.getElementById("alignment-paths");
      if (metadata?.cameras && !paths.childElementCount) {
        document.getElementById("alignment-source").textContent = metadata.source_repo_id +
          " · source episode_index="+metadata.source_episode_index+" · frame=0";
        for (const [role, row] of Object.entries(metadata.cameras)) {
          const item = document.createElement("li");
          const link = document.createElement("a"); link.href = "/api/alignment/reference/"+role+".png";
          link.textContent = role+" 첫 프레임";
          const path = document.createElement("code"); path.textContent = row.source_image_path;
          item.append(link, document.createElement("br"), path); paths.append(item);
        }
      }
      const state = document.getElementById("alignment-status");
      state.textContent = data.fresh ? "실시간 카메라 3대 · 로봇 접근/쓰기 없음" :
        data.error?.reason ? "카메라 미리보기 오류 · "+data.error.reason :
        "정렬 미리보기 대기 · 실행 중에는 실행기 영상을 표시합니다";
      state.dataset.tone = data.fresh ? "ok" : "warning";
      for (const [role, card] of cards) {
        const row = data.cameras?.[role];
        card.metric.dataset.tone = data.fresh && row?.within_target ? "ok" : "warning";
        if (!data.fresh) card.metric.textContent = "정렬 수치 대기 · 이전 값을 현재값으로 표시하지 않습니다";
        else if (role === "end_effector") card.metric.textContent = "손목 구도는 겹친 영상으로 맞추세요 · 밝기 " + fmt(row?.brightness, "");
        else if (row?.status !== "measured") card.metric.textContent = "고정 기준판 마커 부족 · 마커가 화면에 들어오게 맞추세요";
        else card.metric.textContent = "차이 "+fmt(row.position_difference_px,"px")+
          " / 목표 ≤20px · 회전 "+fmt(row.rotation_deg,"°")+
          " · 가로 "+fmt(row.shift_x_px,"px")+" / 세로 "+fmt(row.shift_y_px,"px")+
          " · 배율 "+fmt(row.scale,"배",2)+" · 마커 "+row.common_ids.length+"/4"+
          (row.within_target ? " · 위치 차이 OK" : " · 조정 필요");
      }
      paintAll();
    },
    unavailable() {
      document.getElementById("alignment-status").textContent = "정렬 서버 응답 대기";
      for (const card of cards.values()) card.metric.textContent = "미측정 · 연결 확인 중";
    },
  };
})();
