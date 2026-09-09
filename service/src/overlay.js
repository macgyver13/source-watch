/** Pure overlay + render helpers. No Cloudflare APIs. */

export function slugify(value) {
  let s = String(value || "").trim().toLowerCase();
  s = s.replace(/https?:\/\//g, "");
  s = s.replace(/[^a-z0-9]+/g, "-");
  s = s.replace(/^-+|-+$/g, "");
  return s || "item";
}

function asMap(bucket) {
  if (!bucket) return new Map();
  if (bucket instanceof Map) return bucket;
  return new Map(Object.entries(bucket));
}

export function matchingExclusion(row, exclusions, fields = {}) {
  const haystack =
    fields.haystack != null
      ? fields.haystack
      : [row?.title, row?.summary, row?.name, row?.source_url, row?.url, ...(row?.tags || [])]
          .filter(Boolean)
          .join(" ");
  const url = fields.url != null ? fields.url : row?.source_url || row?.url || "";
  const project = fields.project != null ? fields.project : row?.project || "";
  const sourceType = fields.sourceType != null ? fields.sourceType : row?.source_type || "";
  const text = String(haystack || "").toLowerCase();
  const link = String(url || "").toLowerCase();
  const proj = String(project || "").toLowerCase();
  const stype = String(sourceType || "").toLowerCase();
  for (const rule of exclusions || []) {
    const kind = String(rule.kind || "").trim().toLowerCase();
    const value = String(rule.value || "").trim().toLowerCase();
    if (!value) continue;
    if (kind === "term" && text.includes(value)) return rule;
    if (kind === "url_prefix" && link.startsWith(value)) return rule;
    if (kind === "repo" && link.includes(`github.com/${value}`)) return rule;
    if (kind === "project" && (proj === value || slugify(proj) === slugify(value))) return rule;
    if (kind === "source_type" && stype === value) return rule;
  }
  return null;
}

export function matchesExclusion(row, exclusions, fields = {}) {
  return matchingExclusion(row, exclusions, fields) != null;
}


function applyTags(tags, patch) {
  let out = Array.isArray(tags) ? tags.slice() : [];
  const remove = new Set((patch.tags_remove || []).map((t) => String(t)));
  if (remove.size) out = out.filter((t) => !remove.has(String(t)));
  for (const add of patch.tags_add || []) {
    if (!out.some((t) => String(t) === String(add))) out.push(add);
  }
  return out;
}

function applyItemPatch(item, patch) {
  if (!patch) return item;
  const out = { ...item };
  if (patch.title != null) out.title = patch.title;
  if (patch.summary != null) out.summary = patch.summary;
  if (patch.status != null) out.status = patch.status;
  if (patch.discovered_at != null) {
    out.discovered_at = patch.discovered_at;
    out.event_time = patch.discovered_at;
  }
  if (patch.activity_at != null) out.activity_at = patch.activity_at;
  if (patch.project != null) out.project = patch.project;
  if (patch.tags_remove || patch.tags_add) out.tags = applyTags(out.tags, patch);
  return out;
}

function applyNamedPatch(row, patch, titleKey) {
  if (!patch) return row;
  const out = { ...row };
  if (patch.title != null) out[titleKey] = patch.title;
  if (patch.discovered_at != null) out.discovered_at = patch.discovered_at;
  if (patch.activity_at != null) out.activity_at = patch.activity_at;
  if (patch.tags_remove || patch.tags_add) out.tags = applyTags(out.tags, patch);
  return out;
}

function sourceIdForItem(item) {
  const id = String(item?.id || "");
  return id.startsWith("seed:") ? id.slice(5) : id;
}

function maxIso(values) {
  let best = "";
  for (const value of values) {
    const s = String(value || "");
    if (s > best) best = s;
  }
  return best || "";
}

export function applyOverlay({ items, projects, sources, overrides, exclusions }) {
  const itemPatches = asMap(overrides?.item);
  const projectPatches = asMap(overrides?.project);
  const sourcePatches = asMap(overrides?.source);
  const rawItems = Array.isArray(items) ? items : [];
  const rawProjects = Array.isArray(projects) ? projects : [];
  const rawSources = Array.isArray(sources) ? sources : [];

  const surviving = [];
  for (const raw of rawItems) {
    const item = applyItemPatch({ ...raw }, itemPatches.get(raw.id));
    const itemPatch = itemPatches.get(raw.id);
    if (itemPatch && itemPatch.hidden === true) continue;
    const projectId = slugify(item.project);
    const projectPatch = projectPatches.get(projectId);
    if (projectPatch && projectPatch.hidden === true) continue;
    const haystack = `${item.title || ""} ${item.summary || ""} ${item.source_url || ""} ${(item.tags || []).join(" ")}`;
    if (
      matchesExclusion(item, exclusions, {
        haystack,
        url: item.source_url,
        project: item.project,
        sourceType: item.source_type,
      })
    ) {
      continue;
    }
    surviving.push(item);
  }
  surviving.sort((a, b) => String(b.discovered_at || "").localeCompare(String(a.discovered_at || "")));

  const survivingSourceIds = new Set(surviving.map(sourceIdForItem));
  const outSources = [];
  for (const raw of rawSources) {
    if (!survivingSourceIds.has(raw.id)) continue;
    const patch = sourcePatches.get(raw.id);
    if (patch && patch.hidden === true) continue;
    const source = applyNamedPatch({ ...raw }, patch, "name");
    const haystack = `${source.name || ""} ${source.url || ""}`;
    if (
      matchesExclusion(source, exclusions, {
        haystack,
        url: source.url,
        project: source.project,
        sourceType: source.source_type,
      })
    ) {
      continue;
    }
    outSources.push(source);
  }
  const keptSourceIds = new Set(outSources.map((s) => s.id));
  outSources.sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" }));

  const outProjects = [];
  for (const raw of rawProjects) {
    const patch = projectPatches.get(raw.id);
    if (patch && patch.hidden === true) continue;
    const mine = surviving.filter((item) => slugify(item.project) === slugify(raw.name) || slugify(item.project) === raw.id);
    if (!mine.length) continue;
    const project = applyNamedPatch({ ...raw }, patch, "name");
    project.latest_discovered_at = maxIso(mine.map((i) => i.discovered_at));
    project.activity_at = maxIso(mine.map((i) => i.activity_at));
    project.sources = (raw.sources || []).filter((id) => keptSourceIds.has(id));
    outProjects.push(project);
  }
  outProjects.sort((a, b) =>
    String(b.latest_discovered_at || b.discovered_at || "").localeCompare(
      String(a.latest_discovered_at || a.discovered_at || ""),
    ),
  );

  return { items: surviving, projects: outProjects, sources: outSources };
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderRss(items, watch) {
  const now = new Date();
  const title = watch?.name || "Source Watch";
  const link = watch?.base_url || "https://example.com/";
  const description = watch?.description || "Public-source activity feed.";
  const parts = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0"><channel>',
    `<title>${esc(title)}</title>`,
    `<link>${esc(link)}</link>`,
    `<description>${esc(description)}</description>`,
    `<lastBuildDate>${now.toUTCString()}</lastBuildDate>`,
  ];
  for (const item of items || []) {
    const pub = new Date(item.discovered_at || item.event_time || now.toISOString());
    const pubDate = Number.isNaN(pub.getTime()) ? now : pub;
    parts.push(
      "<item>",
      `<title>${esc(item.title)}</title>`,
      `<link>${esc(item.source_url)}</link>`,
      `<guid isPermaLink="false">${esc(item.id)}</guid>`,
      `<description>${esc(item.summary)}</description>`,
      `<pubDate>${pubDate.toUTCString()}</pubDate>`,
      "</item>",
    );
  }
  parts.push("</channel></rss>");
  return parts.join("\n") + "\n";
}

export function renderJsonl(items) {
  return (items || [])
    .map((item) => JSON.stringify(item, Object.keys(item).sort()))
    .join("\n");
}

/** ISO week slug matching Python strftime("%G-W%V"). */
export function isoWeekSlug(raw) {
  if (!raw) return null;
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return null;
  const d = new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate()));
  const dayNr = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dayNr + 3);
  const isoYear = d.getUTCFullYear();
  const week1 = new Date(Date.UTC(isoYear, 0, 4));
  const w1day = (week1.getUTCDay() + 6) % 7;
  const week1Mon = new Date(week1);
  week1Mon.setUTCDate(week1.getUTCDate() - w1day);
  const week = 1 + Math.round((d.getTime() - week1Mon.getTime()) / (7 * 24 * 3600 * 1000));
  return `${isoYear}-W${String(week).padStart(2, "0")}`;
}

export function weekIndex(items) {
  const counts = new Map();
  for (const item of items || []) {
    const slug = isoWeekSlug(item.discovered_at || item.event_time);
    if (!slug) continue;
    counts.set(slug, (counts.get(slug) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([slug, count]) => ({ slug, count }))
    .sort((a, b) => (a.slug < b.slug ? 1 : a.slug > b.slug ? -1 : 0));
}
