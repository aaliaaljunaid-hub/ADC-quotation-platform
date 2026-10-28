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
  status: ["", "new", "reuse", "modify", "relocate", "remove"],
  roomKind: ["room", "platform", "void"],
  stage: ["CD", "SD", "DD", "AP", "IFT", "IFC", "AB"],
  acType: ["wall_mounted", "ceiling_cassette", "concealed_ducted", "floor_standing"],
  furniture: ["sofa", "armchair", "coffee_table", "dining_table", "chair", "bed", "bedside_table", "wardrobe", "desk",
    "office_chair", "tv_unit", "sideboard", "bookshelf", "rug", "kitchen_counter", "kitchen_island", "fridge", "cooker",
    "sink", "wc", "basin", "shower", "bathtub", "reception_desk", "meeting_table", "plant", "other"],
  fixture: ["downlight", "pendant", "linear_light", "spotlight", "wall_light", "ac_supply", "ac_return",
    "smoke_detector", "speaker", "exhaust_fan", "access_panel"],
  finish: ["floor", "ceiling", "wall", "skirting", "joinery"],
};
const STAGE_NAMES = { CD: "Concept Design", SD: "Schematic Design", DD: "Design Development", AP: "Authority / Permit",
  IFT: "Issued for Tender", IFC: "Issued for Construction", AB: "As-Built" };

const lvl = () => state.level || "GF";

// column: [path, label, type]   type: text | num | enum name | poly | wide
const TABLES = {
  levels: { label: "Levels", cols: [["id", "ID (GF, FF…)"], ["name", "Name", "wide"], ["ffl", "FFL mm", "num"]],
    blank: () => ({ id: "FF", name: "First Floor", ffl: 4000 }) },
  walls: { label: "Walls", cols: [["id", "ID"], ["level", "Level"], ["start.x", "Start X", "num"], ["start.y", "Start Y", "num"], ["end.x", "End X", "num"],
    ["end.y", "End Y", "num"], ["thickness", "Thick.", "num"], ["kind", "Type", "wallKind"], ["height", "Height", "num"], ["status", "Status", "status"]],
    blank: () => ({ id: nextId("W", "walls"), level: lvl(), start: { x: 0, y: 0 }, end: { x: 1000, y: 0 }, thickness: 150, kind: "interior", height: 3000, status: "" }) },
  openings: { label: "Doors & windows", cols: [["tag", "Tag"], ["id", "ID"], ["wall_id", "Wall"], ["kind", "Type", "openingKind"], ["offset", "Offset", "num"],
    ["width", "Width", "num"], ["height", "Height", "num"], ["sill_height", "Sill", "num"], ["hinge", "Hinge", "hinge"], ["swing", "Swing", "swing"], ["status", "Status", "status"]],
    blank: () => ({ id: nextId("o", "openings"), tag: "D", wall_id: state.model.walls[0]?.id || "", kind: "door", offset: 0, width: 900, height: 2100, sill_height: 0, hinge: "start", swing: "left", status: "" }) },
  rooms: { label: "Rooms", cols: [["number", "No."], ["name", "Name"], ["level", "Level"], ["kind", "Kind", "roomKind"], ["floor_level", "Floor +mm", "num"],
    ["polygon", "Boundary (x,y; x,y; …)", "poly"], ["floor_finish", "Floor"], ["skirting", "Skirting"], ["wall_finish", "Walls"], ["ceiling_finish", "Ceiling"],
    ["ceiling_height", "CH", "num"], ["id", "ID"]],
    blank: () => ({ id: nextId("r", "rooms"), level: lvl(), number: "", name: "New room", kind: "room", polygon: [], floor_level: 0, floor_finish: "", skirting: "", wall_finish: "", ceiling_finish: "", ceiling_height: 2700 }) },
  furniture: { label: "Furniture (FF&E)", cols: [["tag", "Tag"], ["name", "Name"], ["status", "Status", "status"], ["category", "Category", "furniture"], ["level", "Level"],
    ["center.x", "X", "num"], ["center.y", "Y", "num"], ["width", "W", "num"], ["depth", "D", "num"], ["rotation", "Rot°", "num"], ["room_id", "Room"], ["finish", "Finish", "wide"], ["source", "Source"]],
    blank: () => ({ id: nextId("f", "furniture"), tag: "F", level: lvl(), name: "", category: "other", center: { x: 0, y: 0 }, width: 600, depth: 600, rotation: 0, room_id: "", finish: "", status: "", source: "manual" }) },
  ceiling_fixtures: { label: "Ceiling", cols: [["id", "ID"], ["kind", "Type", "fixture"], ["level", "Level"], ["position.x", "X", "num"], ["position.y", "Y", "num"],
    ["length", "Length", "num"], ["rotation", "Rot°", "num"], ["room_id", "Room"], ["status", "Status", "status"]],
    blank: () => ({ id: nextId("c", "ceiling_fixtures"), level: lvl(), kind: "downlight", position: { x: 0, y: 0 }, length: 0, rotation: 0, room_id: "", status: "" }) },
  ac_units: { label: "AC units", cols: [["tag", "Tag"], ["type", "Type", "acType"], ["level", "Level"], ["room_id", "Room"], ["position.x", "X", "num"], ["position.y", "Y", "num"],
    ["rotation", "Rot°", "num"], ["capacity", "Capacity"], ["mounting_height", "Mtg AFFL", "num"], ["outdoor_location", "Outdoor unit", "wide"], ["status", "Status", "status"], ["notes", "Notes", "wide"]],
    blank: () => ({ id: nextId("a", "ac_units"), tag: `AC-${String(state.model.ac_units.length + 1).padStart(2, "0")}`, level: lvl(), room_id: "", type: "wall_mounted", position: { x: 0, y: 0 }, rotation: 0, capacity: "", mounting_height: 2300, outdoor_location: "", status: "new", notes: "" }) },
  finishes: { label: "Finishes", cols: [["code", "Code"], ["category", "Type", "finish"], ["description", "Description", "wide"], ["material", "Material", "wide"], ["notes", "Notes", "wide"]],
    blank: () => ({ code: "XX-01", category: "floor", description: "", material: "", notes: "" }) },
  revisions: { label: "Revisions", cols: [["rev", "Rev"], ["date", "Date"], ["description", "Description", "wide"], ["by", "By"]],
    blank: () => ({ rev: state.model.project.revision, date: new Date().toISOString().slice(0, 10), description: "", by: "" }) },
};
const rowsOf = key => key === "revisions" ? state.model.project.revisions : state.model[key];

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
      ENUMS[type].map(o => h("option", { value: o, selected: o === value }, o ? o.replaceAll("_", " ") : "TBC")));
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
  const tabs = [["project", "Project info"], ...Object.entries(TABLES).map(([k, t]) => [k, `${t.label} (${rowsOf(k).length})`])];
  $("#tabs").replaceChildren(...tabs.map(([k, label]) =>
    h("button", { class: state.tab === k ? "active" : "", onclick: () => { state.tab = k; renderTabs(); renderTab(); } }, label)));
}

function renderTab() {
  const body = $("#tab-body");
  if (state.tab === "project") {
    const p = state.model.project;
    const fields = [["project_name", "Project name"], ["project_code", "Project code (in sheet numbers)"], ["client", "Client"],
      ["location", "Site address"], ["company_name", "Company"], ["drawn_by", "Drawn by"], ["checked_by", "Checked by"],
      ["approved_by", "Approved by"], ["date", "Issue date"], ["revision", "Current revision"]];
    body.replaceChildren(h("div", { class: "form-grid" },
      h("label", {}, "Issue stage", h("select", { onchange: e => { p.stage = e.target.value; } },
        ENUMS.stage.map(st => h("option", { value: st, selected: st === p.stage }, `${st} - ${STAGE_NAMES[st]}`)))),
      fields.map(([k, label]) =>
        h("label", {}, label, h("input", { value: p[k] ?? "", onchange: e => { p[k] = e.target.value; } })))),
      h("p", { class: "hint" }, "Sheet number: ", h("code", {}, `ADC-${p.project_code || "XXXX"}-ID-201-${p.revision}`),
        ". Revisions are letters (A, B, C) before IFC and numbers (0, 1, 2) from IFC. Log each issue in the Revisions tab."));
    return;
  }
  const spec = TABLES[state.tab];
  const rows = rowsOf(state.tab);
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
  const count = lv => issues.filter(i => i.level === lv).length;
  $("#issue-count").textContent = issues.length ? `${count("error")} errors · ${count("warning")} warnings · ${count("pricing")} pricing` : "none";
  const order = { error: 0, warning: 1, pricing: 2 };
  $("#issues").replaceChildren(...(issues.length ? [...issues].sort((a, b) => order[a.level] - order[b.level]).map(i =>
    h("li", { class: i.level }, h("b", {}, i.item), i.message)) : [h("li", {}, "No issues found.")]));
  const notes = state.model.extraction_notes || [];
  $("#notes").replaceChildren(...(notes.length ? notes.map(n => h("li", {}, n)) : [h("li", {}, "None.")]));
}

function levelIds() {
  const ids = state.model.levels.map(l => l.id);
  for (const coll of ["walls", "rooms", "furniture", "ceiling_fixtures", "ac_units"])
    for (const x of state.model[coll]) if (!ids.includes(x.level)) ids.push(x.level);
  return ids.length ? ids : ["GF"];
}

function renderLevelPicker() {
  const ids = levelIds();
  if (!ids.includes(state.level)) state.level = ids[0];
  $("#level-pick").replaceChildren(...ids.map(id => h("option", { value: id, selected: id === state.level },
    state.model.levels.find(l => l.id === id)?.name || id)));
  $("#level-pick").parentElement.hidden = ids.length < 2;
}

function renderReview() {
  const has = !!state.model;
  $("#review-card").hidden = !has;
  $("#generate-card").hidden = !has;
  if (!has) return;
  renderLevelPicker();
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
$("#level-pick").addEventListener("change", e => { state.level = e.target.value; drawPreview(); });

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
  const all = state.model;
  const lv = lvl();
  const wallIds = new Set(all.walls.filter(w => w.level === lv).map(w => w.id));
  const m = { walls: all.walls.filter(w => wallIds.has(w.id)), openings: all.openings.filter(o => wallIds.has(o.wall_id)),
    rooms: all.rooms.filter(r => r.level === lv), furniture: all.furniture.filter(f => f.level === lv),
    ceiling_fixtures: all.ceiling_fixtures.filter(c => c.level === lv), ac_units: all.ac_units.filter(a => a.level === lv) };
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
    const fill = r.kind === "void" ? "#fff" : r.kind === "platform" ? "#e3ecd9" : "#eef3f9";
    g.append(s("polygon", { points: r.polygon.map(p => `${p.x},${p.y}`).join(" "), fill, stroke: r.kind === "room" ? "none" : "#5b6b47",
      "stroke-width": 1.5 * unit, "stroke-dasharray": r.kind === "void" ? `${8 * unit}` : "none" }));
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
  for (const a of m.ac_units) {
    const [w, d] = { wall_mounted: [900, 300], ceiling_cassette: [840, 840], concealed_ducted: [1100, 700], floor_standing: [500, 300] }[a.type];
    const r = s("rect", { x: -w / 2, y: -d / 2, width: w, height: d, fill: "rgba(2,122,72,.15)", stroke: "#027a48", "stroke-width": 2 * unit,
      transform: `translate(${a.position.x},${a.position.y}) rotate(${a.rotation})` });
    const t = s("title");
    t.textContent = `${a.tag} ${a.type.replaceAll("_", " ")} ${a.capacity}`;
    r.append(t);
    g.append(r);
    label(a.tag, a.position.x, a.position.y - d / 2 - 250, 9, 600);
  }
  for (const r of m.rooms) {
    if (r.polygon.length < 3) continue;
    const cx = r.polygon.reduce((a, p) => a + p.x, 0) / r.polygon.length, cy = r.polygon.reduce((a, p) => a + p.y, 0) / r.polygon.length;
    label(r.kind === "void" ? "VOID" : `${r.number ? r.number + " " : ""}${r.name}`, cx, cy, 12, 600);
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
    h("a", { class: "btn ghost", href: file(n), download: n },
      n.endsWith(".pdf") ? "Download PDF set" : n.endsWith(".zip") ? "Download CAD package (DXF + logo)" : "Download DXF only")));
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
