(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (n) => new Intl.NumberFormat("vi-VN").format(n || 0);
  const stageNames = {email:"Tạo Google giả lập",verification:"Xác minh email nội bộ",social:"Tạo TikTok giả lập",interactions:"Tương tác video giả lập",completed:"Hoàn tất"};
  const statusNames = {queued:"Đang chờ",email_created:"Email giả lập",email_verified:"Đã xác minh giả",social_created:"Hồ sơ giả lập",completed:"Hoàn tất",running:"Đang chạy",paused:"Tạm dừng",canceled:"Đã dừng",failed:"Lỗi"};
  const kindNames = {run_started:"Bắt đầu",email_created:"Tạo Google giả lập",email_verified:"Xác minh Google giả lập",social_created:"Tạo TikTok giả lập",view:"Xem video giả lập",like:"Thích video giả lập",follow:"Theo dõi giả lập",run_completed:"Hoàn tất"};
  const state = {csrf:"",runId:null,view:"overview",accountOffset:0,accountTotal:0,runs:[],timer:null,toastTimer:null,searchTimer:null};

  async function api(path, options={}) {
    const headers = {...(options.headers || {})};
    if (options.method === "POST") { headers["Content-Type"] = "application/json"; headers["X-Lab-CSRF"] = state.csrf; }
    const res = await fetch(path, {...options, headers, cache:"no-store", credentials:"same-origin"});
    const type = res.headers.get("content-type") || "";
    const data = type.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new Error(data?.error || `Yêu cầu lỗi (${res.status})`);
    return data;
  }
  function toast(message) {
    const node = $("#toast"); node.textContent = message; node.classList.add("show");
    clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => node.classList.remove("show"), 2800);
  }
  function setView(view) {
    state.view = view;
    $$(".page-view").forEach((node) => node.classList.toggle("hidden", node.id !== `view-${view}`));
    $$(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
    $("#pageName").textContent = ({overview:"Tổng quan",accounts:"Tài khoản giả lập",compare:"So sánh thử nghiệm"})[view];
    if (view === "accounts") loadAccounts();
    if (view === "compare") renderCompareOptions();
    location.hash = view;
  }
  function displayRun(id) { state.runId = id; state.accountOffset = 0; refresh(); }
  function renderRunList(runs) {
    const el = $("#runList");
    if (!runs.length) { el.innerHTML = '<div class="empty-side">Chưa có thử nghiệm</div>'; return; }
    el.innerHTML = runs.slice(0, 15).map((r) => `<button class="run-link ${r.id === state.runId ? "selected" : ""}" data-run="${escapeHtml(r.id)}"><b>${escapeHtml(r.name)}</b><small><i class="status-dot ${escapeHtml(r.status)}"></i>${escapeHtml(statusNames[r.status] || r.status)} · ${r.progress.percent}%</small></button>`).join("");
    $$(".run-link").forEach((node) => node.addEventListener("click", () => displayRun(node.dataset.run)));
  }
  function updateSummary(run) {
    const empty = !run;
    $("#emptyState").classList.toggle("hidden", !empty);
    $("#dashboard").classList.toggle("hidden", empty);
    if (!run) return;
    $("#selectedName").textContent = run.name;
    $("#runLabel").textContent = statusNames[run.status] || run.status;
    $("#statusDot").className = `status-dot ${run.status}`;
    $("#progressText").textContent = `${fmt(run.progress.done)} / ${fmt(run.progress.total)}`;
    $("#progressBar").style.width = `${run.progress.percent}%`;
    $("#stageText").textContent = stageNames[run.stage] || run.stage;
    $("#seedText").textContent = `Seed: ${run.seed} · ${run.config.account_count} tài khoản · ${run.config.views_per_account} lượt xem/tài khoản`;
    $("#finishedText").textContent = run.status === "completed" ? "Đã hoàn tất" : run.status === "paused" ? "Đã lưu checkpoint" : run.status === "canceled" ? "Đã giữ lại kết quả từng phần" : "Thử nghiệm đang cập nhật";
    $("#metricEmails").textContent = fmt(run.metrics.emails);
    $("#verifiedCaption").textContent = `${fmt(run.metrics.verified_emails)} đã xác minh nội bộ`;
    $("#metricAccounts").textContent = fmt(run.metrics.social_accounts);
    $("#metricViews").textContent = fmt(run.metrics.views);
    $("#metricLikes").textContent = fmt(run.metrics.likes);
    $("#metricFollows").textContent = fmt(run.metrics.follows);
    $("#pauseBtn").classList.toggle("hidden", run.status !== "running");
    $("#resumeBtn").classList.toggle("hidden", run.status !== "paused");
    $("#cancelBtn").classList.toggle("hidden", !["running","paused"].includes(run.status));
    $("#repeatBtn").disabled = !["completed","canceled"].includes(run.status);
    $("#reportLink").href = `/api/runs/${encodeURIComponent(run.id)}/report.json`;
    $("#eventsLink").href = `/api/runs/${encodeURIComponent(run.id)}/events.csv`;
    $("#accountsCsv").href = `/api/runs/${encodeURIComponent(run.id)}/accounts.csv`;
    $("#accountsCsv").classList.remove("disabled-link");
    $("#creatorCount").textContent = fmt(run.creators.length);
    $("#videoCount").textContent = `${fmt(run.videos.length)} video`;
    renderCreators(run.creators);
    renderVideos(run.videos);
    renderEvents(run.recent_events);
    renderChart(run.timeline);
    $("#fingerprintBox").classList.toggle("hidden", !run.fingerprint);
    $("#fingerprintText").textContent = run.fingerprint || "";
  }
  function renderCreators(creators) {
    $("#creatorList").innerHTML = creators.map((c) => `<div class="creator-row"><div class="avatar">${escapeHtml(c.handle.slice(-2))}</div><div class="creator-info"><b>${escapeHtml(c.handle)}</b><small>Hồ sơ giả lập</small></div><div class="creator-followers">${fmt(c.followers)}<small>theo dõi</small></div></div>`).join("") || '<div class="no-mail">Chưa có dữ liệu</div>';
  }
  function renderVideos(videos) {
    $("#videoRows").innerHTML = videos.map((v) => `<tr><td><div class="video-title"><span class="video-thumb">▷</span><span class="video-name">${escapeHtml(v.title)}</span><span class="synthetic-tag">giả lập</span></div></td><td>${escapeHtml(v.creator_handle)}</td><td>${fmt(v.views)}</td><td>${fmt(v.likes)}</td></tr>`).join("") || '<tr><td colspan="4">Chưa có video trong thử nghiệm.</td></tr>';
  }
  function renderEvents(events) {
    const icons = {view:"▷",like:"♡",follow:"＋",email_created:"✉",email_verified:"✓",social_created:"♙",run_started:"▶",run_completed:"✓"};
    $("#eventList").innerHTML = events.slice(0, 12).map((e) => `<div class="event-row"><div class="event-icon ${escapeHtml(e.kind)}">${icons[e.kind] || "·"}</div><div class="event-copy"><b>${escapeHtml(e.actor_handle || "Phòng thử nghiệm")}</b> · ${escapeHtml(kindNames[e.kind] || e.kind)}<small>${escapeHtml(e.target_label)} — ${escapeHtml(e.message)}</small></div><span class="event-time">Bước ${fmt(e.step)}</span></div>`).join("") || '<div class="no-mail">Chưa phát sinh hoạt động.</div>';
  }
  function renderChart(points) {
    const svg = $("#activityChart"), w=700, h=210, pad={l:34,r:8,t:10,b:24};
    if (!points?.length) { svg.innerHTML = '<text x="350" y="105" text-anchor="middle" class="chart-empty">Chưa có hoạt động mô phỏng</text>'; return; }
    const series = [["views","line-views"],["likes","line-likes"],["follows","line-follows"]];
    const max = Math.max(1, ...points.flatMap((p) => series.map(([key]) => p[key])));
    const x = (i) => pad.l + (points.length < 2 ? 0 : i*(w-pad.l-pad.r)/(points.length-1));
    const y = (v) => h-pad.b - (v/max)*(h-pad.t-pad.b);
    let content = "";
    for (let n=0;n<4;n++) { const yy=pad.t+n*(h-pad.t-pad.b)/3; content += `<line x1="${pad.l}" x2="${w-pad.r}" y1="${yy}" y2="${yy}" class="chart-grid"/><text x="${pad.l-8}" y="${yy+3}" text-anchor="end" class="chart-label">${fmt(Math.round(max*(3-n)/3))}</text>`; }
    for (const [key, cls] of series) {
      const d = points.map((p,i) => `${i ? "L":"M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
      content += `<path d="${d}" class="chart-line ${cls}"/>`;
    }
    content += `<text x="${pad.l}" y="${h-5}" class="chart-label">Bước ${fmt(points[0].step)}</text><text x="${w-pad.r}" y="${h-5}" text-anchor="end" class="chart-label">Bước ${fmt(points.at(-1).step)}</text>`;
    svg.innerHTML = content;
  }
  async function refresh() {
    try {
      const data = await api(`/api/state${state.runId ? `?run_id=${encodeURIComponent(state.runId)}` : ""}`);
      state.csrf = data.csrf_token; state.runs = data.runs;
      if (!state.runId && data.selected) state.runId = data.selected.id;
      if (state.runId && data.selected?.id !== state.runId) {
        const other = data.runs.find((r) => r.id === state.runId);
        if (other) state.runId = other.id; else state.runId = data.selected?.id || null;
      }
      renderRunList(data.runs);
      updateSummary(data.selected?.id === state.runId ? data.selected : null);
      if (state.view === "accounts" && state.runId) await loadAccounts();
      if (state.view === "compare") renderCompareOptions();
      $("#newRunTop").disabled = false;
    } catch (error) { toast(error.message || "Không thể kết nối dịch vụ mô phỏng cục bộ"); }
  }
  async function loadAccounts() {
    if (!state.runId) { $("#accountRows").innerHTML = '<tr><td colspan="6">Tạo thử nghiệm để xem tài khoản giả lập.</td></tr>'; return; }
    const q = encodeURIComponent($("#accountSearch").value.trim());
    const status = encodeURIComponent($("#accountStatus").value);
    try {
      const data = await api(`/api/runs/${encodeURIComponent(state.runId)}/accounts?offset=${state.accountOffset}&limit=25&q=${q}&status=${status}`);
      state.accountTotal = data.total;
      $("#accountTotal").textContent = `${fmt(data.total)} tài khoản`;
      $("#pageInfo").textContent = `Trang ${Math.floor(state.accountOffset/25)+1} / ${Math.max(1,Math.ceil(data.total/25))}`;
      $("#prevPage").disabled = state.accountOffset === 0;
      $("#nextPage").disabled = state.accountOffset + 25 >= data.total;
      $("#accountRows").innerHTML = data.accounts.map((a) => `<tr><td><div class="account-name"><span class="account-avatar">♙</span><span><b>${escapeHtml(a.fake_handle)} <span class="synthetic-tag">giả lập</span></b><small>${escapeHtml(a.fake_email)}</small></span></div></td><td><span class="status-badge ${escapeHtml(a.stage)}">${escapeHtml(statusNames[a.stage] || a.stage)}</span></td><td>${fmt(a.views)}</td><td>${fmt(a.likes)}</td><td>${fmt(a.follows)}</td><td><button class="mail-link" data-mail="${escapeHtml(a.id)}">Hộp thư</button></td></tr>`).join("") || '<tr><td colspan="6">Không có tài khoản khớp bộ lọc.</td></tr>';
      $$("[data-mail]").forEach((button) => button.addEventListener("click", () => openMailbox(button.dataset.mail)));
    } catch (error) { toast(error.message); }
  }
  async function openMailbox(actorId) {
    try {
      const box = await api(`/api/runs/${encodeURIComponent(state.runId)}/mailboxes/${encodeURIComponent(actorId)}`);
      $("#mailAddress").textContent = box.address;
      $("#mailBody").innerHTML = box.messages.length ? box.messages.map((m) => `<article class="mail-message"><b>${escapeHtml(m.subject)}</b><p>${escapeHtml(m.body)}</p><span class="mail-code">${escapeHtml(m.code)}</span></article>`).join("") : '<div class="no-mail">Hộp thư chưa có thư. Tiến trình mô phỏng sẽ tạo thư giả lập ở bước email.</div>';
      $("#mailDialog").showModal();
    } catch (error) { toast(error.message); }
  }
  function renderCompareOptions() {
    const completed = state.runs.filter((r) => r.status === "completed");
    for (const sel of [$("#compareA"), $("#compareB")]) {
      const old = sel.value;
      sel.innerHTML = completed.map((r) => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name)} · seed ${r.seed}</option>`).join("");
      if (completed.some((r) => r.id === old)) sel.value = old;
      sel.disabled = completed.length < 2;
    }
    $("#compareBtn").disabled = completed.length < 2;
  }
  async function compareRuns() {
    const left=$("#compareA").value,right=$("#compareB").value;
    if (!left || !right || left === right) return toast("Chọn hai lần chạy khác nhau.");
    try {
      const c = await api(`/api/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`).catch(async (err) => {
        // Compare uses a query endpoint; on older builds this message guides the user.
        throw err;
      });
      showComparison(c);
    } catch (error) { toast(error.message); }
  }
  function showComparison(c) {
    const labels={emails:"Email giả lập",social_accounts:"Hồ sơ thử nghiệm",views:"Lượt xem",likes:"Lượt thích",follows:"Lượt theo dõi",watch_seconds:"Thời gian xem (giây)"};
    $("#compareResult").innerHTML = `<div class="compare-names"><b>${escapeHtml(c.left.name)} <span class="synthetic-tag">seed ${c.left.seed}</span> → ${escapeHtml(c.right.name)} <span class="synthetic-tag">seed ${c.right.seed}</span></b><span class="same-result">${c.same_result ? "✓ Cùng dấu vân tay kết quả" : "Kết quả khác nhau"}</span></div><div class="compare-metrics">${Object.entries(labels).map(([key,label]) => `<div class="compare-metric"><small>${label}</small><div><b>${fmt(c.left.metrics[key])} → ${fmt(c.right.metrics[key])}</b><span class="${c.delta[key] > 0 ? "positive" : c.delta[key] < 0 ? "negative" : ""}">${c.delta[key] > 0 ? "+" : ""}${fmt(c.delta[key])}</span></div></div>`).join("")}</div><div class="compare-meta">Cùng cấu hình: <b>${c.same_config ? "Có" : "Không"}</b><br>Dấu vân tay A: <code>${escapeHtml(c.left_fingerprint)}</code><br>Dấu vân tay B: <code>${escapeHtml(c.right_fingerprint)}</code></div>`;
  }
  async function createRun(form) {
    const values = Object.fromEntries(new FormData(form));
    for (const k of ["seed","account_count","creator_count","videos_per_creator","views_per_account","delay_ms"]) values[k]=Number(values[k]);
    values.like_probability=Number(values.like_probability); values.follow_probability=Number(values.follow_probability); values.request_id=crypto.randomUUID();
    try {
      const run = await api("/api/runs", {method:"POST",body:JSON.stringify(values)});
      state.runId=run.id; state.accountOffset=0; $("#createDialog").close(); setView("overview"); toast("Đã bắt đầu thử nghiệm mô phỏng."); await refresh();
    } catch (error) { toast(error.message); }
  }
  async function action(name, body={}) {
    if (!state.runId) return;
    try { await api(`/api/runs/${encodeURIComponent(state.runId)}/${name}`, {method:"POST",body:JSON.stringify(body)}); await refresh(); toast(({pause:"Đã lưu checkpoint.",resume:"Đã tiếp tục thử nghiệm.",cancel:"Đã dừng và lưu dữ liệu từng phần.",repeat:"Đã tạo lần chạy mới với cùng seed."})[name] || "Đã cập nhật."); }
    catch (error) { toast(error.message); }
  }

  $$(".nav-item").forEach((node) => node.addEventListener("click", () => setView(node.dataset.view)));
  $("#newRun").addEventListener("click", () => $("#createDialog").showModal());
  $("#newRunTop").addEventListener("click", () => $("#createDialog").showModal());
  $$('[data-open-create]').forEach((node) => node.addEventListener("click", () => $("#createDialog").showModal()));
  $$('[data-close-dialog]').forEach((node) => node.addEventListener("click", () => $("#createDialog").close()));
  $$('[data-close-mail]').forEach((node) => node.addEventListener("click", () => $("#mailDialog").close()));
  $("#createForm").addEventListener("submit", (event) => { event.preventDefault(); createRun(event.currentTarget); });
  $("#pauseBtn").addEventListener("click", () => action("pause"));
  $("#resumeBtn").addEventListener("click", () => action("resume"));
  $("#cancelBtn").addEventListener("click", () => action("cancel"));
  $("#repeatBtn").addEventListener("click", () => action("repeat", {request_id:crypto.randomUUID()}));
  $("#compareBtn").addEventListener("click", compareRuns);
  $("#accountSearch").addEventListener("input", () => { state.accountOffset=0; clearTimeout(state.searchTimer); state.searchTimer=setTimeout(loadAccounts,180); });
  $("#accountStatus").addEventListener("change", () => { state.accountOffset=0; loadAccounts(); });
  $("#prevPage").addEventListener("click", () => { state.accountOffset=Math.max(0,state.accountOffset-25); loadAccounts(); });
  $("#nextPage").addEventListener("click", () => { if(state.accountOffset+25<state.accountTotal){state.accountOffset+=25;loadAccounts();} });
  window.addEventListener("hashchange", () => { const target=location.hash.slice(1); if(["overview","accounts","compare"].includes(target)) setView(target); });
  const initial=location.hash.slice(1); if(["overview","accounts","compare"].includes(initial)) setView(initial);
  refresh(); state.timer=setInterval(refresh,850);
})();
