/* Discussion view builder. Reads one discussion state JSON and pivots claims.
   Views select and arrange; every label and quote comes from the record. */
(function () {
  "use strict";

  var SEP = "|||";

  var DIMENSIONS = [
    { key: "target", label: "option" },
    { key: "polarity", label: "polarity" },
    { key: "participant", label: "participant" },
    { key: "status", label: "status" },
    { key: "week", label: "week" },
    { key: "none", label: "none" }
  ];
  var MEASURES = [
    { key: "participants", label: "distinct participants" },
    { key: "claims", label: "claims" }
  ];
  var VIEWS = [
    { key: "matrix", label: "matrix" },
    { key: "bars", label: "bars" },
    { key: "positions", label: "positions" },
    { key: "questions", label: "open questions" }
  ];
  var DEFAULT = {
    view: "matrix",
    rows: "target",
    cols: "polarity",
    measure: "participants",
    filters: {}
  };

  var state = null;
  var spec = JSON.parse(JSON.stringify(DEFAULT));
  var kindByTarget = {};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function labelFor(key) {
    for (var i = 0; i < DIMENSIONS.length; i += 1) {
      if (DIMENSIONS[i].key === key) return DIMENSIONS[i].label;
    }
    return key;
  }
  function isoWeek(date) {
    var d = new Date(date);
    if (isNaN(d.getTime())) return "unknown";
    var t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var day = t.getUTCDay() || 7;
    t.setUTCDate(t.getUTCDate() + 4 - day);
    var start = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
    var week = Math.ceil((((t - start) / 86400000) + 1) / 7);
    return t.getUTCFullYear() + "-W" + (week < 10 ? "0" : "") + week;
  }
  function visible(claims) {
    return (claims || []).filter(function (c) { return !c.hidden; });
  }
  function dimValue(claim, key) {
    if (key === "none") return "all";
    if (key === "week") return isoWeek(claim.date);
    var v = claim[key];
    return (v === undefined || v === "") ? "unknown" : String(v);
  }

  /* spec lives in the URL hash so a view can be pasted into a thread */
  function readHash() {
    var raw = window.location.hash.replace(/^#/, "");
    if (!raw) return;
    try {
      var parsed = JSON.parse(decodeURIComponent(raw));
      ["view", "rows", "cols", "measure"].forEach(function (k) {
        if (parsed[k]) spec[k] = parsed[k];
      });
      if (parsed.filters && typeof parsed.filters === "object") spec.filters = parsed.filters;
    } catch (e) {
      /* a malformed hash just means the default view */
    }
  }
  function writeHash() {
    var encoded = encodeURIComponent(JSON.stringify(spec));
    if (("#" + encoded) !== window.location.hash) {
      history.replaceState(null, "", "#" + encoded);
    }
  }

  function passes(claim) {
    var f = spec.filters;
    for (var key in f) {
      if (!Object.prototype.hasOwnProperty.call(f, key)) continue;
      var want = f[key];
      if (!want) continue;
      if (key === "kind") {
        if ((kindByTarget[claim.target] || "option") !== want) return false;
      } else if (dimValue(claim, key) !== want) {
        return false;
      }
    }
    return true;
  }
  function filtered() {
    return visible(state.claims).filter(passes);
  }

  function measure(claims) {
    if (spec.measure === "claims") return claims.length;
    var seen = {};
    claims.forEach(function (c) { seen[c.participant] = true; });
    return Object.keys(seen).length;
  }

  function chip(label, active, onClick) {
    var b = el("button", "chip" + (active ? " on" : ""), label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function renderChips() {
    var views = document.getElementById("d-view-chips");
    views.innerHTML = "";
    VIEWS.forEach(function (v) {
      views.appendChild(chip(v.label, spec.view === v.key, function () {
        spec.view = v.key;
        render();
      }));
    });

    ["rows", "cols"].forEach(function (axis) {
      var host = document.getElementById("d-" + axis + "-chips");
      host.innerHTML = "";
      DIMENSIONS.forEach(function (d) {
        host.appendChild(chip(d.label, spec[axis] === d.key, function () {
          spec[axis] = d.key;
          render();
        }));
      });
    });

    var mhost = document.getElementById("d-measure-chips");
    mhost.innerHTML = "";
    MEASURES.forEach(function (m) {
      mhost.appendChild(chip(m.label, spec.measure === m.key, function () {
        spec.measure = m.key;
        render();
      }));
    });

    var fhost = document.getElementById("d-filter-chips");
    fhost.innerHTML = "";
    var active = Object.keys(spec.filters).filter(function (k) { return spec.filters[k]; });
    if (!active.length) {
      fhost.appendChild(el("span", "muted", "none, showing every claim"));
    } else {
      active.forEach(function (k) {
        fhost.appendChild(chip(k + ": " + spec.filters[k] + " x", true, function () {
          delete spec.filters[k];
          render();
        }));
      });
      fhost.appendChild(chip("clear all", false, function () {
        spec.filters = {};
        render();
      }));
    }

    var pivotOnly = spec.view === "matrix" || spec.view === "bars";
    Array.prototype.forEach.call(document.querySelectorAll("[data-pivot-only]"), function (n) {
      n.hidden = !pivotOnly;
    });
  }

  /* every view states its own filters and as-of date */
  function renderCaption(shown, total) {
    var parts = [];
    if (spec.view === "positions") {
      parts.push("Stated and inferred positions");
    } else if (spec.view === "questions") {
      parts.push("Questions raised in the thread");
    } else {
      var m = spec.measure === "claims" ? "claims" : "distinct participants";
      parts.push("Counting " + m + ", " + labelFor(spec.rows) + " by " + labelFor(spec.cols));
    }
    var f = Object.keys(spec.filters).filter(function (k) { return spec.filters[k]; });
    parts.push(f.length
      ? "filtered to " + f.map(function (k) { return k + " " + spec.filters[k]; }).join(" and ")
      : "no filters");
    parts.push(shown + " of " + total + " claims in view");
    var asof = (state.discussion && state.discussion.updated_at) || "";
    if (asof) parts.push("as of " + asof.slice(0, 10));
    document.getElementById("d-caption").textContent = parts.join(" - ") + ".";
  }

  function pivot(claims) {
    var rows = {}, cols = {}, cells = {};
    claims.forEach(function (c) {
      var r = dimValue(c, spec.rows);
      var k = dimValue(c, spec.cols);
      rows[r] = true;
      cols[k] = true;
      var id = r + SEP + k;
      if (!cells[id]) cells[id] = [];
      cells[id].push(c);
    });
    return { rows: Object.keys(rows).sort(), cols: Object.keys(cols).sort(), cells: cells };
  }
  function cellClaims(p, r, k) {
    return p.cells[r + SEP + k] || [];
  }

  function renderMatrix(claims) {
    var out = document.getElementById("d-output");
    out.innerHTML = "";
    if (!claims.length) {
      out.appendChild(el("p", "muted", "No claims match this view."));
      return;
    }
    var p = pivot(claims);
    var max = 0;
    p.rows.forEach(function (r) {
      p.cols.forEach(function (k) {
        max = Math.max(max, measure(cellClaims(p, r, k)));
      });
    });
    var table = el("table", "d-matrix");
    var thead = el("thead");
    var hr = el("tr");
    hr.appendChild(el("th", "d-corner", ""));
    p.cols.forEach(function (k) { hr.appendChild(el("th", null, k)); });
    hr.appendChild(el("th", "d-total", "all"));
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    p.rows.forEach(function (r) {
      var tr = el("tr");
      var th = el("th", "d-rowhead", r);
      if (kindByTarget[r] === "mechanism") th.appendChild(el("span", "d-kind", "mechanism"));
      tr.appendChild(th);
      var rowClaims = [];
      p.cols.forEach(function (k) {
        var cc = cellClaims(p, r, k);
        rowClaims = rowClaims.concat(cc);
        var v = measure(cc);
        var td = el("td", "d-cell" + (v ? "" : " empty"));
        if (v) {
          td.style.background = "rgba(233,161,59," + (0.08 + 0.42 * (v / (max || 1))) + ")";
          var b = el("button", "d-cellbtn", v);
          b.type = "button";
          b.addEventListener("click", function () { showDetail(r + " - " + k, cc); });
          td.appendChild(b);
        }
        tr.appendChild(td);
      });
      var total = el("td", "d-total");
      var tb = el("button", "d-cellbtn", measure(rowClaims));
      tb.type = "button";
      tb.addEventListener("click", function () { showDetail(r, rowClaims); });
      total.appendChild(tb);
      tr.appendChild(total);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var scroll = el("div", "d-scroll");
    scroll.appendChild(table);
    out.appendChild(scroll);
  }

  function renderBars(claims) {
    var out = document.getElementById("d-output");
    out.innerHTML = "";
    if (!claims.length) {
      out.appendChild(el("p", "muted", "No claims match this view."));
      return;
    }
    var p = pivot(claims);
    var totals = p.rows.map(function (r) {
      var all = [];
      p.cols.forEach(function (k) { all = all.concat(cellClaims(p, r, k)); });
      return { row: r, value: measure(all), claims: all };
    }).sort(function (a, b) { return b.value - a.value; });
    var max = totals.reduce(function (m, t) { return Math.max(m, t.value); }, 0) || 1;
    var list = el("div", "d-bars");
    totals.forEach(function (t) {
      var row = el("div", "d-bar");
      var name = el("span", "d-barname", t.row);
      if (kindByTarget[t.row] === "mechanism") name.appendChild(el("span", "d-kind", "mechanism"));
      row.appendChild(name);
      var track = el("span", "d-bartrack");
      var fill = el("span", "d-barfill");
      fill.style.width = Math.round(100 * t.value / max) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      var b = el("button", "d-cellbtn", t.value);
      b.type = "button";
      b.addEventListener("click", function () { showDetail(t.row, t.claims); });
      row.appendChild(b);
      list.appendChild(row);
    });
    out.appendChild(list);
  }

  function renderPositions() {
    var out = document.getElementById("d-output");
    out.innerHTML = "";
    var byParticipant = {};
    (state.positions || []).forEach(function (p) {
      if (!byParticipant[p.participant]) byParticipant[p.participant] = [];
      byParticipant[p.participant].push(p);
    });
    var names = Object.keys(byParticipant).sort();
    if (!names.length) {
      out.appendChild(el("p", "muted", "No positions recorded."));
      return;
    }
    var list = el("div", "d-positions");
    names.forEach(function (who) {
      var rows = byParticipant[who];
      var card = el("div", "d-poscard");
      var head = el("div", "d-poshead");
      head.appendChild(el("span", "d-who", who));
      if (rows.length > 1) {
        head.appendChild(el("span", "d-shift", rows.length + " positions over time"));
      }
      card.appendChild(head);
      rows.forEach(function (p) {
        var line = el("div", "d-posrow");
        line.appendChild(el("span", "d-basis " + (p.basis || ""), String(p.basis || "").replace("_", " ")));
        line.appendChild(el("span", "d-prefers", (p.prefers || []).join(", ") || "no preference recorded"));
        if (p.post_url) {
          var a = el("a", "d-src", "post");
          a.href = p.post_url;
          a.target = "_blank";
          a.rel = "noopener";
          line.appendChild(a);
        }
        if (p.supersedes) line.appendChild(el("span", "d-supersedes", "supersedes " + p.supersedes));
        card.appendChild(line);
      });
      list.appendChild(card);
    });
    out.appendChild(list);
  }

  function renderQuestions() {
    var out = document.getElementById("d-output");
    out.innerHTML = "";
    var questions = state.questions || [];
    if (!questions.length) {
      out.appendChild(el("p", "muted", "No questions recorded for this discussion."));
      return;
    }
    var list = el("div", "d-questions");
    questions.forEach(function (q) {
      var card = el("div", "d-qcard" + (q.status === "resolved" ? " resolved" : ""));
      card.appendChild(el("span", "d-qstatus", q.status || "open"));
      card.appendChild(el("p", "d-qtext", q.text));
      var meta = el("p", "d-qmeta");
      meta.appendChild(el("span", null, "raised by " + (q.raised_by || "unknown")));
      if (q.resolved_by) meta.appendChild(el("span", null, " - resolved by " + q.resolved_by));
      card.appendChild(meta);
      list.appendChild(card);
    });
    out.appendChild(list);
  }

  /* claim detail: the record itself, with quote and link */
  function showDetail(title, claims) {
    var host = document.getElementById("d-detail");
    host.innerHTML = "";
    host.hidden = false;
    var head = el("div", "d-detailhead");
    head.appendChild(el("span", "feed-label", title));
    head.appendChild(el("span", "muted", claims.length + " claims"));
    var close = el("button", "chip", "close");
    close.type = "button";
    close.addEventListener("click", function () { host.hidden = true; });
    head.appendChild(close);
    host.appendChild(head);

    claims.slice().sort(function (a, b) {
      return String(a.date).localeCompare(String(b.date));
    }).forEach(function (c) {
      var card = el("div", "d-claim");
      var top = el("div", "d-claimtop");
      top.appendChild(el("span", "d-pol " + c.polarity, c.polarity));
      top.appendChild(el("span", "d-who", c.participant));
      top.appendChild(el("span", "d-target", c.target));
      if (c.status && c.status !== "open") {
        top.appendChild(el("span", "d-status " + c.status, c.status));
      }
      card.appendChild(top);
      if (c.text) card.appendChild(el("p", "d-claimtext", c.text));
      card.appendChild(el("blockquote", "d-quote", c.quote));
      var foot = el("p", "d-claimfoot");
      var a = el("a", null, "source post");
      a.href = c.post_url;
      a.target = "_blank";
      a.rel = "noopener";
      foot.appendChild(a);
      if (c.date) foot.appendChild(el("span", "muted", " - " + String(c.date).slice(0, 10)));
      if (c.answered_by) foot.appendChild(el("span", "d-answered", " - answered by " + c.answered_by));
      if (c.confidence !== undefined) {
        foot.appendChild(el("span", "muted", " - confidence " + c.confidence));
      }
      card.appendChild(foot);
      host.appendChild(card);
    });
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function render() {
    writeHash();
    renderChips();
    var claims = filtered();
    var total = visible(state.claims).length;
    renderCaption(claims.length, total);
    document.getElementById("d-detail").hidden = true;
    if (spec.view === "positions") return renderPositions();
    if (spec.view === "questions") return renderQuestions();
    if (spec.view === "bars") return renderBars(claims);
    return renderMatrix(claims);
  }

  function boot(data) {
    state = data;
    (state.options || []).forEach(function (o) { kindByTarget[o.name] = o.kind; });
    var d = state.discussion || {};
    document.getElementById("d-title").textContent = d.title || "Discussion";
    var src = document.getElementById("d-source");
    src.innerHTML = "";
    var counts = visible(state.claims).length + " claims - " +
      (state.positions || []).length + " positions - " +
      (state.questions || []).length + " questions";
    src.appendChild(el("b", null, counts));
    (d.sources || []).forEach(function (s) {
      src.appendChild(el("span", "muted", " - "));
      var a = el("a", "d-src", String(s.url).replace(/^https?:\/\//, ""));
      a.href = s.url;
      a.target = "_blank";
      a.rel = "noopener";
      src.appendChild(a);
    });
    if (state._prototype_note) {
      src.appendChild(el("span", "d-note", state._prototype_note));
    }
    readHash();
    render();
  }

  function slug() {
    var m = window.location.pathname.match(/\/discussions\/([^/]+)/);
    return (m && m[1]) ? m[1] : "delving-2749";
  }

  fetch("/discussions/" + slug() + ".json")
    .then(function (r) {
      if (!r.ok) throw new Error("no discussion data");
      return r.json();
    })
    .then(boot)
    .catch(function () {
      document.getElementById("d-output").innerHTML =
        "<p class='muted'>No discussion data found.</p>";
    });
})();
