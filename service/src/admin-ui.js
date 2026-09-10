export const ADMIN_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Source Watch admin</title>
  <style>
    :root { color-scheme: dark; --bg:#111; --fg:#eee; --muted:#9aa; --line:#333; --accent:#8cf; }
    body { margin: 0; font: 14px/1.45 ui-sans-serif, system-ui, sans-serif; background: var(--bg); color: var(--fg); }
    header { display: flex; justify-content: space-between; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--line); }
    main { padding: 16px 20px 48px; max-width: 1100px; margin: 0 auto; }
    h1 { font-size: 18px; margin: 0; }
    h2 { font-size: 15px; margin: 0 0 10px; }
    section { border: 1px solid var(--line); border-radius: 8px; padding: 14px; margin: 14px 0; }
    .muted { color: var(--muted); }
    input, select, textarea, button { font: inherit; }
    input, select, textarea { background: #1c1c1c; color: var(--fg); border: 1px solid var(--line); border-radius: 4px; padding: 4px 8px; }
    button { background: #242424; color: var(--fg); border: 1px solid var(--line); border-radius: 4px; padding: 4px 10px; cursor: pointer; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    button.primary { background: #1c3a55; border-color: #2a5; color: var(--accent); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
    .row-actions { display: flex; flex-wrap: wrap; gap: 4px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; align-items: center; }
    .stats { display: flex; gap: 16px; flex-wrap: wrap; }
    .hidden-row { opacity: 0.7; }
    #login { max-width: 360px; margin: 80px auto; }
    #login input { width: 100%; margin: 8px 0; }
    .edit { display: none; margin-top: 8px; }
    .edit.open { display: grid; gap: 6px; }
    textarea { width: 100%; min-height: 72px; }
    .err { color: #f88; }
    a { color: var(--accent); }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 0; }
    .tabs button.on { border-color: var(--accent); color: var(--accent); }
    .panel { display: none; }
    .panel.on { display: block; }
    .why { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .count { margin-left: auto; color: var(--muted); font-size: 12px; }
  </style>
</head>
<body>
  <div id="login">
    <h1>Admin</h1>
    <p class="muted">Token is stored in sessionStorage and sent as a Bearer header. This page contains no data.</p>
    <input id="token" type="password" placeholder="ADMIN_TOKEN" autocomplete="off">
    <button class="primary" id="login-btn">Open console</button>
    <p class="err" id="login-err"></p>
  </div>
  <div id="app" hidden>
    <header>
      <h1>Source Watch admin</h1>
      <button id="logout">Sign out</button>
    </header>
    <main>
      <section id="status-panel">
        <h2>Status</h2>
        <div class="stats" id="status-stats"></div>
        <p class="toolbar"><button class="primary" id="refresh-btn">Refresh now</button><span class="muted" id="refresh-note"></span></p>
      </section>
      <div class="toolbar">
        <input id="catalog-q" placeholder="search title / name / url">
        <select id="catalog-vis">
          <option value="visible">visible</option>
          <option value="hidden">hidden</option>
          <option value="excluded">excluded</option>
          <option value="all">all</option>
        </select>
        <button id="catalog-search">Search</button>
        <button id="catalog-prev">Prev</button>
        <span class="count" id="catalog-count"></span>
        <button id="catalog-next">Next</button>
      </div>

      <div class="tabs" id="tabs">
        <button data-tab="items" class="on">Items</button>
        <button data-tab="projects">Projects</button>
        <button data-tab="sources">Sources</button>
        <button data-tab="rules">Rules</button>
        <button data-tab="audit">Audit</button>
      </div>
      <section class="panel on" id="panel-items">
        <h2>Items</h2>
        <div id="items-table"></div>
      </section>
      <section class="panel" id="panel-projects">
        <h2>Projects</h2>
        <div id="projects-table"></div>
      </section>
      <section class="panel" id="panel-sources">
        <h2>Sources</h2>
        <div id="sources-table"></div>
      </section>
      <section class="panel" id="panel-rules">
        <h2>Exclusions</h2>
        <form class="toolbar" id="excl-form">
          <select name="kind">
            <option>term</option><option>url_prefix</option><option>repo</option><option>project</option><option>source_type</option>
          </select>
          <input name="value" placeholder="value" required>
          <input name="note" placeholder="note">
          <button type="submit">Add</button>
        </form>
        <div id="excl-table"></div>
        <h2>Include terms</h2>
        <form class="toolbar" id="term-form">
          <select name="bucket">
            <option>required_any</option><option>always_match</option><option>context_any</option>
          </select>
          <input name="term" placeholder="term" required>
          <input name="note" placeholder="note">
          <button type="submit">Add</button>
        </form>
        <div id="term-table"></div>
        <h2>Seed additions</h2>
        <form id="seed-form">
          <div class="toolbar">
            <select name="kind">
              <option>github_repositories</option>
              <option>github_pull_requests</option>
              <option>docs_pages</option>
              <option>crates</option>
            </select>
            <button type="submit">Add</button>
          </div>
          <textarea name="entry" placeholder='{"id":"acme-lib","repo":"acme/lib","project":"Acme"}'></textarea>
        </form>
        <div id="seed-table"></div>
        <h2>Settings</h2>
        <form class="toolbar" id="settings-form">
          <label>discovered_after <input name="discovered_after" type="date"></label>
          <button type="submit">Save</button>
        </form>
      </section>
      <section class="panel" id="panel-audit">
        <h2>Audit</h2>
        <div id="audit-table"></div>
      </section>
    </main>
  </div>
  <script>
  (function () {
    var TOKEN_KEY = "sw_admin_token";
    var token = sessionStorage.getItem(TOKEN_KEY) || "";
    var tab = "items";
    var PAGE = 100;
    var catalogOffset = 0;


    function $(id) { return document.getElementById(id); }

    function api(path, opts) {
      opts = opts || {};
      return fetch(path, {
        method: opts.method || "GET",
        headers: Object.assign({
          "Authorization": "Bearer " + token,
          "Accept": "application/json"
        }, opts.body ? { "Content-Type": "application/json" } : {}),
        body: opts.body ? JSON.stringify(opts.body) : undefined
      }).then(function (res) {
        if (res.status === 401) {
          sessionStorage.removeItem(TOKEN_KEY);
          token = "";
          $("app").hidden = true;
          $("login").hidden = false;
          $("login-err").textContent = "Unauthorized. Enter a valid token.";
          throw new Error("401");
        }
        return res.json().catch(function () { return {}; }).then(function (data) {
          data._status = res.status;
          return data;
        });
      });
    }

    function esc(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }


    function pad2(n) { return String(n).padStart(2, "0"); }
    function toLocal(iso) {
      if (!iso) return "";
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
        "T" + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }
    function fromLocal(v) {
      if (!v) return null;
      var m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/);
      if (!m) return null;
      var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6] || 0));
      if (isNaN(d.getTime())) return null;
      return d.toISOString().replace(/\.\d{3}Z$/, "Z");
    }


    function repoFromUrl(url) {
      try {
        var u = new URL(String(url || ""));
        var host = String(u.hostname || "").toLowerCase();
        if (host !== "github.com" && host !== "www.github.com") return null;
        var path = String(u.pathname || "");
        while (path.charAt(0) === "/") path = path.slice(1);
        var parts = path.split("/");
        if (!parts[0] || !parts[1]) return null;
        var repo = parts[1];
        if (repo.slice(-4).toLowerCase() === ".git") repo = repo.slice(0, -4);
        return parts[0] + "/" + repo;
      } catch (e) {
        return null;
      }
    }


    function catalogQs() {
      var q = $("catalog-q").value;
      var vis = $("catalog-vis").value;
      var qs = "?limit=" + PAGE + "&offset=" + catalogOffset + "&visibility=" + encodeURIComponent(vis);
      if (q) qs += "&q=" + encodeURIComponent(q);
      return qs;
    }

    function paintPager(total, noun) {
      total = Number(total) || 0;
      var start = total ? catalogOffset + 1 : 0;
      var end = Math.min(catalogOffset + PAGE, total);
      $("catalog-count").textContent = start ? (start + "–" + end + " of " + total + " " + noun) : ("0 " + noun);
      $("catalog-prev").disabled = catalogOffset <= 0;
      $("catalog-next").disabled = catalogOffset + PAGE >= total;
    }

    function setTab(name) {
      tab = name;
      catalogOffset = 0;
      document.querySelectorAll(".tabs button").forEach(function (b) {
        b.classList.toggle("on", b.getAttribute("data-tab") === name);
      });
      document.querySelectorAll(".panel").forEach(function (p) {
        p.classList.toggle("on", p.id === "panel-" + name);
      });
      loadTab();
    }


    function showApp() {
      $("login").hidden = true;
      $("app").hidden = false;
      loadState();
      loadTab();
    }

    function loadTab() {
      if (tab === "items") return loadItems();
      if (tab === "projects") return loadProjects();
      if (tab === "sources") return loadSources();
      if (tab === "audit") return loadAudit();
      $("catalog-prev").disabled = true;
      $("catalog-next").disabled = true;
      $("catalog-count").textContent = "";
      if (tab === "rules") {
        loadExclusions();
        loadTerms();
        loadSeeds();
        loadSettings();
      }

    }

    function loadState() {
      return api("/api/admin/state").then(function (s) {
        $("status-stats").innerHTML =
          "<div>raw <b>" + esc(s.raw) + "</b></div>" +
          "<div>visible <b>" + esc(s.visible) + "</b></div>" +
          "<div>hidden <b>" + esc(s.hidden) + "</b></div>" +
          "<div>last ingest <b>" + esc(s.last_ingest_at || "—") + "</b></div>" +
          "<div>generated_at <b>" + esc(s.generated_at || "—") + "</b></div>";
        var btn = $("refresh-btn");
        if (s.refresh_configured) {
          btn.disabled = false;
          btn.textContent = "Refresh now";
        } else {
          btn.disabled = true;
          btn.textContent = "refresh not configured";
        }
      });
    }

    function linkCell(label, url, extra) {
      var title = url
        ? "<a href=\\"" + esc(url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">" + esc(label || url) + "</a>"
        : "<b>" + esc(label || "") + "</b>";
      return title + (extra ? "<div class='why'>" + extra + "</div>" : "");
    }

    function loadItems() {
      return api("/api/admin/items" + catalogQs()).then(function (data) {
        paintPager(data.total, "items");

        var rows = (data.items || []).map(function (item) {
          var url = item.source_url || "";
          var dim = item.suppressed ? "hidden-row" : "";
          var displayTitle = (item.patch && item.patch.title) || item.title;
          var titleVal = displayTitle || "";
          var summaryVal = (item.patch && item.patch.summary) || item.summary || "";
          var discVal = toLocal(item.patch && item.patch.discovered_at || item.discovered_at);
          var actVal = toLocal(item.patch && item.patch.activity_at || item.activity_at);
          return "<tr class='" + dim + "'>" +
            "<td>" + linkCell(displayTitle, url, esc(item.why || "") + (item.id ? " · " + esc(item.id) : "")) +
            "<div class='edit' data-edit='" + esc(item.id) + "'>" +
              "<input data-f='title' data-orig='" + esc(titleVal) + "' value='" + esc(titleVal) + "' placeholder='title'>" +
              "<input data-f='summary' data-orig='" + esc(summaryVal) + "' value='" + esc(summaryVal) + "' placeholder='summary'>" +
              "<input data-f='discovered_at' type='datetime-local' data-orig='" + esc(discVal) + "' value='" + esc(discVal) + "'>" +
              "<input data-f='activity_at' type='datetime-local' data-orig='" + esc(actVal) + "' value='" + esc(actVal) + "'>" +
              "<button data-act='save-edit' data-id='" + esc(item.id) + "'>Save</button>" +
            "</div></td>" +
            "<td>" + esc(item.project) + "<div class='muted'>" + esc(item.source_type) + "</div></td>" +
            "<td>" + esc(item.patch && item.patch.status || item.status || "") + "</td>" +
            "<td>" + esc((item.patch && item.patch.discovered_at) || item.discovered_at || "") + "<div class='muted'>" + esc((item.patch && item.patch.activity_at) || item.activity_at || "") + "</div></td>" +

            "<td class='row-actions'>" +
              "<button data-act='hide' data-id='" + esc(item.id) + "' data-hidden='" + (item.hidden ? "0" : "1") + "'>" + (item.hidden ? "Unhide" : "Hide") + "</button>" +
              "<button data-act='promote' data-id='" + esc(item.id) + "'>Promote</button>" +
              "<button data-act='exclude' data-id='" + esc(item.id) + "' data-url='" + esc(url) + "' data-title='" + esc(displayTitle || "") + "'>Exclude</button>" +

              "<button data-act='edit' data-id='" + esc(item.id) + "'>Edit</button>" +
              "<button data-act='clear' data-id='" + esc(item.id) + "'>Clear override</button>" +
            "</td></tr>";
        }).join("");
        $("items-table").innerHTML = "<table><thead><tr><th>Title</th><th>Project</th><th>Status</th><th>Dates</th><th></th></tr></thead><tbody>" + (rows || "<tr><td colspan='5' class='muted'>No items</td></tr>") + "</tbody></table>";
      });
    }

    function loadProjects() {
      return api("/api/admin/projects" + catalogQs()).then(function (data) {
        paintPager(data.total, "projects");

        var rows = (data.projects || []).map(function (p) {
          var displayName = (p.patch && p.patch.title) || p.name;
          return "<tr class='" + (p.suppressed ? "hidden-row" : "") + "'><td>" +
            "<b>" + esc(displayName) + "</b><div class='why'>" + esc(p.why || p.id) + "</div></td>" +
            "<td class='row-actions'>" +
            "<button data-kind='project' data-act='hide-kind' data-id='" + esc(p.id) + "' data-hidden='" + (p.hidden ? "0" : "1") + "'>" + (p.hidden ? "Unhide" : "Hide") + "</button>" +
            "<button data-act='rename' data-id='" + esc(p.id) + "' data-name='" + esc(displayName) + "'>Rename</button>" +
            "</td></tr>";
        }).join("");
        $("projects-table").innerHTML = "<table><tbody>" + (rows || "<tr><td class='muted'>No projects</td></tr>") + "</tbody></table>";
      });
    }

    function loadSources() {
      return api("/api/admin/sources" + catalogQs()).then(function (data) {
        paintPager(data.total, "sources");

        var rows = (data.sources || []).map(function (s) {
          return "<tr class='" + (s.suppressed ? "hidden-row" : "") + "'><td>" +
            linkCell(s.name, s.url, esc(s.why || s.id)) + "</td>" +
            "<td class='row-actions'><button data-kind='source' data-act='hide-kind' data-id='" + esc(s.id) + "' data-hidden='" + (s.hidden ? "0" : "1") + "'>" + (s.hidden ? "Unhide" : "Hide") + "</button></td></tr>";
        }).join("");
        $("sources-table").innerHTML = "<table><tbody>" + (rows || "<tr><td class='muted'>No sources</td></tr>") + "</tbody></table>";
      });
    }

    function loadExclusions() {
      return api("/api/admin/exclusions").then(function (data) {
        var rows = (data.exclusions || []).map(function (r) {
          return "<tr><td>" + esc(r.kind) + "</td><td>" + esc(r.value) + "</td><td class='muted'>" + esc(r.note || "") + "</td>" +
            "<td><button data-act='del-excl' data-id='" + esc(r.id) + "'>Delete</button></td></tr>";
        }).join("");
        $("excl-table").innerHTML = "<table><tbody>" + (rows || "<tr><td class='muted'>None</td></tr>") + "</tbody></table>";
      });
    }

    function loadTerms() {
      return api("/api/admin/include-terms").then(function (data) {
        var rows = (data.include_terms || []).map(function (r) {
          return "<tr><td>" + esc(r.bucket) + "</td><td>" + esc(r.term) + "</td><td class='muted'>" + esc(r.note || "") + "</td>" +
            "<td><button data-act='del-term' data-id='" + esc(r.id) + "'>Delete</button></td></tr>";
        }).join("");
        $("term-table").innerHTML = "<table><tbody>" + (rows || "<tr><td class='muted'>None</td></tr>") + "</tbody></table>";
      });
    }

    function loadSeeds() {
      return api("/api/admin/seed-additions").then(function (data) {
        var rows = (data.seed_additions || []).map(function (r) {
          var url = r.entry && (r.entry.url || r.entry.repo) || "";
          return "<tr><td>" + esc(r.kind) + "</td><td>" + (url ? "<a href=\\"" + esc(url.indexOf("http") === 0 ? url : "https://github.com/" + url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">" + esc(url) + "</a>" : "") +
            "<div class='why'><code>" + esc(JSON.stringify(r.entry)) + "</code></div></td>" +
            "<td><button data-act='del-seed' data-id='" + esc(r.id) + "'>Delete</button></td></tr>";
        }).join("");
        $("seed-table").innerHTML = "<table><tbody>" + (rows || "<tr><td class='muted'>None</td></tr>") + "</tbody></table>";
      });
    }

    function loadSettings() {
      return api("/api/admin/settings").then(function (s) {
        var v = s.discovered_after || "";
        $("settings-form").discovered_after.value = v ? String(v).slice(0, 10) : "";
      });
    }

    function loadAudit() {
      return api("/api/admin/audit" + catalogQs()).then(function (data) {
        paintPager(data.total, "events");
        var rows = (data.audit || []).map(function (r) {
          var target = r.target || "";
          var targetHtml = /^https?:\\/\\//.test(target)
            ? "<a href=\\"" + esc(target) + "\\" target=\\"_blank\\" rel=\\"noopener\\">" + esc(target) + "</a>"
            : esc(target);
          return "<tr><td>" + esc(r.at) + "</td><td>" + esc(r.action) + "</td><td>" + targetHtml + "</td><td class='muted'>" + esc(r.detail || "") + "</td></tr>";
        }).join("");
        $("audit-table").innerHTML = "<table><thead><tr><th>At</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>" + (rows || "<tr><td class='muted'>Empty</td></tr>") + "</tbody></table>";
      });
    }

    function afterMutation() {
      loadState();
      loadTab();
    }

    $("login-btn").onclick = function () {
      token = $("token").value.trim();
      if (!token) return;
      sessionStorage.setItem(TOKEN_KEY, token);
      api("/api/admin/state").then(showApp).catch(function () {});
    };
    $("logout").onclick = function () {
      sessionStorage.removeItem(TOKEN_KEY);
      token = "";
      $("app").hidden = true;
      $("login").hidden = false;
    };
    $("catalog-search").onclick = function () { catalogOffset = 0; loadTab(); };
    $("catalog-prev").onclick = function () {
      catalogOffset = Math.max(0, catalogOffset - PAGE);
      loadTab();
    };
    $("catalog-next").onclick = function () {
      catalogOffset += PAGE;
      loadTab();
    };

    $("refresh-btn").onclick = function () {
      api("/api/admin/refresh", { method: "POST" }).then(function () { afterMutation(); });
    };
    $("tabs").onclick = function (ev) {
      var btn = ev.target.closest("button[data-tab]");
      if (btn) setTab(btn.getAttribute("data-tab"));
    };

    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-act]");
      if (!btn) return;
      var act = btn.getAttribute("data-act");
      var id = btn.getAttribute("data-id");
      if (act === "hide") {
        var hidden = btn.getAttribute("data-hidden") === "1";
        api("/api/admin/overrides/item/" + encodeURIComponent(id), { method: "PUT", body: { hidden: hidden } }).then(afterMutation);
      } else if (act === "promote") {
        api("/api/admin/overrides/item/" + encodeURIComponent(id), { method: "PUT", body: { status: "seeded" } }).then(afterMutation);
      } else if (act === "exclude") {
        var url = btn.getAttribute("data-url") || "";
        var repo = repoFromUrl(url);
        var body = repo
          ? { kind: "repo", value: repo, note: "from admin" }
          : { kind: "url_prefix", value: url, note: "from admin" };
        if (!body.value) body = { kind: "term", value: btn.getAttribute("data-title") || id, note: "from admin" };
        api("/api/admin/exclusions", { method: "POST", body: body }).then(afterMutation);
      } else if (act === "edit") {
        var el = document.querySelector(".edit[data-edit=\\"" + id.replace(/"/g, "") + "\\"]");
        if (el) el.classList.toggle("open");
      } else if (act === "save-edit") {
        var box = document.querySelector(".edit[data-edit=\\"" + id.replace(/"/g, "") + "\\"]");
        if (!box) return;
        var patch = {};
        box.querySelectorAll("[data-f]").forEach(function (input) {
          var key = input.getAttribute("data-f");
          var orig = input.getAttribute("data-orig") || "";
          var val = input.value;
          if (val === orig) return;
          patch[key] = (key === "discovered_at" || key === "activity_at") ? fromLocal(val) : val;
        });
        if (!Object.keys(patch).length) return;

        api("/api/admin/overrides/item/" + encodeURIComponent(id), { method: "PUT", body: patch }).then(afterMutation);
      } else if (act === "clear") {
        api("/api/admin/overrides/item/" + encodeURIComponent(id), { method: "DELETE" }).then(afterMutation);
      } else if (act === "hide-kind") {
        var kind = btn.getAttribute("data-kind");
        var hide = btn.getAttribute("data-hidden") === "1";
        api("/api/admin/overrides/" + kind + "/" + encodeURIComponent(id), { method: "PUT", body: { hidden: hide } }).then(afterMutation);
      } else if (act === "rename") {
        var name = prompt("New project name", btn.getAttribute("data-name") || "");
        if (!name) return;
        api("/api/admin/overrides/project/" + encodeURIComponent(id), { method: "PUT", body: { title: name } }).then(afterMutation);
      } else if (act === "del-excl") {
        api("/api/admin/exclusions/" + id, { method: "DELETE" }).then(afterMutation);
      } else if (act === "del-term") {
        api("/api/admin/include-terms/" + id, { method: "DELETE" }).then(afterMutation);
      } else if (act === "del-seed") {
        api("/api/admin/seed-additions/" + id, { method: "DELETE" }).then(afterMutation);
      }
    });

    $("excl-form").onsubmit = function (ev) {
      ev.preventDefault();
      api("/api/admin/exclusions", { method: "POST", body: { kind: this.kind.value, value: this.value.value, note: this.note.value } }).then(function () {
        $("excl-form").reset();
        afterMutation();
      });
    };
    $("term-form").onsubmit = function (ev) {
      ev.preventDefault();
      api("/api/admin/include-terms", { method: "POST", body: { bucket: this.bucket.value, term: this.term.value, note: this.note.value } }).then(function () {
        $("term-form").reset();
        afterMutation();
      });
    };
    $("seed-form").onsubmit = function (ev) {
      ev.preventDefault();
      var entry;
      try { entry = JSON.parse(this.entry.value); } catch (err) { alert("entry must be JSON"); return; }
      api("/api/admin/seed-additions", { method: "POST", body: { kind: this.kind.value, entry: entry } }).then(function (res) {
        if (res._status === 400) { alert("invalid seed"); return; }
        $("seed-form").reset();
        afterMutation();
      });
    };
    $("settings-form").onsubmit = function (ev) {
      ev.preventDefault();
      var v = this.discovered_after.value;
      api("/api/admin/settings", { method: "PUT", body: { discovered_after: v ? v + "T00:00:00Z" : "" } }).then(afterMutation);
    };

    if (token) {
      api("/api/admin/state").then(showApp).catch(function () {});
    }
  })();
  </script>
</body>
</html>
`;
