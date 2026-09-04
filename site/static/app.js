(function () {
  "use strict";

  var TYPE_DOT = {
    docs_page: "docs",
    github_repository: "repo",
    github_pull_request: "pr",
    package_crate: "crate"
  };
  var TAG_ALIAS = {};
  var GENERATED_SUMMARY = /^seeded monitored source for /i;
  var GENERATED_QUERY = /^github repository matched .+ live collector query:/i;

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var HIDDEN_TAGS = {};
  var PREFERRED_CHIPS = ["docs", "spec"];
  var EXPLICIT_CHIPS = false;

  function applyWatch(raw) {
    raw = raw || {};
    var hidden = {};
    (raw.hidden_tags || []).forEach(function (t) {
      if (t) hidden[String(t)] = true;
    });
    if (raw.default_tag) hidden[String(raw.default_tag)] = true;
    HIDDEN_TAGS = hidden;
    var chips = raw.preferred_chips;
    EXPLICIT_CHIPS = !!(chips && chips.length);
    PREFERRED_CHIPS = EXPLICIT_CHIPS ? chips.slice() : ["docs", "spec"];
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function parseDate(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function humanDate(iso) {
    var d = parseDate(iso);
    if (!d) return "";
    var now = new Date();
    var diff = now.getTime() - d.getTime();
    var mins = Math.round(diff / 60000);
    var hours = Math.round(diff / 3600000);
    var days = Math.round(diff / 86400000);
    if (mins < 60 && mins >= 0) return (mins <= 1 ? "1m" : mins + "m") + " ago";
    if (hours < 24 && hours >= 0) return hours + "h ago";
    if (days < 7 && days >= 0) return days + "d ago";
    if (d.getFullYear() === now.getFullYear()) return MONTHS[d.getMonth()] + " " + d.getDate();
    return MONTHS[d.getMonth()] + " " + d.getFullYear();
  }

  function relativeFrom(iso) {
    var d = parseDate(iso);
    if (!d) return "";
    var diff = Date.now() - d.getTime();
    if (diff < 0) diff = 0;
    var mins = Math.round(diff / 60000);
    var hours = Math.round(diff / 3600000);
    var days = Math.round(diff / 86400000);
    if (mins < 60) return (mins <= 1 ? "1m" : mins + "m") + " ago";
    if (hours < 48) return hours + "h ago";
    return days + "d ago";
  }

  function formatStamp(iso) {
    var d = parseDate(iso);
    if (!d) return "";
    try {
      var parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true
      }).formatToParts(d);
      var get = function (t) {
        var p = parts.find(function (x) { return x.type === t; });
        return p ? p.value : "";
      };
      var dayPeriod = get("dayPeriod").toLowerCase().replace(/\./g, "");
      return get("month") + " " + get("day") + ", " + get("year") + " " + get("hour") + ":" + get("minute") + dayPeriod + " ET";
    } catch (e) {
      return d.toISOString();
    }
  }
  function formatET(iso) {
    var stamp = formatStamp(iso);
    return stamp ? "Generated " + stamp : "";
  }

  function isoWeekParts(d) {
    var date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var day = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() + 4 - day);
    var year = date.getUTCFullYear();
    var yearStart = new Date(Date.UTC(year, 0, 1));
    var week = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
    return { year: year, week: week };
  }

  function isoWeekStart(year, week) {
    var jan4 = new Date(Date.UTC(year, 0, 4));
    var day = jan4.getUTCDay() || 7;
    var monday = new Date(jan4);
    monday.setUTCDate(jan4.getUTCDate() - (day - 1));
    monday.setUTCDate(monday.getUTCDate() + (week - 1) * 7);
    return monday;
  }

  function formatWeekRange(year, week) {
    var start = isoWeekStart(year, week);
    var end = new Date(start);
    end.setUTCDate(start.getUTCDate() + 6);
    if (start.getUTCMonth() === end.getUTCMonth()) {
      return MONTHS[start.getUTCMonth()] + " " + start.getUTCDate() + "–" + end.getUTCDate();
    }
    return MONTHS[start.getUTCMonth()] + " " + start.getUTCDate() + "–" + MONTHS[end.getUTCMonth()] + " " + end.getUTCDate();
  }

  function parseWeekSlug(slug) {
    var m = String(slug || "").match(/(\d{4})-W(\d{1,2})/i);
    if (!m) return null;
    return { year: parseInt(m[1], 10), week: parseInt(m[2], 10), slug: m[1] + "-W" + String(m[2]).padStart(2, "0") };
  }

  function isCandidate(item) {
    return item && (item.status === "candidate" || (item.tags || []).indexOf("candidate") !== -1);
  }

  function itemDate(item) {
    return item.activity_at || item.source_updated_at || item.source_published_at || item.discovered_at || item.event_time || item.observed_at;
  }

  function displayTitle(item) {
    var t = item.title || item.name || "";
    t = t.replace(/ PR #(\d+)/, " #$1");
    t = t.replace(/ developer page$/, "");
    return t;
  }

  function displaySummary(item) {
    var raw = (item.summary || "").trim();
    if (raw && !GENERATED_SUMMARY.test(raw) && !GENERATED_QUERY.test(raw)) {
      if (raw.length > 160) {
        var cut = raw.slice(0, 157);
        var sp = cut.lastIndexOf(" ");
        return (sp > 80 ? cut.slice(0, sp) : cut) + ".";
      }
      return raw.charAt(raw.length - 1) === "." ? raw : raw + ".";
    }
    var title = displayTitle(item);
    var kind = item.source_type;
    if (kind === "github_pull_request") return "Tracked pull request: " + title + ".";
    if (kind === "package_crate") return "Published crate: " + title + ".";
    if (kind === "docs_page") return "Public documentation: " + title + ".";
    if (kind === "github_repository") return "Public repository: " + title + ".";
    return title;
  }

  function topicTags(item, limit) {
    limit = limit || 3;
    var out = [];
    var seen = {};
    (item.tags || []).forEach(function (tag) {
      var key = TAG_ALIAS[tag] || tag;
      if (HIDDEN_TAGS[tag] || HIDDEN_TAGS[key] || seen[key]) return;
      seen[key] = true;
      out.push(key);
    });
    if (item.source_type === "github_pull_request") {
      var merged = (item.tags || []).indexOf("merged") !== -1;
      if (!merged && !seen.open) {
        out.push("open");
        seen.open = true;
      }
    }
    return out.slice(0, limit);
  }

  function sortActivity(items) {
    return items.slice().sort(function (a, b) {
      var da = itemDate(a) || "";
      var db = itemDate(b) || "";
      return db < da ? -1 : db > da ? 1 : 0;
    });
  }

  function itemWeek(item) {
    var d = parseDate(item.discovered_at || item.event_time || item.activity_at);
    return d ? isoWeekParts(d) : null;
  }

  function itemsForWeek(items, weekSlug) {
    var parsed = parseWeekSlug(weekSlug);
    if (!parsed) return [];
    return items.filter(function (item) {
      var p = itemWeek(item);
      return p && p.year === parsed.year && p.week === parsed.week;
    });
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function currentPage() {
    return document.body.getAttribute("data-page") || "";
  }

  function fillRefresh(generatedAt) {
    var el = document.getElementById("refreshed");
    if (!el || !generatedAt) return;
    el.textContent = relativeFrom(generatedAt);
    el.setAttribute("title", formatET(generatedAt));
  }

  function fillStartCounts(feed, projects, sources) {
    var nSources = (sources.sources || []).length || (feed.items || []).length;
    var nProjects = (projects.projects || []).length;
    document.querySelectorAll("[data-count=sources]").forEach(function (el) {
      el.textContent = nSources + " source" + (nSources === 1 ? "" : "s");
    });
    document.querySelectorAll("[data-count=projects]").forEach(function (el) {
      el.textContent = nProjects + " project" + (nProjects === 1 ? "" : "s");
    });
    var now = new Date();
    var cur = isoWeekParts(now);
    var weekNew = (feed.items || []).filter(function (item) {
      var d = parseDate(item.discovered_at || item.event_time);
      if (!d) return false;
      var p = isoWeekParts(d);
      if (p.year === cur.year && p.week === cur.week) return true;
      var days = (now.getTime() - d.getTime()) / 86400000;
      return days >= 0 && days <= 7;
    }).length;
    document.querySelectorAll("[data-count=week-new]").forEach(function (el) {
      el.textContent = weekNew + " new this week";
    });
    setText("stat-sources", String(nSources));
    setText("stat-projects", String(nProjects));
    setText("stat-new", String(weekNew));
  }

  function topicQuery() {
    try {
      return new URLSearchParams(window.location.search).get("topic") || "";
    } catch (e) {
      return "";
    }
  }

  function itemMatchesTopic(item, slug) {
    if (!slug) return true;
    var key = String(slug).toLowerCase();
    var tags = item.tags || [];
    for (var i = 0; i < tags.length; i++) {
      if (String(tags[i]).toLowerCase() === key) return true;
    }
    return String(item.project || "").toLowerCase() === key;
  }

  function fillTopicCounts(items) {
    document.querySelectorAll("[data-topic-count]").forEach(function (el) {
      var slug = el.getAttribute("data-topic-count") || "";
      var n = items.filter(function (item) { return itemMatchesTopic(item, slug); }).length;
      el.textContent = String(n);
    });
  }

  function renderFeed(items) {
    var root = document.getElementById("activity-feed");
    if (!root) return;
    var topic = topicQuery();
    var list = topic ? items.filter(function (item) { return itemMatchesTopic(item, topic); }) : items;
    var html = '<div class="feed-label">Latest activity</div>';
    if (!list.length) {
      root.innerHTML = html + '<p class="muted">No activity yet.</p>';
      return;
    }
    sortActivity(list).forEach(function (item) {
      var tags = topicTags(item, 3).map(function (t) {
        return '<span class="tag">' + esc(t) + "</span>";
      }).join("");
      html +=
        '<article class="item">' +
          '<div class="item-top">' +
            "<h3><a href=\"" + esc(item.source_url) + "\">" + esc(displayTitle(item)) + "</a></h3>" +
          "</div>" +
          "<p>" + esc(displaySummary(item)) + "</p>" +
          '<div class="item-meta"><span class="proj">' + esc(item.project || "") + '</span><span class="time" title="' + esc(formatStamp(itemDate(item))) + '">' + esc(humanDate(itemDate(item))) + "</span></div>" +
          (tags ? '<div class="tags">' + tags + "</div>" : "") +
        "</article>";
    });
    root.innerHTML = html;
  }

  function renderTeaser(items) {
    var root = document.getElementById("activity-teaser");
    if (!root) return;
    var head = root.querySelector(".feed-head");
    var list = sortActivity(items).slice(0, 2);
    var html = head ? head.outerHTML : "";
    if (!list.length) {
      root.innerHTML = html + '<p class="muted">No activity yet.</p>';
      return;
    }
    list.forEach(function (item) {
      html +=
        '<article class="item">' +
          "<h3><a href=\"" + esc(item.source_url) + "\">" + esc(displayTitle(item)) + "</a></h3>" +
          "<p>" + esc(displaySummary(item)) + "</p>" +
          '<div class="item-meta"><span class="proj">' + esc(item.project || "") + '</span><span class="time" title="' + esc(formatStamp(itemDate(item))) + '">' + esc(humanDate(itemDate(item))) + "</span></div>" +
        "</article>";
    });
    root.innerHTML = html;
  }

  function renderWeek(allItems, weekSlug) {
    var parsed = parseWeekSlug(weekSlug);
    if (parsed) {
      setText("week-range", formatWeekRange(parsed.year, parsed.week));
      document.querySelectorAll(".rail a[data-week]").forEach(function (a) {
        var slug = a.getAttribute("data-week");
        var p = parseWeekSlug(slug);
        if (!p) return;
        var n = itemsForWeek(allItems, slug).length;
        var sub = a.querySelector(".sub");
        if (sub) sub.textContent = formatWeekRange(p.year, p.week);
        var countEl = a.querySelector(".n");
        if (countEl) countEl.textContent = String(n);
      });
    }
    var items = itemsForWeek(allItems, weekSlug);
    var cands = sortActivity(items.filter(isCandidate));
    var prs = sortActivity(items.filter(function (i) { return i.source_type === "github_pull_request" && !isCandidate(i); }));
    var repos = sortActivity(items.filter(function (i) {
      return !isCandidate(i) && (i.source_type === "github_repository" || i.source_type === "package_crate");
    }));
    var docs = sortActivity(items.filter(function (i) { return i.source_type === "docs_page"; }));
    var n = items.length;
    var lede =
      n === 0
        ? "Nothing new this ISO week."
        : n + " item" + (n === 1 ? "" : "s") + " showed up.";
    setText("week-lede", lede);


    function rows(list) {
      return list.map(function (item) {
        return (
          '<div class="row">' +
            "<div>" +
              '<a class="title" href="' + esc(item.source_url) + '">' + esc(displayTitle(item)) + "</a>" +
              '<div class="sum">' + esc(displaySummary(item)) + "</div>" +
            "</div>" +
            '<span class="time">' + esc(humanDate(itemDate(item))) + "</span>" +
          "</div>"
        );
      }).join("");
    }

    function group(title, list) {
      if (!list.length) return "";
      return (
        '<section class="group">' +
          "<h2>" + esc(title) + ' <span class="count">' + list.length + "</span></h2>" +
          rows(list) +
        "</section>"
      );
    }

    var root = document.getElementById("week-groups");
    if (root) {
      root.innerHTML =
        group("New this week", cands) +
        group("Pull requests", prs) +
        group("Repositories", repos) +
        group("Docs & writing", docs);
    }
  }

  function projectSummary(project, items) {
    var mine = items.filter(function (i) { return i.project === project.name; });
    var best = mine.find(function (i) {
      var s = (i.summary || "").trim();
      return s && !GENERATED_SUMMARY.test(s);
    }) || mine[0];
    if (best) return displaySummary(best);
    return "Public project.";
  }

  function projectHref(project, items) {
    var mine = items.filter(function (i) { return i.project === project.name; });
    var repo = mine.find(function (i) { return i.source_type === "github_repository"; });
    if (repo) return repo.source_url;
    if (mine[0]) return mine[0].source_url;
    return "#";
  }

  function sourceLinkLabel(item) {
    if (item.source_type === "github_pull_request") {
      var m = String(item.source_url || "").match(/\/pull\/(\d+)/);
      return m ? "#" + m[1] : displayTitle(item);
    }
    var title = displayTitle(item);
    if (item.source_type === "github_repository") {
      var slash = title.lastIndexOf("/");
      if (slash >= 0) return title.slice(slash + 1);
    }
    return title;
  }

  function sourceKind(item) {
    return TYPE_DOT[item.source_type] || "repo";
  }


  function renderAtlas(projects, items, sources) {
    var grid = document.getElementById("atlas-grid");
    var coverage = document.getElementById("atlas-coverage");
    var chips = document.getElementById("atlas-chips");
    var search = document.getElementById("atlas-search");
    if (!grid) return;
    var sourceCount = (sources.sources || []).length || items.length;
    var preferred = PREFERRED_CHIPS;
    var present = {};
    projects.forEach(function (p) {
      (p.tags || []).forEach(function (t) {
        var key = TAG_ALIAS[t] || t;
        if (!HIDDEN_TAGS[t] && !HIDDEN_TAGS[key]) present[key] = true;
      });
    });
    var chipTags = preferred.filter(function (t) { return present[t]; });
    if (!EXPLICIT_CHIPS) {
      Object.keys(present).forEach(function (t) {
        if (chipTags.indexOf(t) === -1 && chipTags.length < 8) chipTags.push(t);
      });
    }

    var state = { q: "", tag: "all" };

    if (chips) {
      chips.innerHTML =
        '<span class="pill filter on" data-tag="all" role="button" tabindex="0">All</span>' +
        chipTags.map(function (t) {
          return '<span class="pill filter" data-tag="' + esc(t) + '" role="button" tabindex="0">' + esc(t) + "</span>";
        }).join("");
    }

    function matches(p) {
      var hay = (p.name + " " + (p.tags || []).join(" ") + " " + projectSummary(p, items)).toLowerCase();
      if (state.q && hay.indexOf(state.q) === -1) return false;
      if (state.tag !== "all") {
        var tags = (p.tags || []).map(function (t) { return TAG_ALIAS[t] || t; });
        if (tags.indexOf(state.tag) === -1) return false;
      }
      return true;
    }

    function paint() {
      var shown = projects.filter(matches);
      if (coverage) {
        coverage.innerHTML = "<b>" + shown.length + " project" + (shown.length === 1 ? "" : "s") + "</b> · " + sourceCount + " sources";
      }
      grid.innerHTML = shown.map(function (p) {
        var mine = items.filter(function (i) { return i.project === p.name; });
        var types = [];
        var seenT = {};
        mine.forEach(function (i) {
          var d = TYPE_DOT[i.source_type];
          if (d && !seenT[d]) { seenT[d] = true; types.push(d); }
        });
        var nSrc = (p.sources || []).length || mine.length || 1;
        var when = p.activity_at || p.latest_discovered_at || p.discovered_at;
        var name = p.name;
        var links = mine.slice().sort(function (a, b) {
          var oa = a.source_type === "github_pull_request" ? 0 : 1;
          var ob = b.source_type === "github_pull_request" ? 0 : 1;
          return oa - ob || String(sourceLinkLabel(a)).localeCompare(sourceLinkLabel(b));
        }).slice(0, 8).map(function (i) {
          return '<a class="card-src" href="' + esc(i.source_url) + '" title="' + esc(displayTitle(i)) + '">' +
            '<i class="dot ' + sourceKind(i) + '"></i>' + esc(sourceLinkLabel(i)) + "</a>";
        }).join("");
        return (
          '<article class="card">' +
            '<div class="card-top">' +
              "<h3><a href=\"" + esc(projectHref(p, items)) + "\">" + esc(name) + "</a></h3>" +
            "</div>" +
            '<p class="what">' + esc(projectSummary(p, items)) + "</p>" +
            (links ? '<div class="card-sources">' + links + "</div>" : "") +
            '<div class="card-foot">' +
              '<span class="time">' + esc(humanDate(when)) + "</span>" +
              '<span class="src">' + nSrc + " source" + (nSrc === 1 ? "" : "s") + "</span>" +
              '<span class="dots" title="' + esc(types.join(", ")) + '">' +
                types.map(function (t) { return '<i class="dot ' + t + '"></i>'; }).join("") +
              "</span>" +
            "</div>" +
          "</article>"
        );
      }).join("");
    }

    if (chips) {
      chips.addEventListener("click", function (ev) {
        var pill = ev.target.closest("[data-tag]");
        if (!pill) return;
        state.tag = pill.getAttribute("data-tag") || "all";
        chips.querySelectorAll(".pill.filter").forEach(function (el) {
          el.classList.toggle("on", el === pill);
        });
        paint();
      });
    }
    if (search) {
      search.addEventListener("input", function () {
        state.q = (search.value || "").trim().toLowerCase();
        paint();
      });
    }
    paint();
  }

  function renderSources(sources) {
    var root = document.getElementById("source-list");
    if (!root) return;
    var list = (sources.sources || []).slice().sort(function (a, b) {
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
    var state = { q: "", kind: "all" };
    var chips = document.getElementById("source-chips");
    var search = document.getElementById("source-search");
    var kinds = ["repo", "pr", "docs", "crate"];

    if (chips) {
      chips.innerHTML =
        '<span class="pill filter on" data-kind="all" role="button" tabindex="0">All</span>' +
        kinds.map(function (k) {
          return '<span class="pill filter" data-kind="' + k + '" role="button" tabindex="0">' + k + "</span>";
        }).join("");
    }

    function paint() {
      var shown = list.filter(function (s) {
        var hay = ((s.name || "") + " " + (s.project || "") + " " + (s.source_type || "")).toLowerCase();
        if (state.q && hay.indexOf(state.q) === -1) return false;
        if (state.kind !== "all" && (TYPE_DOT[s.source_type] || "repo") !== state.kind) return false;
        return true;
      });
      root.innerHTML = shown.map(function (s) {
        var kind = TYPE_DOT[s.source_type] || "repo";
        return (
          '<div class="source-row">' +
            '<span class="kind"><i class="dot ' + kind + '"></i>' + esc(kind) + "</span>" +
            '<a class="name" href="' + esc(s.url) + '">' + esc(s.name) + "</a>" +
            '<span class="proj">' + esc(s.project || "") + "</span>" +
          "</div>"
        );
      }).join("") || '<p class="muted">No sources match.</p>';
    }

    if (chips) {
      chips.addEventListener("click", function (ev) {
        var pill = ev.target.closest("[data-kind]");
        if (!pill) return;
        state.kind = pill.getAttribute("data-kind") || "all";
        chips.querySelectorAll(".pill.filter").forEach(function (el) {
          el.classList.toggle("on", el === pill);
        });
        paint();
      });
    }
    if (search) {
      search.addEventListener("input", function () {
        state.q = (search.value || "").trim().toLowerCase();
        paint();
      });
    }
    paint();
  }


  function boot(feed, projects, sources) {
    var items = feed.items || [];
    fillRefresh(feed.generated_at);
    fillStartCounts(feed, projects, sources);
    fillTopicCounts(items);
    var page = currentPage();
    if (page === "home") renderTeaser(items);
    if (page === "activity") renderFeed(items);
    if (page === "week") renderWeek(items, document.body.getAttribute("data-week") || "");
    if (page === "projects") renderAtlas(projects.projects || [], items, sources);
    if (page === "sources") renderSources(sources);
  }

  function fetchJson(url, fallback) {
    return fetch(url).then(function (r) {
      if (!r.ok) return fallback;
      return r.json();
    }).catch(function () { return fallback; });
  }

  Promise.all([
    fetchJson("/feed.json", { items: [] }),
    fetchJson("/projects.json", { projects: [] }),
    fetchJson("/sources.json", { sources: [] }),
    fetchJson("/watch.json", {})
  ]).then(function (res) {
    applyWatch(res[3] || {});
    if (!(res[0] && res[0].items)) {
      throw new Error("feed missing");
    }
    boot(res[0], res[1], res[2]);
  }).catch(function () {
    var el = document.getElementById("activity-feed") || document.getElementById("activity-teaser") || document.getElementById("atlas-grid") || document.getElementById("week-groups") || document.getElementById("source-list");
    if (el) el.innerHTML = '<p class="muted">Could not load live feed artifacts.</p>';
  });
})();
