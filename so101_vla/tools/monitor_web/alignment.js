(() => {
  const controls = document.getElementById("alignment-visible");
  const opacity = document.getElementById("alignment-opacity");
  const replayToggle = document.getElementById("replay-target");
  const figures = [...document.querySelectorAll("[data-camera]")];
  let metadata;
  let replayTarget;
  let previewActive = false;
  const cards = new Map();
  const fmt = (value, unit, digits=1) => Number.isFinite(value) ? value.toFixed(digits)+unit : "미측정";
  const offset = (dx, dy) => [dx === 0 ? "가로 0" : (dx > 0 ? "오른쪽 " : "왼쪽 ")+Math.abs(dx).toFixed(1),
    dy === 0 ? "세로 0" : (dy > 0 ? "아래 " : "위 ")+Math.abs(dy).toFixed(1)].join(" / ")+" px";
  const objectLabels = {bottle_cap: "약통 A 뚜껑", basket_opening: "파란 바구니 입구", basket_tag: "바구니 앞 표식"};
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
    card.canvas.hidden = !controls.checked || !previewActive;
    if (card.canvas.hidden) return;
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
    for (const point of card.points ?? []) {
      const [x,y] = point.reference_xy;
      const [u,v] = point.current_xy;
      context.strokeStyle = "#00f7ff"; context.lineWidth = 2;
      context.beginPath(); context.moveTo(x-7,y); context.lineTo(x+7,y);
      context.moveTo(x,y-7); context.lineTo(x,y+7); context.stroke();
      context.strokeStyle = "#ff4d9a"; context.beginPath();
      context.arc(u,v,6,0,Math.PI*2); context.stroke();
    }
    const target = replayTarget?.first_cap_xy?.[role];
    if (replayToggle.checked && target) {
      const [x, y] = target;
      context.strokeStyle = "#ffd34f"; context.lineWidth = 3;
      context.beginPath(); context.moveTo(x-13,y); context.lineTo(x+13,y);
      context.moveTo(x,y-13); context.lineTo(x,y+13); context.stroke();
      const label = "재생 "+replayTarget.episode+" · 약통 A";
      context.font = "bold 13px system-ui"; context.strokeStyle = "#191509";
      context.strokeText(label,x+12,y-13); context.fillStyle = "#ffd34f";
      context.fillText(label,x+12,y-13);
    }
  }
  function paintAll() {
    document.getElementById("alignment-opacity-value").textContent = opacity.value+"%";
    for (const role of cards.keys()) paint(role);
  }
  controls.addEventListener("change", paintAll); opacity.addEventListener("input", paintAll);
  replayToggle.addEventListener("change", () => {
    if (replayToggle.checked) controls.checked = true;
    paintAll();
  });
  window.alignmentUi = {
    update(data, active) {
      previewActive = Boolean(data.fresh && active);
      metadata = data.reference;
      replayTarget = data.replay_target;
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
      state.textContent = previewActive ? "실시간 카메라 3대 · 로봇 쓰기 0회" :
        data.error?.reason ? "카메라 미리보기 오류 · "+data.error.reason :
        "정렬 미리보기 대기 · 실행 중에는 실행기 영상을 표시합니다";
      state.dataset.tone = previewActive ? "ok" : "warning";
      for (const [role, card] of cards) {
        const row = data.cameras?.[role];
        card.points = row?.wrist_landmarks ?? Object.values(row?.objects ?? {});
        card.metric.dataset.tone = previewActive && row?.within_target ? "ok" : "warning";
        if (!previewActive) card.metric.textContent = "현재 정렬 수치 대기";
        else if (role === "end_effector") card.metric.textContent = card.points.length === 2 ?
          card.points.map((p,i) => (i ? "오른쪽 병 끝 " : "왼쪽 병 끝 ")+fmt(p.distance,"px")+" ("+offset(p.dx,p.dy)+")").join(" · ") :
          "손목 구도 미측정 · 화면 상단 두 병의 끝점이 필요합니다";
        else if (row?.status !== "measured") card.metric.textContent = "고정 기준판 마커 부족 · 마커가 화면에 들어오게 맞추세요";
        else card.metric.textContent = "차이 "+fmt(row.position_difference_px,"px")+
          " / 목표 ≤20px · 회전 "+fmt(row.rotation_deg,"°")+
          " · 가로 "+fmt(row.shift_x_px,"px")+" / 세로 "+fmt(row.shift_y_px,"px")+
          " · 배율 "+fmt(row.scale,"배",2)+" · 마커 "+row.common_ids.length+"/4"+
          (row.within_target ? " · 위치 차이 OK" : " · 조정 필요");
        if (previewActive && role !== "end_effector") {
          const target = replayTarget?.first_cap_xy?.[role];
          const cap = row?.objects?.bottle_cap?.current_xy;
          if (replayToggle.checked && target && cap) {
            card.metric.append(document.createElement("br"), "재생 "+replayTarget.episode+" 약통 A · "+
              offset(cap[0]-target[0], cap[1]-target[1]));
          }
          for (const [key,label] of Object.entries(objectLabels)) {
            const p = row?.objects?.[key];
            card.metric.append(document.createElement("br"), label+" · "+(p ?
              fmt(p.distance,"px")+" ("+offset(p.dx,p.dy)+")" : "미측정"));
          }
        }
      }
      paintAll();
    },
    unavailable() {
      previewActive = false;
      paintAll();
      document.getElementById("alignment-status").textContent = "정렬 서버 응답 대기";
      for (const card of cards.values()) card.metric.textContent = "미측정 · 연결 확인 중";
    },
  };
})();
