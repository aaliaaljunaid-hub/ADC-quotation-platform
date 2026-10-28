// ADC CAD Drawing System - single-page UI (no build step).
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c);
  return el;
};

const state = { project: null, model: null, issues: [], tab: "walls", show: { furniture: true, fixtures: false, labels: true } };

// ---------------------------------------------------------------- API

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body && body.detail;
    const err = new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${res.status})`);
    err.detail = detail;
    throw err;
  }
  return body;
}
const json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function setStatus(id, text, kind = "") {
  const el = $(id);
  el.textContent = text;
  el.className = "status " + kind;
}

// ---------------------------------------------------------------- projects

async function loadProjects() {
  const list = await api("/api/projects");
  const ul = $("#project-list");
  ul.replaceChildren(...list.map(p => h("li", {
    class: state.project?.id === p.id ? "active" : "",
    onclick: () => openProject(p.id),
  }, p.name, h("small", {}, new Date(p.updated_at).toLocaleString()))));
}

async function openProject(id) {
  state.project = await api(`/api/projects/${id}`);
  state.model = state.project.has_model ? await api(`/api/projects/${id}/model`) : null;
  state.issues = state.project.issues || [];
  $("#empty").hidden = true;
  $("#project").hidden = false;
  $("#project-title").textContent = state.project.name;
  renderUploads();
  renderReview();
  renderOutputs();
  loadProjects();
}

$("#new-project").addEventListener("submit", async e => {
  e.preventDefault();
  const name = $("#new-project-name").value;
  const p = await api("/api/projects", json("POST", { name }));
  $("#new-project-name").value = "";
  openProject(p.id);
});

$("#delete-project").addEventListener("click", async () => {
  if (!confirm(`Delete project "${state.project.name}" and all its files?`)) return;
  await api(`/api/projects/${state.project.id}`, { method: "DELETE" });
  state.project = null;
  $("#project").hidden = true;
  $("#empty").hidden = false;
  loadProjects();
});

// ---------------------------------------------------------------- uploads

for (const drop of $$(".drop")) {
  const input = $("input", drop);
  const send = async files => {
    if (!files.length || !state.project) return;
    const fd = new FormData();
    fd.append("role", drop.dataset.role);
    for (const f of files) fd.append("files", f);
    try {
      state.project = await api(`/api/projects/${state.project.id}/uploads`, { method: "POST", body: fd });
      renderUploads();
    } catch (err) { alert(err.message); }
  };
  input.addEventListener("change", () => { send([...input.files]); input.value = ""; });
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); send([...e.dataTransfer.files]); });
}

function renderUploads() {
  const pid = state.project.id;
  $("#uploads").replaceChildren(...state.project.uploads.map(u => {
    const url = `/api/projects/${pid}/uploads/${encodeURIComponent(u.file)}`;
    const isPdf = u.file.toLowerCase().endsWith(".pdf");
    return h("div", { class: "thumb" },
      isPdf ? h("div", { class: "pdf" }, "PDF") : h("img", { src: url, alt: u.original, onclick: () => lightbox(url) }),
      h("span", { class: "role" }, u.role === "plan" ? "Plan" : "Render"),
      h("span", { class: "x", title: "Remove", onclick: async () => {
        state.project = await api(url, { method: "DELETE" });
        renderUploads();
      } }, "×"),
      h("div", { class: "meta", title: u.original }, u.original));
  }));
}

// ---------------------------------------------------------------- extraction

$("#extract").addEventListener("click", async () => {
  const btn = $("#extract");
  btn.disabled = true;
  setStatus("#extract-status", "Reading the images… this usually takes 1-4 minutes.");
  try {
    const r = await api(`/api/projects/${state.project.id}/extract`, json("POST", { brief: $("#brief").value }));
    state.model = r.model;
    state.issues = r.issues;
    setStatus("#extract-status", "Draft model ready - review it below.", "ok");
    renderReview();
    $("#review-card").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    setStatus("#extract-status", err.message, "error");
  } finally {
    btn.disabled = false;
  }
});

$("#sample").addEventListener("click", async () => {
  const r = await api(`/api/projects/${state.project.id}/sample`, { method: "POST" });
  state.model = r.model;
  state.issues = r.issues;
  setStatus("#extract-status", "Sample model loaded.", "ok");
  renderReview();
});

// ---------------------------------------------------------------- review tables

const ENUMS = {
  wallKind: ["exterior", "interior", "partition"],
  openingKind: ["door", "double_door", "sliding_door", "window", "opening"],
  hinge: ["start", "end"],
  swing: ["left", "right"],
  furniture: ["sofa", "armchair", "coffee_table", "dining_table", "chair", "bed", "bedside_table", "wardrobe", "desk",
    "office_chair", "tv_unit", "sideboard", "bookshelf", "rug", "kitchen_counter", "kitchen_island", "fridge", "cooker",
    "sink", "wc", "basin", "shower", "bathtub", "reception_desk", "meeting_table", "plant", "other"],
  fixture: ["downlight", "pendant", "linear_light", "spotlight", "wall_light", "ac_supply", "ac_return",
    "smoke_detector", "speaker", "exhaust_fan", "access_panel"],
  finish: ["floor", "ceiling", "wall", "joinery"],
};

// column: [path, label, type]   type: text | num | enum name | poly | wide
const TABLES = {
  walls: { label: "Walls", cols: [["id", "ID"], ["start.x", "Start X", "num"], ["start.y", "Start Y", "num"], ["end.x", "End X", "num"],
    ["end.y", "End Y", "num"], ["thickness", "Thick.", "num"], ["kind", "Type", "wallKind"], ["height", "Height", "num"]],
    blank: () => ({ id: nextId("W", "walls"), start: { x: 0, y: 0 }, end: { x: 1000, y: 0 }, thickness: 150, kind: "interior", height: 3000 }) },
  openings: { label: "Doors & windows", cols: [["tag", "Tag"], ["id", "ID"], ["wall_id", "Wall"], ["kind", "Type", "openingKind"], ["offset", "Offset", "num"],
    ["width", "Width", "num"], ["height", "Height", "num"], ["sill_height", "Sill", "num"], ["hinge", "Hinge", "hinge"], ["swing", "Swing", "swing"]],
    blank: () => ({ id: nextId("o", "openings"), tag: "D", wall_id: state.model.walls[0]?.id || "", kind: "door", offset: 0, width: 900, height: 2100, sill_height: 0, hinge: "start", swing: "left" }) },
  rooms: { label: "Rooms", cols: [["id", "ID"], ["name", "Name"], ["polygon", "Boundary (x,y; x,y; …)", "poly"], ["floor_finish", "Floor"],
    ["ceiling_finish", "Ceiling"], ["ceiling_height", "Ceiling ht.", "num"]],
    blank: () => ({ id: nextId("r", "rooms"), name: "New room", polygon: [], floor_finish: "", ceiling_finish: "", ceiling_height: 2700 }) },
  furniture: { label: "Furniture (FF&E)", cols: [["tag", "Tag"], ["name", "Name"], ["category", "Category", "furniture"], ["center.x", "X", "num"],
    ["center.y", "Y", "num"], ["width", "W", "num"], ["depth", "D", "num"], ["rotation", "Rot°", "num"], ["room_id", "Room"], ["finish", "Finish", "wide"], ["source", "Source"]],
    blank: () => ({ id: nextId("f", "furniture"), tag: "F", name: "", category: "other", center: { x: 0, y: 0 }, width: 600, depth: 600, rotation: 0, room_id: "", finish: "", source: "manual" }) },
  ceiling_fixtures: { label: "Ceiling", cols: [["id", "ID"], ["kind", "Type", "fixture"], ["position.x", "X", "num"], ["position.y", "Y", "num"],
    ["length", "Length", "num"], ["rotation", "Rot°", "num"], ["room_id", "Room"]],
    blank: () => ({ id: nextId("c", "ceiling_fixtures"), kind: "downlight", position: { x: 0, y: 0 }, length: 0, rotation: 0, room_id: "" }) },
  finishes: { label: "Finishes", cols: [["code", "Code"], ["category", "Type", "finish"], ["description", "Description", "wide"], ["material", "Material", "wide"], ["notes", "Notes", "wide"]],
    blank: () => ({ code: "XX-01", category: "floor", description: "", material: "", notes: "" }) },
};

function nextId(prefix, coll) {
  const used = new Set(state.model[coll].map(x => x.id));
  let n = state.model[coll].length + 1;
  while (used.has(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

const getPath = (o, p) => p.split(".").reduce((a, k) => a?.[k], o);
function setPath(o, p, v) {
  const keys = p.split(".");
  const last = keys.pop();
  keys.reduce((a, k) => a[k], o)[last] = v;
}
const polyToText = pts => pts.map(p => `${Math.round(p.x)},${Math.round(p.y)}`).join("; ");
const textToPoly = t => t.split(";").map(s => s.trim()).filter(Boolean).map(s => {
  const [x, y] = s.split(",").map(Number);
  return { x, y };
}).filter(p => Number.isFinite(p.x) && Number.isFinite(p.y));

function cellInput(row, [path, , type]) {
  const value = getPath(row, path);
  if (type && ENUMS[type]) {
    return h("select", { onchange: e => { setPath(row, path, e.target.value); drawPreview(); } },
      ENUMS[type].map(o => h("option", { value: o, selected: o === value }, o.replaceAll("_", " "))));
  }
  if (type === "poly") {
    return h("input", { class: "wide", value: polyToText(value || []), onchange: e => { setPath(row, path, textToPoly(e.target.value)); drawPreview(); } });
  }
  if (type === "num") {
    return h("input", { class: "num", type: "number", step: "any", value: value ?? 0,
      onchange: e => { setPath(row, path, Number(e.target.value)); drawPreview(); } });
  }
  return h("input", { class: type === "wide" ? "wide" : "", value: value ?? "", onchange: e => { setPath(row, path, e.target.value); drawPreview(); } });
}

function renderTabs() {
  const tabs = [["project", "Project info"], ...Object.entries(TABLES).map(([k, t]) => [k, `${t.label} (${state.model[k].length})`])];
  $("#tabs").replaceChildren(...tabs.map(([k, label]) =>
    h("button", { class: state.tab === k ? "active" : "", onclick: () => { state.tab = k; renderTabs(); renderTab(); } }, label)));
}

function renderTab() {
  const body = $("#tab-body");
  if (state.tab === "project") {
    const p = state.model.project;
    const fields = [["project_name", "Project name"], ["project_number", "Project no."], ["client", "Client"], ["location", "Location"],
      ["company_name", "Company"], ["drawn_by", "Drawn by"], ["checked_by", "Checked by"], ["date", "Date"], ["revision", "Revision"], ["status", "Status"]];
    body.replaceChildren(h("div", { class: "form-grid" }, fields.map(([k, label]) =>
      h("label", {}, label, h("input", { value: p[k] ?? "", onchange: e => { p[k] = e.target.value; } })))));
    return;
  }
  const spec = TABLES[state.tab];
  const rows = state.model[state.tab];
  const table = h("table", { class: "grid" },
    h("thead", {}, h("tr", {}, spec.cols.map(c => h("th", {}, c[1])), h("th"))),
    h("tbody", {}, rows.map((row, i) => h("tr", { "data-id": row.id || row.code },
      spec.cols.map(c => h("td", {}, cellInput(row, c))),
      h("td", {}, h("button", { class: "btn ghost small danger", title: "Delete row",
        onclick: () => { rows.splice(i, 1); renderTabs(); renderTab(); drawPreview(); } }, "×"))))));
  body.replaceChildren(table, h("button", { class: "btn ghost small", onclick: () => {
    rows.push(spec.blank());
    renderTabs();
    renderTab();
    body.scrollTop = body.scrollHeight;
  } }, "+ Add row"));
}

function renderIssues() {
  const issues = state.issues || [];
  const errors = issues.filter(i => i.level === "error").length;
  $("#issue-count").textContent = issues.length ? `${errors} errors · ${issues.length - errors} warnings` : "none";
  $("#issues").replaceChildren(...(issues.length ? issues.map(i =>
    h("li", { class: i.level }, h("b", {}, i.item), i.message)) : [h("li", {}, "No issues found.")]));
  const notes = state.model.extraction_notes || [];
  $("#notes").replaceChildren(...(notes.length ? notes.map(n => h("li", {}, n)) : [h("li", {}, "None.")]));
}

function renderReview() {
  const has = !!state.model;
  $("#review-card").hidden = !has;
  $("#generate-card").hidden = !has;
  if (!has) return;
  if (!(state.tab in TABLES) && state.tab !== "project") state.tab = "walls";
  renderTabs();
  renderTab();
  renderIssues();
  drawPreview();
}

async function save(snap) {
  setStatus("#save-status", "Saving…");
  let data = state.model;
  if (!$("#json").hidden) {
    try { data = JSON.parse($("#json").value); } catch (err) { setStatus("#save-status", "JSON is not valid: " + err.message, "error"); return; }
  }
  try {
    const r = await api(`/api/projects/${state.project.id}/model${snap ? "?snap=true" : ""}`, json("PUT", data));
    state.model = r.model;
    state.issues = r.issues;
    if (!$("#json").hidden) $("#json").value = JSON.stringify(state.model, null, 1);
    renderReview();
    setStatus("#save-status", "Saved.", "ok");
  } catch (err) {
    const d = err.detail;
    const msg = Array.isArray(d) ? d.slice(0, 3).map(e => `${e.loc.join(".")}: ${e.msg}`).join(" | ") : err.message;
    setStatus("#save-status", msg, "error");
  }
}
$("#save").addEventListener("click", () => save(false));
$("#save-snap").addEventListener("click", () => save(true));
$("#toggle-json").addEventListener("click", () => {
  const ta = $("#json");
  ta.hidden = !ta.hidden;
  if (!ta.hidden) ta.value = JSON.stringify(state.model, null, 1);
  $("#toggle-json").textContent = ta.hidden ? "Edit raw JSON" : "Back to tables";
});
for (const cb of $$("[data-show]")) cb.addEventListener("change", () => { state.show[cb.dataset.show] = cb.checked; drawPreview(); });

// ---------------------------------------------------------------- plan preview (SVG, y flipped)

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

function wallPoint(w, d, side = 0) {
  const dx = w.end.x - w.start.x, dy = w.end.y - w.start.y, L = Math.hypot(dx, dy) || 1;
  const ux = dx / L, uy = dy / L;
  return [w.start.x + ux * d - uy * side, w.start.y + uy * d + ux * side];
}

function drawPreview() {
  const svg = $("#preview");
  const m = state.model;
  svg.replaceChildren();
  const pts = [...m.walls.flatMap(w => [w.start, w.end]), ...m.rooms.flatMap(r => r.polygon)];
  if (!pts.length) return;
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  const pad = 800;
  const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
  svg.setAttribute("viewBox", `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`);
  const g = s("g", { transform: "scale(1,-1)" });
  svg.append(g);
  const unit = (x1 - x0) / 900; // ~1 screen px
  const label = (text, x, y, size = 11, weight = 400) => {
    if (!state.show.labels) return;
    const t = s("text", { x, y: -y, "font-size": size * unit, "text-anchor": "middle", "dominant-baseline": "middle", transform: "scale(1,-1)", fill: "#1d2330", "font-weight": weight });
    t.textContent = text;
    g.append(t);
  };

  for (const r of m.rooms) {
    if (r.polygon.length < 3) continue;
    g.append(s("polygon", { points: r.polygon.map(p => `${p.x},${p.y}`).join(" "), fill: "#eef3f9", stroke: "none" }));
  }
  const walls = Object.fromEntries(m.walls.map(w => [w.id, w]));
  for (const w of m.walls) {
    g.append(s("line", { x1: w.start.x, y1: w.start.y, x2: w.end.x, y2: w.end.y, stroke: w.kind === "partition" ? "#7a8291" : "#2b3240",
      "stroke-width": w.thickness, "stroke-linecap": "square" }));
  }
  for (const o of m.openings) {
    const w = walls[o.wall_id];
    if (!w) continue;
    const [ax, ay] = wallPoint(w, o.offset), [bx, by] = wallPoint(w, o.offset + o.width);
    const isWin = o.kind === "window";
    g.append(s("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: isWin ? "#9cc3e6" : "#fff", "stroke-width": w.thickness + 2 }));
    if (o.kind === "door" || o.kind === "double_door") {
      const side = o.swing === "left" ? 1 : -1;
      const leaves = o.kind === "door" ? [[o.hinge === "start" ? o.offset : o.offset + o.width, o.hinge === "start" ? 1 : -1, o.width]]
        : [[o.offset, 1, o.width / 2], [o.offset + o.width, -1, o.width / 2]];
      for (const [hd, toward, leaf] of leaves) {
        const [hx, hy] = wallPoint(w, hd, side * w.thickness / 2);
        const [ox, oy] = wallPoint(w, hd, side * (w.thickness / 2 + leaf));
        const [cx, cy] = wallPoint(w, hd + toward * leaf, side * w.thickness / 2);
        const sweep = ((cx - hx) * (oy - hy) - (cy - hy) * (ox - hx)) > 0 ? 1 : 0;
        g.append(s("path", { d: `M${hx},${hy} L${ox},${oy} A${leaf},${leaf} 0 0 ${sweep} ${cx},${cy}`, fill: "none", stroke: "#b54708", "stroke-width": 2 * unit }));
      }
    }
    const [mx, my] = wallPoint(w, o.offset + o.width / 2);
    label(o.tag, mx, my, 9, 600);
  }
  if (state.show.furniture) {
    for (const f of m.furniture) {
      const r = s("rect", { x: -f.width / 2, y: -f.depth / 2, width: f.width, height: f.depth, fill: "rgba(31,78,121,.08)", stroke: "#1f4e79", "stroke-width": 1.2 * unit,
        transform: `translate(${f.center.x},${f.center.y}) rotate(${f.rotation})`, "stroke-dasharray": f.category === "rug" ? `${6 * unit}` : "none" });
      const t = s("title");
      t.textContent = `${f.tag} ${f.name} (${f.width}x${f.depth})`;
      r.append(t);
      g.append(r);
      // front edge marker
      const a = f.rotation * Math.PI / 180, fx = f.center.x + Math.sin(a) * f.depth / 2, fy = f.center.y - Math.cos(a) * f.depth / 2;
      g.append(s("circle", { cx: fx, cy: fy, r: 3 * unit, fill: "#1f4e79" }));
    }
  }
  if (state.show.fixtures) {
    for (const c of m.ceiling_fixtures) {
      const el = c.kind === "linear_light"
        ? s("rect", { x: -c.length / 2, y: -40, width: c.length || 600, height: 80, transform: `translate(${c.position.x},${c.position.y}) rotate(${c.rotation})`, fill: "#f79009" })
        : s("circle", { cx: c.position.x, cy: c.position.y, r: c.kind.startsWith("ac_") ? 300 : 110, fill: c.kind.startsWith("ac_") ? "none" : "#f79009", stroke: "#b54708", "stroke-width": 1.5 * unit });
      const t = s("title");
      t.textContent = c.kind;
      el.append(t);
      g.append(el);
    }
  }
  for (const r of m.rooms) {
    if (r.polygon.length < 3) continue;
    const cx = r.polygon.reduce((a, p) => a + p.x, 0) / r.polygon.length, cy = r.polygon.reduce((a, p) => a + p.y, 0) / r.polygon.length;
    label(r.name, cx, cy, 12, 600);
  }
}

// ---------------------------------------------------------------- drawings

$("#generate").addEventListener("click", async () => {
  const btn = $("#generate");
  btn.disabled = true;
  setStatus("#generate-status", "Generating DXF and PDF…");
  try {
    state.project = await api(`/api/projects/${state.project.id}/generate`, { method: "POST" });
    setStatus("#generate-status", "Drawing set generated.", "ok");
    renderOutputs();
  } catch (err) {
    if (err.detail?.issues) { state.issues = err.detail.issues; renderIssues(); }
    setStatus("#generate-status", err.message, "error");
  } finally {
    btn.disabled = false;
  }
});

function renderOutputs() {
  const pid = state.project.id;
  const file = n => `/api/projects/${pid}/files/${encodeURIComponent(n)}`;
  $("#downloads").replaceChildren(...state.project.outputs.map(n =>
    h("a", { class: "btn ghost", href: file(n), download: n }, `Download ${n.endsWith(".pdf") ? "PDF set" : "DXF (AutoCAD)"} · ${n}`)));
  const bust = Date.now();
  $("#sheets").replaceChildren(...state.project.sheets.map(sh =>
    h("div", { class: "sheet" }, h("img", { src: `${file(sh.preview)}?v=${bust}`, alt: sh.name, onclick: () => lightbox(`${file(sh.preview)}?v=${bust}`) }), h("div", {}, sh.name))));
}

function lightbox(url) {
  const lb = $("#lightbox");
  $("img", lb).src = url;
  lb.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });

loadProjects();
