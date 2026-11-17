import "./style.css";
import Plotly from "plotly.js-dist-min";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import "monaco-editor/esm/vs/editor/editor.all.js";
import "monaco-editor/esm/vs/basic-languages/monaco.contribution";
import "monaco-editor/esm/vs/language/json/monaco.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import { format } from "prettier/standalone";
import babel from "prettier/plugins/babel";
import estree from "prettier/plugins/estree";

self.MonacoEnvironment = {
  getWorker: (_, label) =>
    label === "json" ? new JsonWorker() : new EditorWorker(),
};
monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
  validate: false,
  enableSchemaRequest: false,
  schemas: [],
});
// Language grammars are local; no language service, dataset configuration or network plugin is loaded.
monaco.languages.typescript?.typescriptDefaults?.setEagerModelSync(false);
const $ = (q, el = document) => el.querySelector(q);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const button = (label, fn, cls = "") => {
  const b = el("button", label, cls);
  b.onclick = () => guard(fn);
  return b;
};
const fmt = (v) =>
  v == null
    ? "—"
    : typeof v === "number"
      ? Number.isInteger(v)
        ? v.toLocaleString()
        : v.toFixed(4)
      : String(v);
const state = {
  sid: new URLSearchParams(location.search).get("snapshot"),
  snapshot: null,
  screen: "overview",
  audit: null,
  offset: 0,
  editors: [],
  models: [],
  workflow: "leakage",
};
const screens = [
  ["overview", "01", "Dataset"],
  ["atlas", "02", "Embedding atlas"],
  ["leakage", "03", "Leakage"],
  ["coverage", "04", "Coverage"],
  ["label", "05", "Label review"],
  ["cohorts", "06", "Cohort distances"],
  ["browse", "07", "Source explorer"],
];
$("#app").innerHTML =
  `<aside><div class="brand"><span class="brand-mark">Z</span><div>THE ZOOKEEPER<small>DATASET OBSERVATORY</small></div></div><div class="nav-label">WORKSPACE</div><nav></nav><div class="sidebar-bottom"><span class="dot"></span> Offline by design<small>Original embeddings. Human decisions.</small></div></aside><main><header><div><span class="eyebrow">SOURCE-CODE INTELLIGENCE</span><h1 id="title">Dataset</h1></div><div class="snapshot-selector"><label for="snapshots">Snapshot</label><select id="snapshots"><option value="">Import a dataset</option></select></div></header><div id="notice" role="status" hidden></div><div id="job" hidden></div><section id="content"></section></main><dialog id="viewer"><div id="viewer-top"><div><span class="eyebrow">SOURCE EVIDENCE</span><h2>Code inspection</h2></div><button id="close-viewer" aria-label="Close viewer">Close ×</button></div><div id="evidence"></div><div id="editor-host"></div><div id="viewer-bottom"></div></dialog>`;
for (const [name, num, label] of screens) {
  const b = button("", () => navigate(name));
  b.append(el("span", num), el("span", label));
  b.dataset.screen = name;
  $("nav").append(b);
}
$("#close-viewer").onclick = () => {
  $("#viewer").close();
  disposeEditors();
};
$("#viewer").addEventListener("cancel", disposeEditors);
$("#snapshots").onchange = () =>
  guard(() => selectSnapshot($("#snapshots").value));
async function api(path, options = {}) {
  const r = await fetch("/api" + path, options);
  if (!r.ok) {
    let d;
    try {
      d = await r.json();
    } catch {
      d = { detail: r.statusText };
    }
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail),
    );
  }
  return r.json();
}
const post = (path, body = {}) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
const base = () => `/snapshots/${state.sid}`;
function notify(message, error = false) {
  const n = $("#notice");
  n.hidden = false;
  n.textContent = message;
  n.className = error ? "error" : "";
}
async function guard(fn) {
  try {
    await fn();
  } catch (e) {
    notify(e.message, true);
  }
}
function section(title, subtitle) {
  const p = el("div", undefined, "card");
  if (title) p.append(el("h2", title));
  if (subtitle) p.append(el("p", subtitle, "muted"));
  return p;
}
function select(values, value) {
  const s = el("select");
  for (const item of values) {
    const [v, label] = Array.isArray(item) ? item : [item, item];
    const o = el("option", label);
    o.value = v;
    s.append(o);
  }
  if (value !== undefined) s.value = value;
  return s;
}
function field(label, input) {
  const wrap = el("label", undefined, "field");
  wrap.append(el("span", label), input);
  return wrap;
}
function input(value = "", type = "text") {
  const i = el("input");
  i.type = type;
  i.value = value;
  return i;
}
function table(columns, items, click) {
  const wrap = el("div", undefined, "table-wrap"),
    t = el("table"),
    head = el("thead"),
    hr = el("tr");
  for (const [, title] of columns) hr.append(el("th", title));
  head.append(hr);
  t.append(head);
  const body = el("tbody");
  for (const item of items) {
    const tr = el("tr");
    if (click) {
      tr.tabIndex = 0;
      tr.className = "clickable";
      tr.onclick = () => guard(() => click(item));
      tr.onkeydown = (e) => {
        if (e.key === "Enter") tr.click();
      };
    }
    for (const [key] of columns) {
      const cell = el(
        "td",
        typeof key === "function" ? key(item) : fmt(item[key]),
      );
      tr.append(cell);
    }
    body.append(tr);
  }
  t.append(body);
  wrap.append(t);
  if (!items.length) wrap.append(el("p", "No rows match this view.", "empty"));
  return wrap;
}
function metric(parent, label, value, detail) {
  const m = el("div", undefined, "metric");
  m.append(el("small", label), el("strong", fmt(value)));
  if (detail) m.append(el("span", detail));
  parent.append(m);
}
function plot(node, data, layout = {}) {
  return Plotly.newPlot(
    node,
    data,
    {
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: { family: "system-ui", color: "#51606b", size: 12 },
      margin: { t: 20, b: 45, l: 55, r: 20 },
      ...layout,
    },
    { responsive: true, displaylogo: false },
  );
}
async function reloadSnapshots() {
  const list = await api("/snapshots");
  const s = $("#snapshots");
  s.replaceChildren();
  s.append(new Option("Import a dataset", ""));
  for (const d of list) {
    const o = new Option(
      `${new Date(d.created * 1000).toLocaleString()} · ${d.id.slice(0, 8)} · ${d.status}`,
      d.id,
    );
    s.append(o);
  }
  s.value = state.sid || "";
}
async function selectSnapshot(sid) {
  state.sid = sid;
  state.audit = null;
  state.offset = 0;
  if (sid) {
    state.snapshot = await api(base());
    history.replaceState(null, "", `?snapshot=${sid}`);
    const audits = await api(base() + "/audits");
    state.audit = audits.find((a) => !a.settings.opposite) || null;
  } else {
    state.snapshot = null;
    history.replaceState(null, "", location.pathname);
  }
  await reloadSnapshots();
  await navigate("overview");
}
async function waitJob(jid) {
  const target = $("#job");
  target.hidden = false;
  for (;;) {
    const j = await api("/jobs/" + jid);
    target.replaceChildren(el("span", `${j.kind} · ${j.message}`));
    const p = el("progress");
    p.max = 1;
    p.value = j.progress;
    target.append(p);
    if (j.status === "completed") {
      target.hidden = true;
      return j.result;
    }
    if (["failed", "interrupted"].includes(j.status)) {
      target.hidden = true;
      throw Error(j.message);
    }
    await new Promise((r) => setTimeout(r, 650));
  }
}
async function navigate(name) {
  state.screen = name;
  state.offset = 0;
  $("#title").textContent = screens.find((x) => x[0] === name)[2];
  for (const b of $("nav").children)
    b.classList.toggle("active", b.dataset.screen === name);
  $("#content").replaceChildren();
  if (name === "overview") return overview();
  if (!state.snapshot || state.snapshot.status !== "ready") {
    $("#content").append(
      section(
        "Start with a dataset",
        "Import a Parquet snapshot on the Dataset screen to begin.",
      ),
    );
    return;
  }
  return {
    atlas,
    leakage: () => auditScreen(false),
    coverage,
    label: () => auditScreen(true),
    cohorts,
    browse,
  }[name]();
}

async function overview() {
  const root = $("#content");
  const intro = el("div", undefined, "intro");
  intro.append(
    el("h2", "Know what’s in your corpus."),
    el(
      "p",
      "Trace cross-split copies, find coverage gaps, and review conflicting labels — with source code as the evidence.",
    ),
  );
  root.append(intro);
  if (state.snapshot?.status === "ready") {
    const workflows = el("div", undefined, "workflow-grid");
    for (const [screen, num, title, description] of [
      [
        "leakage",
        "01 / CROSS-SPLIT AUDIT",
        "Trace near-copies",
        "Compare each evaluation item with its closest training match. Set a cutoff and inspect the evidence.",
      ],
      [
        "coverage",
        "02 / TRAINING COVERAGE",
        "Find the missing pieces",
        "Start with weak matches. Compare slices by label, source, language and actual text length.",
      ],
      [
        "label",
        "03 / LABEL QUALITY",
        "Review conflicting labels",
        "Explore connected groups of close opposite-label neighbours. Preserve the differences that matter.",
      ],
    ]) {
      const card = button("", () => navigate(screen), "workflow-card");
      card.append(
        el("span", num, "eyebrow"),
        el("h3", title + " →"),
        el("p", description),
      );
      workflows.append(card);
    }
    root.append(workflows);
  }
  const imports = section(
    "Import a snapshot",
    "Your source stays in the persistent local volume. MinIO uses server-configured, read-only credentials.",
  );
  const form = el("div", undefined, "toolbar");
  const bucket = input(),
    key = input();
  bucket.placeholder = "dataset-bucket";
  key.placeholder = "exports/code.parquet";
  form.append(
    field("MinIO bucket", bucket),
    field("Parquet object key", key),
    button("Open snapshot", () => importMinio(false), "primary"),
    button("Refresh snapshot", () => importMinio(true)),
  );
  imports.append(form);
  const upload = input("", "file");
  upload.accept = ".parquet";
  imports.append(field("Or upload local Parquet", upload));
  upload.onchange = () =>
    guard(async () => {
      if (!upload.files[0]) return;
      const f = new FormData();
      f.append("file", upload.files[0]);
      const r = await api("/import/upload", { method: "POST", body: f });
      await waitJob(r.job);
      await selectSnapshot(r.snapshot);
    });
  root.append(imports);
  async function importMinio(refresh) {
    const r = await post("/import/minio", {
      bucket: bucket.value,
      key: key.value,
      refresh,
    });
    if (r.job) await waitJob(r.job);
    notify(
      r.reused
        ? "Reopened saved snapshot. Use Refresh snapshot for a new version."
        : "Snapshot imported.",
    );
    await selectSnapshot(r.snapshot);
  }
  if (state.snapshot?.status === "ready") {
    const s = state.snapshot.summary;
    const stats = el("div", undefined, "metrics");
    metric(stats, "RECORDS", s.rows);
    metric(
      stats,
      "SEARCHABLE",
      s.embeddings.usable || 0,
      `${s.dimension} dimensions`,
    );
    metric(
      stats,
      "EXCLUDED FROM VECTORS",
      s.rows - (s.embeddings.usable || 0),
      "Text and hash inspection available",
    );
    metric(stats, "SNAPSHOT", state.sid.slice(0, 8), "Saved locally");
    root.append(stats);
    const grid = el("div", undefined, "grid");
    for (const [f, title] of [
      ["split", "Splits"],
      ["label", "Labels"],
      ["language", "Languages"],
    ]) {
      const card = section(title);
      card.append(
        table(
          [
            ["name", title],
            ["count", "Records"],
          ],
          Object.entries(s[f]).map(([name, count]) => ({ name, count })),
        ),
      );
      grid.append(card);
    }
    root.append(grid);
    const size = section(
      "File-size distribution",
      "Units are exactly as supplied by the dataset.",
    );
    size.append(
      table(
        [
          ["range", "Range"],
          ["count", "Rows"],
        ],
        s.size_histogram,
      ),
    );
    size.append(
      el(
        "p",
        `Min / p10 / median / p90 / max: ${s.size_quantiles.map(fmt).join(" / ")}`,
      ),
    );
    root.append(size);
    if (s.excluded_by_split.length) {
      const bad = section(
        "Embedding exclusions",
        "Missing, zero and non-finite vectors remain available in Source explorer and exact hashes.",
      );
      bad.append(
        table(
          [
            ["split", "Split"],
            ["state", "Reason"],
            ["count", "Rows"],
          ],
          s.excluded_by_split,
        ),
      );
      root.append(bad);
    }
    root.append(
      el(
        "p",
        `Stable snapshot link: ${location.origin}/?snapshot=${state.sid}`,
        "muted",
      ),
    );
  }
  const jobs = await api("/jobs");
  if (jobs.length) {
    const p = section(
      "Recent jobs",
      "Completed results survive a server restart. Interrupted jobs can be resubmitted.",
    );
    p.append(
      table(
        [
          ["kind", "Job"],
          ["status", "Status"],
          ["message", "Progress"],
        ],
        jobs.slice(0, 10),
      ),
    );
    root.append(p);
  }
}

function scopeControls(parent, change) {
  const scope = {};
  for (const [key, label] of [
    ["split", "Split"],
    ["label", "Label"],
    ["language", "Language"],
    ["source", "Source"],
    ["repository", "Repository"],
  ]) {
    let node;
    if (state.snapshot.summary[key])
      node = select([["", "All"], ...Object.keys(state.snapshot.summary[key])]);
    else {
      node = input();
      node.placeholder = "All / unknown";
    }
    node.onchange = () => {
      scope[key] = node.value;
      guard(() => change({ ...scope }));
    };
    parent.append(field(label, node));
  }
  for (const [key, label] of [
    ["size_min", "Minimum size"],
    ["size_max", "Size below"],
  ]) {
    const node = input("", "number");
    node.min = 0;
    node.onchange = () => {
      scope[key] = node.value === "" ? null : Number(node.value);
      guard(() => change({ ...scope }));
    };
    parent.append(field(label, node));
  }
  const extras = (state.snapshot.summary.scalar_fields || []).filter(
    (k) => !["language", "source", "repository"].includes(k),
  );
  if (extras.length) {
    const key = select([["", "No extra field"], ...extras]),
      value = input();
    value.placeholder = "Exact scalar value";
    function update() {
      for (const k of Object.keys(scope))
        if (k.startsWith("extra:")) delete scope[k];
      if (key.value && value.value) scope["extra:" + key.value] = value.value;
      guard(() => change({ ...scope }));
    }
    key.onchange = value.onchange = update;
    parent.append(field("Extra metadata", key), field("Value", value));
  }
  return scope;
}
function auditControls(parent, opposite, onRun) {
  const toolbar = el("div", undefined, "toolbar");
  const splits = Object.keys(state.snapshot.summary.split);
  const source = select(
    opposite ? [["", "All splits"], ...splits] : splits,
    splits.includes("test") ? "test" : splits[0],
  );
  const reference = select(
    opposite ? [["", "All splits"], ...splits] : splits,
    opposite ? "" : splits.includes("train") ? "train" : splits[0],
  );
  const mode = select([
    ["auto", "Auto: exact small / approximate large"],
    ["exact_cpu", "Exact CPU"],
    ["exact_gpu", "Exact GPU (batched / sharded)"],
    ["approximate", "Approximate + float32 reranking"],
  ]);
  toolbar.append(
    field("Source split", source),
    field("Reference split", reference),
    field("Search mode", mode),
    button(
      "Run audit",
      () =>
        onRun({
          source: source.value ? { split: source.value } : {},
          reference: reference.value ? { split: reference.value } : {},
          mode: mode.value,
          opposite,
        }),
      "primary",
    ),
  );
  parent.append(toolbar);
  return { source, reference, mode };
}
async function run(settings) {
  const j = await post(base() + "/audits", settings);
  const result = await waitJob(j.job);
  if (!settings.opposite) state.audit = result;
  return result;
}
function histogram(parent, bins) {
  const div = el("div", undefined, "histogram");
  parent.append(div);
  plot(
    div,
    [
      {
        x: bins.map((b) => -1 + (b.bin + 0.5) * 0.05),
        y: bins.map((b) => b.count),
        type: "bar",
        marker: { color: "#167b78" },
        width: 0.048,
      },
    ],
    {
      xaxis: {
        title: { text: "Best cosine similarity → closer" },
        range: [-1, 1],
      },
      yaxis: { title: { text: "Source rows" } },
      height: 230,
    },
  );
}

async function auditScreen(opposite) {
  const root = $("#content");
  const p = section(
    opposite
      ? "Very similar code. Different labels."
      : "Find candidates across splits.",
    opposite
      ? "Search specifically for opposite-label neighbours, even when the nearest overall neighbour has the same label. Labels remain intact."
      : "Each source row is compared with the selected reference population in the original embedding space. A cutoff controls the review queue.",
  );
  root.append(p);
  const controls = auditControls(p, opposite, async (settings) => {
    current = await run(settings);
    offset = 0;
    await render();
  });
  let current = null,
    offset = 0,
    scope = {},
    reviewStatus = "";
  const audits = await api(base() + "/audits");
  current = audits.find((a) => a.settings.opposite === opposite) || null;
  function showAuditSettings() {
    if (!current) return;
    controls.source.value = current.settings.source.split || "";
    controls.reference.value = current.settings.reference.split || "";
    controls.mode.value = current.settings.mode;
  }
  showAuditSettings();
  const cached = select(
    [
      ["", "Choose a saved audit"],
      ...audits
        .filter((a) => a.settings.opposite === opposite)
        .map((a) => [
          a.key,
          `${a.key.slice(0, 8)} · ${JSON.stringify(a.settings.source)} → ${JSON.stringify(a.settings.reference)} · ${a.modes.join(", ")}`,
        ]),
    ],
    current?.key || "",
  );
  cached.onchange = () =>
    guard(async () => {
      current = audits.find((a) => a.key === cached.value);
      showAuditSettings();
      offset = 0;
      await render();
    });
  p.append(field("Saved results", cached));
  const threshold = el("div", undefined, "toolbar");
  const range = input(opposite ? 0.95 : 0.95, "range"),
    number = input(0.95, "number");
  for (const i of [range, number]) {
    i.min = -1;
    i.max = 1;
    i.step = 0.005;
  }
  threshold.append(
    field("Similarity cutoff", range),
    field("Numeric cutoff", number),
  );
  const queueView = select([
    ["pairs", "Ranked pairs"],
    ["groups", "Conflict groups"],
  ]);
  if (opposite) {
    threshold.append(field("Queue view", queueView));
    queueView.onchange = () =>
      guard(async () => {
        offset = 0;
        await render();
      });
  }
  const results = el("div");
  root.append(threshold, results);
  let debounce,
    renderVersion = 0;
  for (const i of [range, number])
    i.oninput = () => {
      const v = Math.min(1, Math.max(-1, Number(i.value)));
      range.value = number.value = v;
      offset = 0;
      clearTimeout(debounce);
      debounce = setTimeout(() => guard(render), 100);
    };
  const filters = el("div", undefined, "toolbar");
  scopeControls(filters, async (s) => {
    scope = s;
    offset = 0;
    await render();
  });
  const decisions = opposite
    ? ["unreviewed", "suspected mislabel", "labels justified", "unsure"]
    : [
        "unreviewed",
        "confirmed leakage",
        "related but acceptable",
        "distinct",
        "unsure",
      ];
  const review = select([["", "All review states"], ...decisions]);
  review.onchange = () =>
    guard(async () => {
      reviewStatus = review.value;
      offset = 0;
      await render();
    });
  filters.append(field("Review", review));
  root.insertBefore(filters, results);
  async function render() {
    const version = ++renderVersion;
    results.replaceChildren();
    if (!current) {
      results.append(
        el("p", "Choose a direction and run your first audit.", "empty"),
      );
      return;
    }
    const r = await api(
      `${base()}/audits/${current.key}?cutoff=${number.value}&offset=${offset}&scope=${encodeURIComponent(JSON.stringify(scope))}&review_status=${encodeURIComponent(reviewStatus)}`,
    );
    if (version !== renderVersion) return;
    const stats = el("div", undefined, "metrics");
    metric(stats, "SOURCE POPULATION", r.population);
    metric(
      stats,
      "SEARCHABLE",
      r.searchable,
      `${r.no_match} returned no match`,
    );
    metric(
      stats,
      "AT / ABOVE CUTOFF",
      r.above_cutoff,
      `${r.percentage_population.toFixed(1)}% of source population · ${r.percentage_searchable.toFixed(1)}% of searchable`,
    );
    metric(stats, "SEARCH METHOD", r.modes.join(", "));
    results.append(stats);
    histogram(results, r.histogram);
    results.append(
      el(
        "p",
        `${r.count.toLocaleString()} ${opposite ? "unique pairs" : "candidate source rows"} in the filtered queue. Cosine similarity ranges from −1 to 1; higher means closer.`,
        "muted",
      ),
    );
    if (r.modes.includes("approximate_reranked"))
      results.append(
        button(
          "Verify deterministic sample with exact CPU (up to 1,000 rows)",
          async () => {
            current = await run({
              ...current.settings,
              mode: "exact_cpu",
              sample: 1000,
            });
            await render();
          },
        ),
      );
    let queueCount = r.count;
    if (opposite && queueView.value === "groups") {
      const query = new URLSearchParams({
        cutoff: number.value,
        scope: JSON.stringify(scope),
        review_status: reviewStatus,
      });
      const groups = await api(
        `${base()}/audits/${current.key}/groups?${query}&offset=${offset}`,
      );
      if (version !== renderVersion) return;
      queueCount = groups.count;
      const grid = el("div", undefined, "conflict-groups");
      const details = el("div");
      results.append(
        el("h2", `${groups.count} conflict groups`),
        el("p", groups.meaning, "muted"),
        grid,
        details,
      );
      for (const group of groups.items) {
        const card = button(
          "",
          async () => {
            for (const c of grid.children) c.classList.remove("selected");
            card.classList.add("selected");
            async function members(memberOffset = 0) {
              const detail = await api(
                `${base()}/audits/${current.key}/groups/${group.group_id}?${query}&offset=${memberOffset}`,
              );
              const panel = section(
                `Group ${group.group_id} · ${detail.count} texts`,
                `Labels: ${group.labels}. Review the scored connections below; original labels and meaningful word/line differences remain visible.`,
              );
              panel.append(
                table(
                  [
                    ["path", "Text / source"],
                    ["label", "Label"],
                    ["split", "Split"],
                    ["language", "Language"],
                  ],
                  detail.items,
                  (row) => openViewer(row.row_id),
                ),
              );
              const pages = el("div", undefined, "pager");
              pages.append(
                button("← Previous members", () =>
                  members(Math.max(0, memberOffset - 50)),
                ),
                el(
                  "span",
                  `${memberOffset + 1}–${Math.min(memberOffset + 50, detail.count)} / ${detail.count}`,
                ),
                button("More members →", () =>
                  members(
                    memberOffset + 50 < detail.count
                      ? memberOffset + 50
                      : memberOffset,
                  ),
                ),
              );
              panel.append(
                pages,
                el("h3", "Scored opposite-label connections"),
                table(
                  [
                    ["path", "Source"],
                    ["label", "Label"],
                    ["match_path", "Neighbour"],
                    ["match_label", "Label"],
                    ["similarity", "Cosine similarity"],
                    ["review_status", "Review"],
                  ],
                  detail.pairs,
                  (pair) =>
                    openViewer(pair.a, pair.b, pair.similarity, "label"),
                ),
              );
              details.replaceChildren(panel);
            }
            await members();
          },
          "conflict-card",
        );
        card.setAttribute(
          "aria-label",
          `Open conflict group ${group.group_id}`,
        );
        card.append(
          el("span", `GROUP ${group.group_id}`, "eyebrow"),
          el("strong", `${group.rows} texts · ${group.label_count} labels`),
          el("span", group.labels, "group-labels"),
          el("p", group.example_path, "muted"),
          el(
            "span",
            `${group.pair_count} connections · strongest ${fmt(group.strongest)} · inspect →`,
            "group-footer",
          ),
        );
        grid.append(card);
      }
      if (!groups.items.length)
        grid.append(
          el(
            "p",
            "No conflict groups at this cutoff. Lower the cutoff or widen the source scope.",
            "empty",
          ),
        );
    } else
      results.append(
        table(
          [
            ["path", "Source"],
            ["label", "Label"],
            ["split", "Split"],
            ["language", "Language"],
            ["size", "Size"],
            ["match_path", "Closest reference"],
            ["match_label", "Label"],
            ["match_split", "Split"],
            ["match_language", "Language"],
            ["match_size", "Size"],
            ["similarity", "Cosine similarity"],
            ["review_status", "Review"],
          ],
          r.items,
          (x) =>
            openViewer(x.a, x.b, x.similarity, opposite ? "label" : "leakage"),
        ),
      );
    const pages = el("div", undefined, "pager");
    pages.append(
      button("← Previous", async () => {
        offset = Math.max(0, offset - 50);
        await render();
      }),
      el(
        "span",
        `${queueCount ? offset + 1 : 0}–${Math.min(offset + 50, queueCount)} / ${queueCount}`,
      ),
      button("Next →", async () => {
        if (offset + 50 < queueCount) {
          offset += 50;
          await render();
        }
      }),
    );
    results.append(pages);
  }
  await render();
  if (!opposite) {
    const hashCard = section(
      "Exact supplied hashes",
      "Equal, non-empty supplied hashes are grouped separately. Includes rows without usable embeddings.",
    );
    const target = el("div");
    let ho = 0;
    async function loadHashes() {
      const r = await api(
        `${base()}/hashes?source=${encodeURIComponent(controls.source.value)}&reference=${encodeURIComponent(controls.reference.value)}&offset=${ho}`,
      );
      target.replaceChildren(
        el("p", `${r.count} cross-split hash groups`),
        table(
          [
            ["hash", "Supplied hash"],
            ["source_rows", "Source rows"],
            ["reference_rows", "Reference rows"],
          ],
          r.items,
          async (x) => {
            await openViewer(x.a, x.b, null, "leakage");
            const all = await api(
              `${base()}/rows?hash=${encodeURIComponent(x.hash)}&limit=200`,
            );
            const extra = section(
              "Rows with this hash",
              `${all.count} total rows; up to 200 shown. Source explorer supports the same hash filter.`,
            );
            extra.append(
              table(
                [
                  ["path", "Path"],
                  ["split", "Split"],
                  ["label", "Label"],
                  ["embedding_state", "Embedding"],
                ],
                all.items,
                (i) => openViewer(i.row_id),
              ),
            );
            $("#viewer-bottom").append(extra);
          },
        ),
      );
    }
    hashCard.append(
      button("Load exact hashes", loadHashes),
      target,
      button("More hash groups", async () => {
        ho += 50;
        await loadHashes();
      }),
    );
    root.append(hashCard);
  }
}

async function coverage() {
  const root = $("#content");
  const p = section(
    "Where training has fewer close examples.",
    "Low nearest-training similarity is evidence of weak coverage in this embedding space. It may reflect novel code, model limitations or dataset issues.",
  );
  root.append(p);
  auditControls(p, false, async (s) => {
    state.audit = await run(s);
    await render();
  });
  const target = el("div");
  const toolbar = el("div", undefined, "toolbar");
  const cutoff = input(0.6, "number");
  cutoff.min = -1;
  cutoff.max = 1;
  cutoff.step = 0.01;
  const group = select([
    "language",
    "label",
    "source",
    ["text_length", "Text length (characters)"],
    ["size", "File size (dataset units)"],
  ]);
  const order = select([
    ["median", "Median similarity ↑"],
    ["p10", "p10 ↑"],
    ["sample_size", "Slice size ↓"],
    ["fraction_below", "Fraction below ↓"],
  ]);
  let scope = {},
    lowOffset = 0,
    renderVersion = 0;
  toolbar.append(
    field("Review cutoff", cutoff),
    field("Slice by", group),
    field("Sort", order),
  );
  for (const i of [cutoff, group, order])
    i.onchange = () => {
      lowOffset = 0;
      guard(render);
    };
  scopeControls(toolbar, async (s) => {
    scope = s;
    lowOffset = 0;
    await render();
  });
  root.append(toolbar, target);
  async function render() {
    const version = ++renderVersion;
    target.replaceChildren();
    if (!state.audit) {
      target.append(
        el(
          "p",
          "Run an evaluation → train audit to see coverage slices. The Leakage screen reuses these results.",
          "empty",
        ),
      );
      return;
    }
    const key = state.audit.key;
    const overview = await api(
      `${base()}/audits/${key}?cutoff=${cutoff.value}&low=true&limit=50&offset=${lowOffset}&scope=${encodeURIComponent(JSON.stringify(scope))}`,
    );
    if (version !== renderVersion) return;
    histogram(target, overview.histogram);
    target.append(
      el(
        "p",
        `${overview.population} source rows · ${overview.searchable} searchable · ${overview.matched} valid matches · ${overview.no_match} no match · ${overview.modes.join(", ")}`,
        "muted",
      ),
    );
    if (overview.modes.includes("approximate_reranked"))
      target.append(
        button("Rerun 1,000-row sample with exact CPU", async () => {
          state.audit = await run({
            ...state.audit.settings,
            mode: "exact_cpu",
            sample: 1000,
          });
          await render();
        }),
      );
    const r = await api(
      `${base()}/audits/${key}/coverage?field=${group.value}&cutoff=${cutoff.value}&sort=${order.value}&scope=${encodeURIComponent(JSON.stringify(scope))}`,
    );
    if (version !== renderVersion) return;
    const slices = table(
      [
        ["slice", "Slice"],
        ["sample_size", "Evaluation rows"],
        ["reference_rows", "Reference rows"],
        ["valid_matches", "Valid matches"],
        ["median", "Median"],
        ["p10", "p10"],
        [
          (x) =>
            x.fraction_below == null
              ? "—"
              : (100 * x.fraction_below).toFixed(1) + "%",
          "Below cutoff",
        ],
        ["missing_embedding", "Missing embedding"],
        ["no_match", "No match"],
        [(x) => (x.sample_size < 20 ? "Small slice" : ""), "Context"],
      ],
      r.items,
      async (x) => {
        let sliceScope = { ...scope };
        if (["size", "text_length"].includes(group.value)) {
          const ranges = {
            "0–999": [0, 1000],
            "1000–9999": [1000, 10000],
            "10000–99999": [10000, 100000],
            "100000+": [100000, null],
          };
          const [lo, hi] = ranges[x.slice];
          sliceScope[group.value + "_min"] = lo;
          if (hi) sliceScope[group.value + "_max"] = hi;
        } else sliceScope[group.value] = x.slice;
        const r = await api(
          `${base()}/audits/${key}/representatives?scope=${encodeURIComponent(JSON.stringify(sliceScope))}`,
        );
        const samples = section(
          `Examples: ${x.slice}`,
          `${x.sample_size} slice rows; ${r.count} valid matches. Lowest scores and examples around the median.`,
        );
        samples.append(
          table(
            [
              ["path", "Source"],
              ["label", "Label"],
              ["match_path", "Closest training match"],
              ["similarity", "Similarity"],
            ],
            r.items,
            (i) => openViewer(i.a, i.b, i.similarity, "leakage"),
          ),
        );
        target.append(samples);
        samples.scrollIntoView({ behavior: "smooth" });
      },
    );
    slices.classList.add("coverage-slices");
    target.append(slices);
    const weak = section(
      "Lowest-coverage examples",
      `${overview.count} matching evaluation rows below ${cutoff.value}. Open any row beside its closest reference; missing embeddings and no-match rows remain separate in the slice counts.`,
    );
    weak.append(
      table(
        [
          ["path", "Evaluation text / source"],
          ["label", "Label"],
          ["language", "Language"],
          ["match_path", "Closest reference"],
          ["similarity", "Cosine similarity"],
        ],
        overview.items,
        (row) => openViewer(row.a, row.b, row.similarity, "leakage"),
      ),
    );
    const pages = el("div", undefined, "pager");
    pages.append(
      button("← Previous examples", async () => {
        lowOffset = Math.max(0, lowOffset - 50);
        await render();
      }),
      el(
        "span",
        `${overview.count ? lowOffset + 1 : 0}–${Math.min(lowOffset + 50, overview.count)} / ${overview.count}`,
      ),
      button("More examples →", async () => {
        if (lowOffset + 50 < overview.count) {
          lowOffset += 50;
          await render();
        }
      }),
    );
    weak.append(pages);
    target.append(weak);
  }
  await render();
}

async function atlas() {
  const root = $("#content");
  const p = section(
    "Navigate the embedding space.",
    "Projections are for navigation. Similarity scores always use the original embeddings. Click a point to inspect source and neighbours.",
  );
  root.append(p);
  const toolbar = el("div", undefined, "toolbar"),
    method = select([
      ["pca", "PCA · all searchable rows"],
      ["umap", "UMAP · up to 15,000 rows"],
      ["tsne", "Barnes–Hut t-SNE · up to 5,000 rows"],
    ]),
    color = select([
      "split",
      "label",
      "language",
      "source",
      "repository",
      "training_similarity",
      ...(state.snapshot.summary.scalar_fields || [])
        .filter((k) => !["language", "source", "repository"].includes(k))
        .map((k) => ["extra:" + k, k]),
    ]),
    opacity = select([
      ["fixed", "Fixed opacity"],
      ["size", "Opacity by size"],
    ]);
  let scope = {},
    renderVersion = 0;
  toolbar.append(
    field("Projection", method),
    field("Colour by", color),
    field("Opacity", opacity),
    button(
      "Fit / open projection",
      async () => {
        await waitJob(
          (await post(base() + "/projections/" + method.value)).job,
        );
        await render();
      },
      "primary",
    ),
  );
  scopeControls(toolbar, async (s) => {
    scope = s;
    await render();
  });
  root.append(toolbar);
  const caption = el("p", undefined, "muted"),
    chart = el("div", undefined, "atlas");
  root.append(caption, chart);
  for (const s of [method, color, opacity]) s.onchange = () => guard(render);
  async function render() {
    const version = ++renderVersion;
    let r;
    try {
      r = await api(
        `${base()}/projections/${method.value}?scope=${encodeURIComponent(JSON.stringify(scope))}${state.audit ? "&audit_key=" + state.audit.key : ""}`,
      );
    } catch (e) {
      chart.replaceChildren(
        el("p", e.message + ". Use Fit / open projection.", "empty"),
      );
      return;
    }
    if (version !== renderVersion) return;
    caption.textContent = `${r.shown.toLocaleString()} points shown / ${r.filtered_rows.toLocaleString()} filtered projected rows / ${r.projection_rows.toLocaleString()} projection rows. Snapshot: ${state.snapshot.summary.rows.toLocaleString()} rows. Row identities remain stable.`;
    if (color.value.startsWith("extra:"))
      for (const item of r.items)
        item[color.value] =
          JSON.parse(item.extra || "{}")[color.value.slice(6)] ?? "unknown";
    const numeric = color.value === "training_similarity";
    if (numeric)
      caption.textContent +=
        " Grey points have no saved match in the selected audit.";
    const groups = new Map();
    for (const row of r.items) {
      const category = numeric
        ? row.training_similarity == null
          ? "No saved match"
          : "Scored evaluation rows"
        : (row[color.value] ?? "unknown");
      const alpha =
        opacity.value === "size"
          ? Math.round(
              Math.min(0.95, Math.max(0.1, Math.log10(1 + row.size) / 6)) * 5,
            ) / 5
          : 0.8;
      const key = JSON.stringify([category, alpha]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }
    const traces = [];
    const names = new Map();
    const palette = [
      "#167b78",
      "#ce9741",
      "#7179af",
      "#bd687f",
      "#76a15a",
      "#609abb",
    ];
    for (const [group, items] of groups) {
      const [name, alpha] = JSON.parse(group);
      const first = !names.has(name);
      const scored = numeric && name === "Scored evaluation rows";
      if (first) names.set(name, palette[names.size % palette.length]);
      traces.push({
        type: "scatter3d",
        mode: "markers",
        name,
        legendgroup: name,
        showlegend: first,
        x: items.map((x) => x.x),
        y: items.map((x) => x.y),
        z: items.map((x) => x.z),
        customdata: items.map((x) => x.row_id),
        text: items.map((x) => `${x.row_id} · ${x.split} · ${x.label}`),
        hoverinfo: "text",
        marker: {
          size: 3,
          opacity: alpha,
          color: numeric && !scored ? "#b8c4bf" : names.get(name),
          ...(scored
            ? {
                color: items.map((x) => x.training_similarity),
                colorscale: "Viridis",
                cmin: -1,
                cmax: 1,
                colorbar: { title: "Nearest train" },
                showscale: first,
              }
            : {}),
        },
      });
    }
    await plot(chart, traces, {
      height: 600,
      margin: { t: 10, b: 10, l: 0, r: 0 },
      scene: {
        xaxis: { title: "Projection 1" },
        yaxis: { title: "Projection 2" },
        zaxis: { title: "Projection 3" },
      },
    });
    chart.removeAllListeners?.("plotly_click");
    chart.on("plotly_click", (e) =>
      guard(() => openViewer(e.points[0].customdata)),
    );
  }
  await render();
}

async function cohorts() {
  const root = $("#content");
  const p = section(
    "Compare random pairs between cohorts.",
    "Cosine distance = 1 − cosine similarity. This is a random-pair comparison, distinct from each row’s nearest-neighbour score. Samples use a reproducible seed, with replacement; self-pairs are reported when cohorts overlap.",
  );
  root.append(p);
  const toolbar = el("div", undefined, "toolbar");
  const splits = Object.keys(state.snapshot.summary.split),
    left = select(splits, "test"),
    right = select(splits, "train");
  const labelA = select([
      ["", "All labels"],
      ...Object.keys(state.snapshot.summary.label),
    ]),
    labelB = select([
      ["", "All labels"],
      ...Object.keys(state.snapshot.summary.label),
    ]);
  toolbar.append(
    field("Cohort A split", left),
    field("A label", labelA),
    field("Cohort B split", right),
    field("B label", labelB),
    button(
      "Compare 10,000 random pairs",
      async () => {
        const a = { split: left.value },
          b = { split: right.value };
        if (labelA.value) a.label = labelA.value;
        if (labelB.value) b.label = labelB.value;
        const r = await post(base() + "/cohorts", { left: a, right: b });
        target.replaceChildren();
        const stats = el("div", undefined, "metrics");
        metric(stats, "POPULATIONS", `${r.population_a} / ${r.population_b}`);
        metric(
          stats,
          "SAMPLED PAIRS",
          r.sample_pairs,
          `${r.self_pairs} self-pairs`,
        );
        metric(stats, "MEAN / MEDIAN", `${fmt(r.mean)} / ${fmt(r.median)}`);
        metric(
          stats,
          "P10 / P25 / P75 / P90",
          r.quantiles.map(fmt).join(" / "),
        );
        target.append(stats);
        const chart = el("div");
        target.append(chart);
        await plot(
          chart,
          [
            {
              type: "bar",
              x: r.counts.map((_, i) => (r.edges[i] + r.edges[i + 1]) / 2),
              y: r.counts,
              marker: { color: "#167b78" },
            },
          ],
          { xaxis: { title: { text: r.kind } }, height: 350 },
        );
      },
      "primary",
    ),
  );
  p.append(toolbar);
  const target = el("div");
  root.append(target);
}

async function browse() {
  const root = $("#content");
  const p = section(
    "Inspect original source.",
    "Search all rows, including unusable embeddings. Source text is loaded only when opened.",
  );
  root.append(p);
  const toolbar = el("div", undefined, "toolbar"),
    text = input(),
    hash = input();
  text.placeholder = "Path or source contains…";
  hash.placeholder = "Exact supplied hash";
  let scope = {},
    offset = 0;
  scopeControls(toolbar, async (s) => {
    scope = s;
    offset = 0;
    await render();
  });
  toolbar.append(
    field("Text search", text),
    field("Hash", hash),
    button("Search", async () => {
      offset = 0;
      await render();
    }),
  );
  p.append(toolbar);
  const target = el("div");
  root.append(target);
  async function render() {
    const r = await api(
      `${base()}/rows?scope=${encodeURIComponent(JSON.stringify(scope))}&text=${encodeURIComponent(text.value)}&offset=${offset}${hash.value ? "&hash=" + encodeURIComponent(hash.value) : ""}`,
    );
    target.replaceChildren(
      el("p", `${r.count} matching rows`),
      table(
        [
          ["path", "Path"],
          ["split", "Split"],
          ["label", "Label"],
          ["language", "Language"],
          ["size", "Size"],
          ["embedding_state", "Embedding"],
        ],
        r.items,
        (x) => openViewer(x.row_id),
      ),
    );
    target.append(
      button("← Previous", async () => {
        offset = Math.max(0, offset - 50);
        await render();
      }),
      button("Next →", async () => {
        if (offset + 50 < r.count) {
          offset += 50;
          await render();
        }
      }),
    );
  }
  await render();
}

function disposeEditors() {
  for (const e of state.editors) e.dispose();
  for (const m of state.models) m.dispose();
  state.editors = [];
  state.models = [];
}
async function openViewer(
  a,
  b = null,
  similarity = null,
  workflow = "leakage",
  full = false,
) {
  disposeEditors();
  state.workflow = workflow;
  const rows = await Promise.all([
    api(`${base()}/rows/${a}?full=${full}`),
    ...(b != null ? [api(`${base()}/rows/${b}?full=${full}`)] : []),
  ]);
  const d = $("#viewer");
  if (!d.open) d.showModal();
  const evidence = $("#evidence"),
    host = $("#editor-host"),
    bottom = $("#viewer-bottom");
  evidence.replaceChildren();
  host.replaceChildren();
  bottom.replaceChildren();
  const info = el("div", undefined, "pane-info");
  evidence.append(info);
  const versions = rows.map((r) => ({
    original: r.content,
    formatted: null,
    show: "original",
    language: r.language,
  }));
  const health = await api("/health");
  const options = {
    readOnly: true,
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13,
    lineNumbers: "on",
    scrollBeyondLastLine: false,
    wordWrap: "off",
    renderSideBySide: true,
    originalEditable: false,
    ignoreTrimWhitespace: false,
  };
  const models = rows.map((r) =>
    monaco.editor.createModel(r.content, r.language),
  );
  state.models = models;
  let editor;
  if (rows.length === 2) {
    editor = monaco.editor.createDiffEditor(host, options);
    editor.setModel({ original: models[0], modified: models[1] });
  } else {
    editor = monaco.editor.create(host, { ...options, model: models[0] });
  }
  state.editors = [editor];
  for (const [i, r] of rows.entries()) {
    const pane = el("div", undefined, "pane-meta");
    pane.append(
      el("strong", r.path),
      el(
        "span",
        `${r.split} · LABEL: ${r.label} · ${r.original_characters.toLocaleString()} characters`,
        "label-badge",
      ),
    );
    const controls = el("div", undefined, "toolbar");
    const lang = select(health.languages, r.language);
    lang.setAttribute("aria-label", `Language pane ${i + 1}`);
    lang.onchange = () => {
      versions[i].language = lang.value;
      monaco.editor.setModelLanguage(models[i], lang.value);
    };
    const toggle = select(
      [
        ["original", "Original"],
        ["formatted", "Formatted"],
      ],
      "original",
    );
    toggle.setAttribute("aria-label", `Version pane ${i + 1}`);
    toggle.onchange = () => {
      const v = versions[i];
      if (toggle.value === "formatted" && !v.formatted) {
        toggle.value = "original";
        return;
      }
      v.show = toggle.value;
      models[i].setValue(v[v.show]);
    };
    const status = el("span", undefined, "format-status");
    controls.append(
      lang,
      button("Copy original", async () => {
        const original = r.partial
          ? (await api(`${base()}/rows/${r.row_id}?full=true`)).content
          : versions[i].original;
        await navigator.clipboard.writeText(original);
      }),
      button("Format", async () => {
        try {
          if (r.partial)
            throw Error(
              "Open full original before formatting; this pane is partial.",
            );
          if (r.content.length > 2_000_000)
            throw Error("Formatting limit: 2 million characters.");
          const v = versions[i];
          if (v.language === "python")
            v.formatted = (
              await post("/format/python", { content: v.original })
            ).content;
          else if (v.language === "javascript")
            v.formatted = await format(v.original, {
              parser: "babel",
              plugins: [babel, estree],
              printWidth: 88,
            });
          else
            throw Error(
              "Format supports Python and JavaScript. Choose a supported language.",
            );
          models[i].setValue(v.formatted);
          v.show = toggle.value = "formatted";
          status.textContent = "Formatted copy";
        } catch (e) {
          status.textContent = "Format failed: " + e.message;
          status.className = "format-status error";
          toggle.value = "original";
          versions[i].show = "original";
          models[i].setValue(versions[i].original);
        }
      }),
      toggle,
    );
    const download = el("a", "Download original");
    download.href = "/api" + base() + `/rows/${r.row_id}/download`;
    download.download = "";
    controls.append(download);
    pane.append(controls, status);
    info.append(pane);
  }
  evidence.append(
    el(
      "p",
      similarity == null
        ? "Original source · use Ctrl/Cmd+F to search within an editor."
        : `Cosine similarity: ${similarity.toFixed(6)} · original embedding space · Ctrl/Cmd+F to search`,
      "muted",
    ),
  );
  if (rows.some((r) => r.partial)) {
    const warn = el("div", undefined, "warning");
    warn.append(
      el("strong", "Partial view — this is not a complete comparison."),
      el(
        "p",
        rows
          .map(
            (r) =>
              `${r.path}: ${r.content.length} / ${r.original_characters} characters shown`,
          )
          .join(" · "),
      ),
      button("Open full original(s)", () =>
        openViewer(a, b, similarity, workflow, true),
      ),
    );
    evidence.append(warn);
  }
  if (b != null) {
    const reviewBox = section(
      "Review this pair",
      "Leakage and label decisions are saved independently. Pair order does not affect the saved review.",
    );
    const workflowSelect = select(
        [
          ["leakage", "Leakage review"],
          ["label", "Label review"],
        ],
        workflow,
      ),
      decision = select([]),
      notes = el("textarea");
    notes.placeholder = "Review notes";
    notes.setAttribute("aria-label", "Review notes");
    const toolbar = el("div", undefined, "toolbar");
    const saved = el("span");
    async function load() {
      const w = workflowSelect.value;
      const choices =
        w === "label"
          ? ["unreviewed", "suspected mislabel", "labels justified", "unsure"]
          : [
              "unreviewed",
              "confirmed leakage",
              "related but acceptable",
              "distinct",
              "unsure",
            ];
      decision.replaceChildren(...choices.map((x) => new Option(x, x)));
      const r = await api(`${base()}/reviews?a=${a}&b=${b}&workflow=${w}`);
      decision.value = r.decision;
      notes.value = r.notes;
      saved.textContent = "";
    }
    workflowSelect.onchange = () => guard(load);
    toolbar.append(
      workflowSelect,
      decision,
      button(
        "Save decision",
        async () => {
          await api(base() + "/reviews", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              a,
              b,
              workflow: workflowSelect.value,
              decision: decision.value,
              notes: notes.value,
            }),
          });
          saved.textContent = "Saved to this snapshot";
        },
        "primary",
      ),
      saved,
    );
    reviewBox.append(toolbar, notes);
    bottom.append(reviewBox);
    await load();
  }
  const neighbours = section(
    "Nearest neighbours",
    "Scores use the original vectors; the source row is excluded. Opposite labels are marked explicitly.",
  );
  bottom.append(neighbours);
  neighbours.append(
    button("Show top 10 neighbours", async () => {
      const r = await api(`${base()}/rows/${a}/neighbours`);
      neighbours.append(
        el("p", r.mode || r.state),
        table(
          [
            ["path", "Path"],
            ["label", "Label"],
            ["split", "Split"],
            ["similarity", "Similarity"],
            [
              (x) => (x.opposite_label ? "⚑ Opposite label" : "Same label"),
              "Label relationship",
            ],
          ],
          r.items,
          (x) =>
            openViewer(
              a,
              x.row_id,
              x.similarity,
              x.opposite_label ? "label" : "leakage",
            ),
        ),
      );
    }),
  );
}

guard(async () => {
  await reloadSnapshots();
  if (state.sid) await selectSnapshot(state.sid);
  else await navigate("overview");
});
