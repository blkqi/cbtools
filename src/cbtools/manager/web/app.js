const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
}[char]));
const icon = (name, small = false) => `<svg class="icon${small ? " sm" : ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const shortId = id => id.slice(0, 6).toUpperCase();
const stages = ["repacking", "tagging", "renaming"];
const activeStates = ["checking_files", ...stages];
const labels = {queued:"Queued", checking_files:"Checking files", repacking:"Repacking", tagging:"Tagging", renaming:"Renaming", succeeded:"Completed", failed:"Failed", removed:"Removed"};
const colors = {queued:"gray", checking_files:"blue", repacking:"blue", tagging:"blue", renaming:"blue", succeeded:"green", failed:"red", removed:"gray"};
let model = null, selected = null, scope = null, view = "overview";
let settings = null, settingsGroup = null, online = false, writing = false, settingsSaving = false;
let source = null, retryTimer, toastTimer, generation = 0, failures = 0;
let pollingUntil = 0, polling = false, lastReceive = 0, lastUpdate = null;
let stopped = false, confirmAction = null, inspectorKey = "", inspectorJob = null, activityKey = "";
let offlineReason = "Connecting to manager...";
let authStatus = null, authError = "", authChecked = 0, oauthNavigating = false;

function cursorParts(cursor) {
  if (typeof cursor !== "string") throw new Error("Missing manager event cursor.");
  const split = cursor.lastIndexOf(":");
  const boot = cursor.slice(0, split), sequence = Number(cursor.slice(split + 1));
  if (split < 1 || !/^\d+$/.test(cursor.slice(split + 1)) || !Number.isSafeInteger(sequence)) throw new Error("Invalid manager event cursor.");
  return {boot, sequence};
}

async function api(path, {method = "GET", data, revision} = {}) {
  const headers = {Accept:"application/json"};
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = model?.csrf_token ?? "";
  }
  if (revision) headers["If-Match"] = `"${revision}"`;
  const response = await fetch(`/api/v1/${path}`, {
    method, headers, credentials:"same-origin", cache:"no-store",
    body:data === undefined ? undefined : JSON.stringify(data),
    signal:AbortSignal.timeout(15000)
  });
  if (response.status === 204) return null;
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.error?.message ?? `Manager request failed (${response.status}).`);
    error.fields = value.error?.fields;
    error.status = response.status;
    throw error;
  }
  return value;
}

function showError(error) {
  console.error(error);
  $("#request-error-text").textContent = error.message || String(error);
  $("#request-error").hidden = false;
  if (error.fields) {
    $("#settings-errors").textContent = Object.entries(error.fields).map(([key, message]) =>
      `${settings?.fields.find(field => field.key === key)?.label ?? key}: ${message}`
    ).join(" ");
    $("#settings-errors").hidden = false;
    const first = visibleFields().find(field => Object.hasOwn(error.fields, field.key));
    if (first) selectSettingsGroup(first.group);
  }
}

function notify(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}

function getJob(id) {
  return model?.jobs[id] ?? model?.events.find(event => event.job_id === id)?.data.job;
}
const jobLabel = job => activeStates.includes(job.state) && job.wait_reason === "rate_limited" ? "Waiting for AniList"
  : job.state === "queued" && job.wait_reason === "files_changing" ? "Waiting for files" : labels[job.state];
const activeAlerts = () => Object.values(model?.jobs ?? {}).filter(job =>
  job.requires_intervention && !job.acknowledged && !job.resolved_by
);
const elapsed = job => Math.floor(job.elapsed + (
  job.started_at && !job.finished_at ? (Date.now() - job._received) / 1000 : 0
));
const eligible = job => Math.max(0, Math.ceil(job.eligible_in - (Date.now() - job._received) / 1000));
const timeLabel = value => new Date(value).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"});

function applySnapshot(next) {
  if (next.schema_version !== 1 || !next.jobs || !Array.isArray(next.events)
      || !Array.isArray(next.queue) || !Number.isInteger(next.limits?.events)
      || next.limits.events < 1 || next.limits.events > 1000) {
    throw new Error("The manager returned an unsupported snapshot.");
  }
  const incoming = cursorParts(next.cursor);
  if (incoming.boot !== next.boot_id) throw new Error("The manager snapshot has an inconsistent session.");
  if (model && model.boot_id === next.boot_id && incoming.sequence < cursorParts(model.cursor).sequence) return;
  const changedSession = model && model.boot_id !== next.boot_id;
  const selectRecent = !model || changedSession;
  const received = Date.now();
  for (const job of Object.values(next.jobs)) job._received = received;
  next.events = next.events.slice().reverse();
  model = next;
  if (changedSession) {
    scope = null;
    inspectorKey = "";
    notify("Manager restarted. Activity from the previous session is no longer available.");
  }
  const previous = getJob(selected);
  if (!previous || previous.state === "removed") {
    selected = model.active ?? model.queue[0]
      ?? (selectRecent ? model.events.find(event => {
        const job = getJob(event.job_id);
        return job && job.state !== "removed";
      })?.job_id : null) ?? null;
  }
  lastUpdate = model.events[0]?.occurred_at ?? new Date().toISOString();
}

async function refreshSnapshot() {
  const next = await api("snapshot");
  const changedSession = model && model.boot_id !== next.boot_id;
  applySnapshot(next);
  render();
  if (changedSession) scheduleReconnect("Manager restarted. Reconnecting...", 0);
}

function applyEvent(event) {
  if (event.schema_version !== 1) throw new Error("Unsupported manager event version.");
  const incoming = cursorParts(event.event_id), current = cursorParts(model.cursor);
  if (incoming.boot !== current.boot) throw new Error("Manager session changed.");
  if (incoming.sequence <= current.sequence) return;
  if (incoming.sequence !== current.sequence + 1) throw new Error("Activity sequence gap. Refreshing manager state.");
  model.cursor = event.event_id;
  const job = event.data.job;
  if (job) {
    job._received = Date.now();
    model.jobs[job.id] = job;
    if (job.state === "queued") {
      if (!model.queue.includes(job.id)) model.queue.push(job.id);
    } else {
      model.queue = model.queue.filter(id => id !== job.id);
    }
    if (activeStates.includes(job.state)) model.active = job.id;
    else if (model.active === job.id) model.active = null;
    if (selected === job.id && job.state === "removed") selected = model.active;
    if (!selected && job.state !== "removed") selected = job.id;
  }
  for (const id of event.data.evicted_job_ids ?? []) delete model.jobs[id];
  if (event.data.completed !== undefined) model.completed = event.data.completed;
  if (event.data.completed_volumes !== undefined) model.completed_volumes = event.data.completed_volumes;
  if (event.data.health) model.health = event.data.health;
  if (event.data.rescan) model.rescan = event.data.rescan;
  if (event.type === "queue.delays_skipped") {
    for (const id of model.queue) model.jobs[id].eligible_in = 0;
  }
  model.events.unshift(event);
  if (model.events.length > model.limits.events) model.events.length = model.limits.events;
  model.oldest_cursor = model.events.at(-1)?.event_id ?? null;
  lastUpdate = event.occurred_at;
  if (event.type === "settings.saved" && !settingsSaving && settings?.revision !== event.data.revision) {
    loadSettings().catch(showError);
  }
  render();
}

function scheduleReconnect(reason, delay) {
  if (stopped) return;
  generation++;
  source?.close();
  source = null;
  online = false;
  offlineReason = reason;
  clearTimeout(retryTimer);
  render();
  retryTimer = setTimeout(connect, delay);
}

async function connect() {
  if (stopped) return;
  const attempt = ++generation;
  source?.close();
  source = null;
  try {
    const next = await api("snapshot");
    if (attempt !== generation || stopped) return;
    applySnapshot(next);
    loadSettings().catch(showError);
    polling = Date.now() < pollingUntil;
    if (polling) {
      online = true;
      render();
      retryTimer = setTimeout(connect, 5000);
      return;
    }
    online = false;
    render();
    const stream = new EventSource(`/api/v1/events?after=${encodeURIComponent(model.cursor)}`);
    source = stream;
    stream.onopen = () => {
      if (attempt !== generation) return;
      online = true;
      failures = 0;
      lastReceive = Date.now();
      render();
    };
    stream.addEventListener("heartbeat", () => { lastReceive = Date.now(); });
    stream.addEventListener("activity", message => {
      if (attempt !== generation) return;
      try {
        lastReceive = Date.now();
        applyEvent(JSON.parse(message.data));
      } catch (error) {
        console.error(error);
        scheduleReconnect(error.message, 0);
      }
    });
    stream.addEventListener("reset", () => {
      if (attempt === generation) scheduleReconnect("Refreshing manager session/activity...", 0);
    });
    stream.onerror = () => {
      if (attempt !== generation) return;
      failures++;
      if (failures >= 3) pollingUntil = Date.now() + 60000;
      scheduleReconnect("Connection lost. Displayed data is stale; changes are disabled until reconnection.",
        Math.min(15000, 1000 * 2 ** Math.min(failures - 1, 4)));
    };
  } catch (error) {
    if (attempt !== generation || stopped) return;
    console.error(error);
    failures++;
    scheduleReconnect(`Manager unavailable: ${error.message}. Retrying...`,
      Math.min(15000, 1000 * 2 ** Math.min(failures - 1, 4)));
  }
}

function preserveButton(container, update) {
  const focused = container.contains(document.activeElement) ? document.activeElement : null;
  const action = focused?.dataset.action, job = focused?.dataset.job;
  update();
  if (focused && action) {
    const replacement = [...container.querySelectorAll("button")].find(button =>
      button.dataset.action === action && button.dataset.job === job
    );
    (replacement ?? $("#alerts-close")).focus({preventScroll:true});
  }
}

function renderAlerts() {
  const alerts = activeAlerts(), count = alerts.length;
  const label = count ? `${count} active alert${count === 1 ? "" : "s"}` : "No active alerts";
  $("#alert-count").textContent = count;
  $("#alert-count").hidden = count === 0;
  $("#alerts-toggle").setAttribute("aria-label", `Alerts: ${label}`);
  $("#alerts-toggle").title = label;
  if ($("#alerts-status").textContent !== label) $("#alerts-status").textContent = label;
  preserveButton($("#alert-list"), () => {
    $("#alert-list").innerHTML = alerts.map(job => `<div class="alert-item">
      <div class="row"><strong class="grow">${escapeHtml(job.title)}</strong><span class="mono muted">#${shortId(job.id)}</span></div>
      <p>${escapeHtml(job.error.message)}</p><div class="alert-actions">
      <button class="text-button" data-action="job" data-job="${job.id}">View details</button>
      <button class="text-button" data-action="dismiss" data-job="${job.id}" data-mutation>Dismiss</button></div></div>`
    ).join("") || '<div class="empty">No active alerts.</div>';
  });
}

const volumeTotal = jobs => jobs.some(job => job?.archives == null)
  ? null : jobs.reduce((sum, job) => sum + job.archives, 0);

function renderVolumeCount(name, count, error) {
  const element = $(`#stat-${name}-volumes`);
  element.textContent = !model ? "\u2014" : count == null
    ? (error ? "Unavailable" : "Counting...") : count.toLocaleString();
  element.classList.toggle("pending", Boolean(model) && count == null);
  element.title = model ? (error ?? "") : "";
  $(`#stat-${name}-volume-unit`).textContent = count === 1 ? "volume" : "volumes";
}

function renderQueue() {
  const ids = model ? [model.active, ...model.queue].filter(Boolean) : [];
  const processing = model?.active ? [getJob(model.active)] : [];
  const waiting = (model?.queue ?? []).map(getJob);
  $("#stat-active").textContent = model ? (model.active ? "1" : "0") : "\u2014";
  $("#stat-waiting").textContent = model ? model.queue.length.toLocaleString() : "\u2014";
  $("#stat-completed").textContent = model ? model.completed.toLocaleString() : "\u2014";
  renderVolumeCount("active", volumeTotal(processing), processing[0]?.archive_count_error);
  renderVolumeCount("waiting", volumeTotal(waiting), waiting.find(job => job?.archive_count_error)?.archive_count_error
    ?? (model?.health?.volume_counts === "failed" ? "Volume counting failed. Review the diagnostic logs." : null));
  renderVolumeCount("completed", model?.completed_volumes,
    model?.completed_volumes == null ? "Completed volume count unavailable for this session." : null);
  $("#queue-count").textContent = ids.length;
  const focused = $("#queue-list").contains(document.activeElement) ? document.activeElement.dataset.job : null;
  const scrollTop = $("#queue-list").scrollTop;
  $("#queue-list").innerHTML = ids.map(id => {
    const job = getJob(id);
    const label = jobLabel(job);
    return `<button class="job-row${selected === id ? " selected" : ""}" data-action="job" data-job="${id}" aria-pressed="${selected === id}" aria-label="Select ${escapeHtml(job.title)}, job ${shortId(id)}, ${label}">
      <span class="grow"><span class="job-title">${escapeHtml(job.title)}</span><span class="job-meta">
      <span class="mono">#${shortId(id)}</span>${job.archives === null ? "" : `<span>&middot;</span><span>${job.archives} archive${job.archives === 1 ? "" : "s"}</span>`}<span>&middot;</span><span>${job.source === "rescan" ? "Rescan" : "Watcher"}</span></span></span>
      <span class="job-state"><span class="badge ${colors[job.state]}">${activeStates.includes(job.state) ? '<span class="dot"></span>' : ""}${label}</span>
      <span class="small" data-eligibility="${id}"></span></span></button>`;
  }).join("") || `<div class="empty">${model ? "No waiting or active jobs." : "Connecting to manager..."}</div>`;
  $("#queue-list").scrollTop = scrollTop;
  if (focused) ($$("#queue-list button").find(button => button.dataset.job === focused) ?? $("#queue-list")).focus({preventScroll:true});
}

function renderInspector() {
  const job = getJob(selected);
  const key = JSON.stringify([selected, job], (name, value) => name === "_received" ? undefined : value);
  if (key === inspectorKey) return;
  inspectorKey = key;
  const sameJob = inspectorJob === selected;
  const expanded = sameJob && $("#inspector details")?.open;
  const focusAction = $("#inspector").contains(document.activeElement) ? document.activeElement.dataset.action : null;
  inspectorJob = selected;
  if (!job) {
    $("#inspector").innerHTML = '<div class="empty">Select a job to see its details.</div>';
    return;
  }
  const stageIndex = stages.indexOf(job.stage);
  const tracker = stages.map((stage, index) => {
    const result = job.results[stage];
    const state = result?.failed || (job.state === "failed" && job.stage === stage) ? "failed"
      : result?.outcome === "unchanged" ? "skipped" : result ? "done" : job.state === stage ? "active" : "";
    const name = ["Repack", "Tag", "Rename"][index];
    const description = {failed:"Failed", skipped:"Skipped (no changes needed)", done:job.dry_run ? "Changes previewed" : "Completed",
      active:"In progress", "":job.state === "failed" ? "Not run" : "Not started"}[state];
    return `<div class="stage ${state}" title="${name}: ${description}">${icon(state === "failed" ? "alert" : state === "skipped" ? "skip" : state === "done" ? "check" : state === "active" ? "activity" : "clock", true)}<div class="stage-name">${name}</div><span class="sr-only">${description}</span></div>`;
  }).join("");
  const outcome = job.resolved_by ? `Issue resolved by #${shortId(job.resolved_by)}. This attempt remains failed.`
    : job.acknowledged ? "Alert dismissed. This failure is still unresolved." : "";
  const callout = job.error ? `<div class="callout error"><strong>${escapeHtml(job.error.message)}</strong><br>${escapeHtml(job.error.remedy)}${outcome ? `<br>${escapeHtml(outcome)}` : ""}</div>` : "";
  const folder = job.output_paths.length && job.state === "succeeded"
    ? job.output_paths[0].replace(/[\\/][^\\/]+$/, "") : job.path;
  $("#inspector").innerHTML = `<div class="card-header"><h2>Job details</h2><span class="mono muted">#${shortId(job.id)}</span></div>
    <div class="inspector-body"><div class="series-heading"><div class="series-name"><span class="series-title">${escapeHtml(job.title)}</span>
    ${Number.isInteger(job.series_id) && job.series_id > 0 ? `<a class="anilist" href="https://anilist.co/manga/${job.series_id}" target="_blank" rel="noopener noreferrer" aria-label="View ${escapeHtml(job.title)} on AniList" title="View on AniList"><svg class="anilist-mark" aria-hidden="true"><use href="#i-anilist"/></svg></a>` : ""}</div></div>
    <div class="pipeline">${tracker}</div>${!stages.includes(job.state) ? `<div class="stage-note">${jobLabel(job)}${job.dry_run ? " (test mode)" : ""}</div>` : job.wait_reason ? '<div class="stage-note">Waiting for AniList. Processing will resume automatically.</div>' : ""}
    <dl class="info-grid"><dt>Archives</dt><dd>${job.archives ?? "&mdash;"}</dd><dt>Elapsed</dt><dd id="job-elapsed"></dd></dl>
    <div class="section-label">Folder</div><div class="path">${escapeHtml(folder)}</div>${callout}
    ${job.error ? `<details><summary>Diagnostic context</summary><pre>${escapeHtml(job.error.code + "\nStage: " + (stages[stageIndex] ?? "file check") + "\n" + job.error.diagnostic)}</pre></details>` : ""}</div>
    <div class="inspector-actions"><button class="text-button" data-action="job-activity">View job activity</button>
    ${job.requires_intervention && !job.acknowledged ? `<button class="text-button" data-action="dismiss" data-job="${job.id}" data-mutation>Dismiss alert</button>` : ""}</div>`;
  if (expanded) $("#inspector details").open = true;
  if (focusAction) ($$("#inspector button").find(button => button.dataset.action === focusAction) ?? $("#inspector")).focus({preventScroll:true});
}

function renderActivity() {
  const query = $("#search").value.trim().toLowerCase();
  const key = JSON.stringify([scope, query]);
  const scrollTop = key === activityKey ? $("#activity-feed").scrollTop : 0;
  activityKey = key;
  const seen = new Set();
  const events = (model?.events ?? []).filter(event => {
    if (event.type === "job.counted") return false;
    if (scope && event.job_id !== scope) return false;
    const job = getJob(event.job_id);
    if (query && ![event.message, job?.title, event.job_id, job?.path, job?.error?.message, job?.error?.remedy]
      .filter(Boolean).join(" ").toLowerCase().includes(query)) return false;
    if (!scope && event.job_id) {
      if (seen.has(event.job_id)) return false;
      seen.add(event.job_id);
    }
    return true;
  });
  $("#feed-filter").textContent = scope ? `Job #${shortId(scope)}` : "All jobs";
  $("#all-jobs").hidden = !scope;
  $("#retention-note").hidden = !model?.oldest_cursor || cursorParts(model.oldest_cursor).sequence <= 1;
  $("#activity-feed").innerHTML = events.slice(0, 100).map(event => {
    const currentJob = getJob(event.job_id);
    const job = (scope ? event.data?.job : null) ?? currentJob;
    const message = !scope && job?.state === "failed" ? job.error?.message ?? event.message : event.message;
    const level = !scope && job?.state === "failed" ? "error" : event.level;
    const skipped = (scope || job?.state !== "failed") && event.type === "stage.finished"
      && event.data.job?.results[event.data.job.stage]?.outcome === "unchanged";
    const error = job?.state === "failed" ? (job.resolved_by ? `Issue resolved by #${shortId(job.resolved_by)}`
      : job.acknowledged ? "Alert dismissed; failure unresolved" : "Required attention") : "";
    return `<div class="feed-item"><span class="event-icon ${level}">${icon(skipped ? "skip" : level === "error" ? "alert" : level === "success" ? "check" : "activity", true)}</span>
      <div class="grow"><div class="feed-title">${escapeHtml(message)}${job ? `<button class="job-link" data-action="job" data-job="${job.id}" aria-label="View ${escapeHtml(job.title)}, job ${shortId(job.id)}">#${shortId(job.id)}</button><span class="badge ${colors[job.state]}">${jobLabel(job)}</span>` : '<span class="badge gray">Manager</span>'}</div>
      ${job ? `<div class="feed-description">${escapeHtml(job.title)}</div>` : ""}${error && error !== message ? `<div class="feed-actions"><span class="muted">${escapeHtml(error)}</span>${currentJob?.requires_intervention && !currentJob.acknowledged ? `<button class="text-button" data-action="dismiss" data-job="${job.id}" data-mutation>Dismiss alert</button>` : ""}</div>` : ""}</div>
      <time class="feed-time" datetime="${escapeHtml(event.occurred_at)}">${timeLabel(event.occurred_at)}</time></div>`;
  }).join("") || '<div class="empty">No activity matches this view.</div>';
  $("#activity-feed").scrollTop = scrollTop;
}

function render() {
  $("#offline-banner").hidden = online;
  $("#offline-banner").textContent = offlineReason;
  const unhealthy = Object.entries(model?.health ?? {}).filter(([, state]) => state !== "running");
  $("#health-banner").hidden = !unhealthy.length;
  $("#health-banner").textContent = unhealthy.map(([service, state]) =>
    `${service === "volume_counts" ? "Volume counting" : service}: ${state.replaceAll("_", " ")}.`
  ).join(" ") + " Review the diagnostic logs; a manager restart may be required.";
  renderAlerts();
  renderQueue();
  renderInspector();
  renderActivity();
  const scan = model?.rescan;
  $("#scan-status").hidden = !scan || scan.state === "idle";
  $("#scan-status").textContent = scan?.state === "running" ? "Adding library jobs to the queue..."
    : scan?.state === "failed" ? `Rescan failed: ${scan.error}` : `Rescan queued ${scan?.queued ?? 0} job${scan?.queued === 1 ? "" : "s"}.`;
  $$("[data-mutation]").forEach(button => { button.disabled = !online || writing; });
  $("#dismiss-all").disabled = !online || writing || !activeAlerts().length;
  $("#rescan").disabled = !online || writing || scan?.state === "running";
  $("#clear").disabled = !online || writing || !model?.queue.length;
  $("#flush").disabled = !online || writing || !model?.queue.some(id => eligible(model.jobs[id]) > 0);
  $$("#settings-form input, #settings-form textarea").forEach(input => { input.disabled = settingsSaving; });
  renderAuth();
  updateDirty();
  updateTimes();
}

function updateTimes() {
  $$("[data-eligibility]").forEach(element => {
    const job = getJob(element.dataset.eligibility);
    const remaining = eligible(job);
    element.textContent = job.state === "queued" ? (remaining
      ? `${job.wait_reason === "files_changing" ? "Checking again" : "Checking"} in ${remaining}s`
      : model.active ? "Waiting for current job" : job.wait_reason === "files_changing" ? "Checking again soon" : "Checking soon")
      : job.stage ? `Stage ${stages.indexOf(job.stage) + 1} of 3` : "";
  });
  const job = getJob(selected);
  if (job && $("#job-elapsed")) $("#job-elapsed").textContent = job.started_at ? `${elapsed(job)}s` : "Not started";
  $("#snapshot-label").textContent = lastUpdate ? `Updated ${timeLabel(lastUpdate)}${polling ? " (polling)" : ""}` : "";
}

function sizePanels() {
  const panel = $(".view:not([hidden]) [data-size-panel]"), footer = $(".page-footer");
  if (!panel) return;
  const bottom = footer.offsetHeight + parseFloat(getComputedStyle(footer).marginTop)
    + parseFloat(getComputedStyle($("main")).paddingBottom);
  const available = innerHeight - (panel.getBoundingClientRect().top + scrollY) - bottom;
  panel.style.setProperty("--panel-max-height", `${Math.max(200, Math.floor(available))}px`);
}

const visibleFields = () => {
  const fields = settings?.fields.filter(field => field.key !== "manager.api_port")
    .map(field => field.group === "Library" ? {...field, group:"Processing"} : field) ?? [];
  const library = fields.find(field => field.key === "manager.library_path");
  return library ? [library, ...fields.filter(field => field !== library)] : fields;
};
function formValues() {
  if (!settings) return {};
  return Object.fromEntries(visibleFields().map(field => {
    if (field.options) {
      const selected = $$(`#settings-form [name="${field.key}"]:checked`).map(input => input.value);
      const before = settings.configured[field.key];
      return [field.key, [...before.filter(name => selected.includes(name)), ...selected.filter(name => !before.includes(name))]];
    }
    const input = $("#settings-form").elements.namedItem(field.key);
    const value = field.type === "boolean" ? input.checked : field.type === "integer"
      ? input.value.trim() ? Number(input.value) : null : field.type === "list"
        ? input.value.split(",").map(item => item.trim()).filter(Boolean) : input.value;
    return [field.key, value];
  }));
}
function changes() {
  const values = formValues();
  return visibleFields().filter(field => JSON.stringify(values[field.key]) !== JSON.stringify(settings.configured[field.key]))
    .map(field => ({...field, old:settings.configured[field.key], value:values[field.key]}));
}
function updateDirty() {
  const count = changes().length;
  $("#dirty-count").textContent = count ? `${count} unsaved change${count === 1 ? "" : "s"}` : "No unsaved changes";
  $("#discard").disabled = !count || settingsSaving;
  $("#review").disabled = !count || !online || writing || settings?.external_change || Boolean(settings?.load_error);
}
const displayValue = value => typeof value === "boolean" ? value ? "On" : "Off" : Array.isArray(value) ? value.join(", ") || "(none)" : String(value);

function populateSettings() {
  const fields = visibleFields(), groups = [...new Set(fields.map(field => field.group)), "Other"];
  const selectedGroup = groups.includes(settingsGroup) ? settingsGroup : groups[0];
  $("#settings-tabs").innerHTML = groups.map((group, index) =>
    `<button class="settings-tab" type="button" role="tab" id="settings-tab-${index}" aria-controls="settings-panel-${index}" data-action="settings-tab" data-group="${escapeHtml(group)}">${escapeHtml(group)}</button>`
  ).join("");
  $("#settings-tabs").hidden = false;
  $("#settings-fields").innerHTML = groups.map((group, index) =>
    `<section class="settings-section" role="tabpanel" id="settings-panel-${index}" aria-labelledby="settings-tab-${index}" data-group="${escapeHtml(group)}" tabindex="0">${group === "Other"
      ? `<div class="settings-read-only"><p>These settings are read-only.</p><pre>${escapeHtml(JSON.stringify(settings.read_only, null, 2))}</pre></div>`
      : fields.filter(field => field.group === group).map(field => {
      const value = settings.configured[field.key], id = field.key.replaceAll(".", "-");
      const pending = settings.restart_required.includes(field.key);
      if (field.options) {
        return `<div class="setting extension-setting"><div class="setting-copy"><span id="${id}-label">${escapeHtml(field.label)}</span><p>${escapeHtml(field.help)}</p></div>
          <fieldset class="extension-options" aria-labelledby="${id}-label">${field.options.map(option =>
            `<label><input type="checkbox" name="${field.key}" value="${escapeHtml(option.value)}"${value.includes(option.value) ? " checked" : ""}><span>${escapeHtml(option.label)}<small>${escapeHtml(option.help)}</small></span></label>`
          ).join("")}</fieldset></div>`;
      }
      const control = field.key === "logging.format"
        ? `<textarea id="${id}" name="${field.key}" rows="2" spellcheck="false"></textarea>`
        : `<input id="${id}" name="${field.key}" type="${field.type === "integer" ? "number" : "text"}" value="${escapeHtml(Array.isArray(value) ? value.join(", ") : value)}"${field.key === "rename.pattern" ? ' class="pattern"' : ""} spellcheck="false">`;
      const input = field.type === "boolean" ? `<input id="${id}" name="${field.key}" type="checkbox"${value ? " checked" : ""}>`
        : `<div>${control}
        ${pending ? `<p class="pending-value">Still effective: ${escapeHtml(displayValue(settings.effective[field.key]))}</p>` : ""}</div>`;
      return `<div class="setting${pending ? " effective-pending" : ""}"><div class="setting-copy"><label for="${id}">${escapeHtml(field.label)}</label>
        <p>${escapeHtml(field.help)}</p></div>${input}</div>`;
    }).join("")
  }</section>`).join("");
  selectSettingsGroup(selectedGroup);
  $("#logging-format").value = settings.configured["logging.format"];
  $("#restart-notice").hidden = !settings.restart_required.length;
  $("#restart-notice").textContent = `${settings.restart_required.length} saved setting(s) require a manager restart. Running values are unchanged.`;
  $("#settings-errors").hidden = true;
  renderSettingsNotice();
  updateDirty();
}
function selectSettingsGroup(group, focus = false) {
  const tabs = $$("#settings-tabs [role=tab]"), selected = tabs.find(tab => tab.dataset.group === group);
  tabs.forEach(tab => {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
  });
  if (settingsGroup !== group) $("#settings-fields").scrollTop = 0;
  settingsGroup = group;
  if (focus) {
    selected.focus({preventScroll:true});
    selected.scrollIntoView({block:"nearest", inline:"nearest"});
  }
}
function renderSettingsNotice(message) {
  const problem = message ?? settings?.load_error ?? (settings?.external_change
    ? "The configuration file changed outside the manager. Restart before saving settings here." : "");
  $("#settings-notice").hidden = !problem;
  $("#settings-notice").textContent = problem;
}
function renderAuth() {
  const status = authStatus, environment = status?.source === "environment";
  $("#anilist-status").textContent = authError || (!status ? "Loading connection..." : environment
    ? status.state === "invalid" ? "Environment token rejected. Replace or remove ANILIST_TOKEN." : "Configured through ANILIST_TOKEN."
    : ({connected:`Connected as ${status.account}.`, expired:"Connection expired. Reconnect to AniList.",
        invalid:"Connection rejected. Reconnect to AniList.", storage_error:status.error,
        not_connected:"Not connected."})[status.state]);
  $("#anilist-details").hidden = !status?.expires_at;
  $("#anilist-details").textContent = status?.expires_at ? `Token expires ${new Date(status.expires_at * 1000).toLocaleDateString()}.` : "";
  $("#anilist-setup").hidden = environment || !status?.setup_error;
  $("#anilist-setup").textContent = status?.setup_error ?? "";
  $("#anilist-connect").hidden = environment;
  $("#anilist-connect").textContent = status?.source === "oauth" ? "Reconnect to AniList" : "Connect to AniList";
  $("#anilist-connect").disabled = !online || writing || !status?.can_connect || Boolean(authError);
  $("#anilist-remove").hidden = !status?.can_remove;
  $("#anilist-remove").disabled = !online || writing || Boolean(authError);
}
async function loadAuth() {
  authChecked = Date.now();
  try {
    authStatus = await api("anilist");
    authError = "";
  } catch (error) {
    authError = `AniList status unavailable: ${error.message}`;
    throw error;
  } finally { renderAuth(); }
}
async function loadSettings(force = false) {
  const [next] = await Promise.all([api("settings"), loadAuth().catch(showError)]);
  if (!force && settings && changes().length) {
    if (next.revision !== settings.revision || next.external_change || next.load_error) {
      renderSettingsNotice("Saved settings changed. The draft is retained. Discard/reload it before editing again.");
    }
    return;
  }
  settings = next;
  populateSettings();
  render();
}

function closeAlerts() {
  $("#alerts-panel").hidePopover();
  $("#alerts-toggle").focus({preventScroll:true});
}
function setView(next) {
  if (view === "settings" && next !== "settings" && changes().length && !confirm("Leave settings with unsaved changes?")) return false;
  $("#alerts-panel").hidePopover();
  view = next;
  $$(".view").forEach(section => { section.hidden = section.id !== next; });
  $$(".nav-button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === next);
    if (button.dataset.view === next) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.title = `cbmanager / ${{overview:"Overview", activity:"Activity", settings:"Settings"}[next]}`;
  if (next === "settings" && online) loadAuth().catch(showError);
  requestAnimationFrame(sizePanels);
  return true;
}
function selectJob(id) {
  if (!getJob(id)) { showError(new Error("That job is no longer retained. Refresh Activity.")); return; }
  if (!setView("overview")) return;
  selected = id;
  render();
  $("#inspector").focus({preventScroll:true});
  if (innerWidth <= 800) $("#inspector").scrollIntoView({block:"start"});
}
function modal(title, content, label, action, danger = false) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = content;
  $("#dialog-confirm").textContent = label;
  $("#dialog-confirm").className = `button ${danger ? "danger" : "primary"}`;
  confirmAction = action;
  $("#action-dialog").showModal();
  render();
}
async function perform(action, message) {
  if (!online || writing) return;
  const alertFocus = $("#alerts-panel").contains(document.activeElement);
  writing = true;
  render();
  try {
    await action();
    await refreshSnapshot();
    $("#request-error").hidden = true;
    notify(message);
  } catch (error) {
    showError(error);
    if (error.status === 403 || error.name === "TypeError" || error.name === "TimeoutError") {
      scheduleReconnect("Refreshing the manager connection after a failed request...", 1000);
    }
  } finally {
    writing = false;
    render();
    if (alertFocus && $("#alerts-panel").matches(":popover-open") && document.activeElement === document.body) {
      ($("#alert-list button:not(:disabled)") ?? $("#alerts-close")).focus({preventScroll:true});
    }
  }
}

$("#settings-form").addEventListener("input", () => { $("#settings-errors").hidden = true; updateDirty(); });
$("#settings-form").addEventListener("submit", event => {
  event.preventDefault();
  const invalid = [...event.currentTarget.elements].find(input => input.willValidate && !input.validity.valid);
  if (invalid) {
    selectSettingsGroup(invalid.closest("[role=tabpanel]").dataset.group);
    invalid.reportValidity();
    return;
  }
  const diff = changes(), revision = settings?.revision;
  if (!diff.length || !online) return;
  modal("Review changes", diff.map(field =>
    `<div class="diff-item"><div class="row between"><strong>${escapeHtml(field.label)}</strong>${field.activation === "restart" ? '<span class="activation restart">Restart required</span>' : ""}</div>
    <div class="diff-values"><span>Current</span><span class="diff-old">${escapeHtml(displayValue(field.old))}</span><span>New</span><span class="diff-new">${escapeHtml(displayValue(field.value))}</span></div></div>`
  ).join(""), "Save settings", async () => {
    settingsSaving = true;
    try {
      await perform(async () => {
        settings = await api("settings", {method:"PATCH", revision, data:Object.fromEntries(diff.map(field => [field.key, field.value]))});
        populateSettings();
      }, "Settings saved.");
    } finally { settingsSaving = false; render(); }
  });
  $("#settings-tabs").addEventListener("keydown", event => {
    const tabs = $$("#settings-tabs [role=tab]"), index = tabs.indexOf(event.target.closest("[role=tab]"));
    if (index < 0) return;
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    selectSettingsGroup(tabs[next].dataset.group, true);
  });
});
$("#dialog-cancel").addEventListener("click", () => $("#action-dialog").close());
$("#dialog-confirm").addEventListener("click", async () => {
  if (!online || writing) return;
  const action = confirmAction;
  $("#action-dialog").close();
  if (action) await action();
});
$("#alerts-panel").addEventListener("toggle", event => $("#alerts-toggle").setAttribute("aria-expanded", String(event.newState === "open")));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && $("#alerts-panel").matches(":popover-open")) { event.preventDefault(); closeAlerts(); }
});
$$(".nav-button").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
$("#search").addEventListener("input", renderActivity);
document.addEventListener("click", async event => {
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === "settings-tab") selectSettingsGroup(button.dataset.group, true);
  else if (action === "job") selectJob(button.dataset.job);
  else if (action === "clear-error") $("#request-error").hidden = true;
  else if (action === "all-jobs") { scope = null; $("#search").value = ""; renderActivity(); }
  else if (action === "job-activity") {
    scope = selected;
    $("#search").value = "";
    if (setView("activity")) { renderActivity(); $("#search").focus(); }
  } else if (action === "discard") {
    try { await loadSettings(true); } catch (error) { showError(error); }
  } else if (action === "anilist-connect") {
    if (changes().length && !confirm("Connecting to AniList leaves this page. Discard unsaved settings?")) return;
    modal("Connect to AniList?", "<p>Connecting authorizes cbtools to access your AniList account. Authentication is optional, but may help if AniList restricts anonymous requests.</p>",
      "Continue to AniList", () => perform(async () => {
        const result = await api("anilist/connect", {method:"POST", data:{}});
        oauthNavigating = true;
        location.assign(result.url);
      }, "Opening AniList authorization..."));
  } else if (action === "anilist-remove") {
    modal("Remove AniList connection?", "<p>Remove the saved token from cbtools. This does not revoke it on AniList; revoke the authorization in AniList settings if needed. Active tagging retains its current token.</p>",
      "Remove connection", () => perform(async () => {
        await api("anilist", {method:"DELETE", data:{}});
        await loadAuth();
      }, "Saved AniList connection removed."), true);
  } else if (action === "dismiss" || action === "dismiss-all") {
    const ids = action === "dismiss-all" ? activeAlerts().map(job => job.id) : [button.dataset.job];
    await perform(async () => {
      await api(action === "dismiss-all" ? "alerts/acknowledge" : `jobs/${ids[0]}/acknowledge`,
        {method:"POST", data:action === "dismiss-all" ? {job_ids:ids} : {}});
      if (action === "dismiss-all" || activeAlerts().every(job => ids.includes(job.id))) closeAlerts();
    }, "Alerts dismissed.");
  } else if (action === "flush") {
    await perform(() => api("queue/flush", {method:"POST", data:{}}), "Wait skipped. Files will still be checked before processing.");
  } else if (action === "clear") {
    modal("Clear queue?", `<p>Remove ${model.queue.length} waiting jobs? Active processing will continue. No files are deleted.</p>
      ${model.rescan.state === "running" ? "<p><strong>A rescan is running.</strong> It may add more jobs after you clear the queue.</p>" : ""}`,
    "Clear", () => perform(() => api("queue/clear", {method:"POST", data:{}}), "Waiting jobs removed."), true);
  } else if (action === "rescan") {
    modal("Rescan library?", "<p>Recheck all series in the library. <strong>This can take a long time.</strong></p>",
      "Start rescan", () => perform(() => api("queue/rescan", {method:"POST", data:{}}), "Library rescan started."));
  }
});
setInterval(() => {
  updateTimes();
  if (online && source && Date.now() - lastReceive > 45000) scheduleReconnect("Activity stream became stale. Reconnecting...", 0);
  if (online && view === "settings" && !writing && Date.now() - authChecked > 30000) loadAuth().catch(showError);
}, 1000);
const panelSizer = new ResizeObserver(() => requestAnimationFrame(sizePanels));
$$(".topbar, .stats, main > .banner, .page-footer").forEach(element => panelSizer.observe(element));
window.addEventListener("resize", sizePanels);
window.addEventListener("pagehide", () => { stopped = true; clearTimeout(retryTimer); source?.close(); panelSizer.disconnect(); });
window.addEventListener("beforeunload", event => {
  if (!oauthNavigating && changes().length) { event.preventDefault(); event.returnValue = ""; }
});
const oauthResult = new URL(location.href);
if (oauthResult.searchParams.has("anilist_connected") || oauthResult.searchParams.has("anilist_error")) {
  setView("settings");
  if (oauthResult.searchParams.has("anilist_error")) showError(new Error(oauthResult.searchParams.get("anilist_error")));
  else notify("AniList connected.");
  oauthResult.searchParams.delete("anilist_connected");
  oauthResult.searchParams.delete("anilist_error");
  history.replaceState(null, "", oauthResult.pathname + oauthResult.search + oauthResult.hash);
}
render();
connect();
