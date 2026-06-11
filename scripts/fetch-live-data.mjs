// Fetches public live data for the akanjilal.dev Live page and writes JSON into
// assets/data/. Node 20, no dependencies (global fetch). Fault tolerant: one
// source failing never aborts the run, it just drops that entry.
import { writeFile, mkdir } from "node:fs/promises";

const GH = "https://api.github.com";
const token = process.env.GITHUB_TOKEN;
const ghHeaders = {
  "Accept": "application/vnd.github+json",
  "User-Agent": "akanjilal-live-data",
  ...(token ? { "Authorization": `Bearer ${token}` } : {})
};

const withTimeout = (ms) => {
  const c = new AbortController();
  setTimeout(() => c.abort(), ms);
  return c.signal;
};

async function getJson(url, headers = {}) {
  const res = await fetch(url, { headers, signal: withTimeout(15000) });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

async function getText(url) {
  const res = await fetch(url, { headers: { "User-Agent": "akanjilal-live-data" }, signal: withTimeout(15000) });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.text();
}

async function repoMeta(owner, repo) {
  try {
    const r = await getJson(`${GH}/repos/${owner}/${repo}`, ghHeaders);
    return { stars: r.stargazers_count ?? null, updated_at: r.pushed_at ?? null, description: r.description ?? "" };
  } catch { return { stars: null, updated_at: null, description: "" }; }
}

async function latestVersion(owner, repo) {
  try {
    const r = await getJson(`${GH}/repos/${owner}/${repo}/releases/latest`, ghHeaders);
    return r.tag_name || r.name || null;
  } catch { return null; }
}

// ---- 3A connectors ----
async function awsConnectors() {
  const meta = await repoMeta("awslabs", "mcp");
  const items = await getJson(`${GH}/repos/awslabs/mcp/contents/src`, ghHeaders);
  return items
    .filter((it) => it.type === "dir")
    .map((it) => ({
      vendor: "aws",
      name: it.name,
      description: "",
      repo_url: it.html_url,
      install: `uvx awslabs.${it.name}@latest`,
      language: "Python",
      version: null,
      updated_at: meta.updated_at,
      stars: meta.stars
    }));
}

async function repoConnector(vendor, owner, repo, install, language) {
  const meta = await repoMeta(owner, repo);
  const version = await latestVersion(owner, repo);
  return [{
    vendor,
    name: repo,
    description: meta.description,
    repo_url: `https://github.com/${owner}/${repo}`,
    install,
    language,
    version,
    updated_at: meta.updated_at,
    stars: meta.stars
  }];
}

const googleConnectors = async () => (await Promise.all([
  repoConnector("google", "googleapis", "gcloud-mcp", "npx -y @google-cloud/gcloud-mcp", "TypeScript"),
  repoConnector("google", "googleapis", "genai-toolbox", "see repository releases", "Go")
])).flat();

const microsoftConnectors = () =>
  repoConnector("microsoft", "Azure", "azure-mcp", "npx -y @azure/mcp@latest", "C#");

// ---- 3B quantum feed ----
async function latestRelease(vendor, owner, repo) {
  const rel = await getJson(`${GH}/repos/${owner}/${repo}/releases?per_page=1`, ghHeaders);
  const r = rel[0];
  if (!r) return [];
  return [{
    vendor,
    type: "release",
    title: r.name || r.tag_name,
    summary: (r.body || "").replace(/\r?\n+/g, " ").slice(0, 240),
    url: r.html_url,
    published_at: r.published_at,
    source: "github-releases"
  }];
}

function parseFeed(xml, vendor, max = 5) {
  const blocks = xml.split(/<item[\s>]|<entry[\s>]/).slice(1, max + 1);
  const pick = (b, tags) => {
    for (const t of tags) {
      const m = b.match(new RegExp(`<${t}[^>]*>([\\s\\S]*?)</${t}>`));
      if (m) return m[1].replace(/<!\[CDATA\[|\]\]>/g, "").trim();
    }
    return "";
  };
  return blocks.map((b) => ({
    vendor,
    type: "announcement",
    title: pick(b, ["title"]).replace(/<[^>]+>/g, ""),
    summary: pick(b, ["description", "summary"]).replace(/<[^>]+>/g, "").slice(0, 240),
    url: (b.match(/<link[^>]*href="([^"]+)"/) || b.match(/<link>([^<]+)<\/link>/) || [])[1] || "",
    published_at: pick(b, ["pubDate", "updated", "published"]),
    source: "rss"
  })).filter((i) => i.title);
}

// ---- 3C health ----
const LABEL = { none: "Operational", minor: "Degraded", major: "Partial outage", critical: "Major outage" };

async function statuspage(vendor, scope, host) {
  const s = await getJson(`https://${host}/api/v2/summary.json`);
  const ind = s.status?.indicator || "missing";
  return {
    vendor, scope,
    indicator: ind,
    label: LABEL[ind] || "Status unknown",
    url: `https://${host}`,
    updated_at: s.page?.updated_at || new Date().toISOString(),
    open_incidents: (s.incidents || []).map((i) => ({ title: i.name, url: i.shortlink, impact: i.impact }))
  };
}

// a vendor with no machine readable status feed: link the human page, no live badge
const linkEntry = (vendor, scope, url) => ({
  vendor, scope, indicator: "missing", label: "Status page", url,
  updated_at: new Date().toISOString(), open_incidents: []
});

// try the Statuspage summary, fall back to a link if the host is wrong or down
async function statusOrLink(vendor, scope, host, humanUrl) {
  try { return await statuspage(vendor, scope, host); }
  catch { return linkEntry(vendor, scope, humanUrl || `https://${host}`); }
}

const settled = async (label, p) => {
  try { return await p; }
  catch (e) { console.error(`skip ${label}: ${e.message}`); return null; }
};

function topPerVendorThenOverall(items) {
  const byVendor = {};
  for (const i of items) (byVendor[i.vendor] ||= []).push(i);
  const kept = [];
  for (const v of Object.keys(byVendor)) {
    byVendor[v].sort((a, b) => new Date(b.published_at || 0) - new Date(a.published_at || 0));
    kept.push(...byVendor[v].slice(0, 5));
  }
  kept.sort((a, b) => new Date(b.published_at || 0) - new Date(a.published_at || 0));
  return kept.slice(0, 20);
}

async function main() {
  await mkdir("assets/data", { recursive: true });

  const connectors = (await Promise.all([
    settled("aws-connectors", awsConnectors()),
    settled("google-connectors", googleConnectors()),
    settled("ms-connectors", microsoftConnectors())
  ])).flat().filter(Boolean);

  const feedRaw = (await Promise.all([
    settled("braket-rel", latestRelease("aws", "amazon-braket", "amazon-braket-sdk-python")),
    settled("qiskit-rel", latestRelease("ibm", "Qiskit", "qiskit")),
    settled("cirq-rel", latestRelease("google", "quantumlib", "Cirq")),
    settled("qsharp-rel", latestRelease("microsoft", "microsoft", "qsharp")),
    settled("aws-news", getText("https://aws.amazon.com/blogs/quantum-computing/feed/").then((x) => parseFeed(x, "aws"))),
    settled("ms-news", getText("https://azure.microsoft.com/en-us/blog/topics/quantum/feed/").then((x) => parseFeed(x, "microsoft"))),
    settled("ibm-news", getText("https://www.ibm.com/quantum/blog/feed.xml").then((x) => parseFeed(x, "ibm")))
  ])).flat().filter(Boolean);
  const feed = topPerVendorThenOverall(feedRaw);

  const health = (await Promise.all([
    settled("ionq", statusOrLink("ionq", "Cloud", "status.ionq.co", "https://ionq.com")),
    settled("dwave", statusOrLink("dwave", "Leap platform", "dwavesys.statuspage.io", "https://cloud.dwavesys.com/leap/")),
    settled("rigetti", statusOrLink("rigetti", "Quantum Cloud Services", "rigetti.statuspage.io", "https://qcs.rigetti.com")),
    settled("quantinuum", statusOrLink("quantinuum", "Systems", "quantinuum.statuspage.io", "https://www.quantinuum.com")),
    settled("gcp", getJson("https://status.cloud.google.com/incidents.json").then((inc) => {
      const open = inc.some((i) => !i.end);
      return {
        vendor: "google", scope: "Google Cloud (platform proxy)",
        indicator: open ? "minor" : "none", label: open ? "Degraded" : "Operational",
        url: "https://status.cloud.google.com", updated_at: new Date().toISOString(),
        open_incidents: inc.filter((i) => !i.end).slice(0, 3).map((i) => ({ title: i.external_desc, url: i.uri, impact: i.severity }))
      };
    })),
    // Azure, AWS, and IBM do not expose a reliable quantum scoped machine feed, link the human dashboards honestly
    linkEntry("microsoft", "Azure status", "https://status.azure.com/en-us/status"),
    linkEntry("aws", "AWS Health Dashboard", "https://health.aws.amazon.com/health/status"),
    linkEntry("ibm", "IBM Quantum platform", "https://quantum.ibm.com")
  ])).filter(Boolean);

  const now = new Date().toISOString();
  await writeFile("assets/data/connectors.json", JSON.stringify({ updated_at: now, items: connectors }, null, 2));
  await writeFile("assets/data/quantum-feed.json", JSON.stringify({ updated_at: now, items: feed }, null, 2));
  await writeFile("assets/data/quantum-health.json", JSON.stringify({ updated_at: now, items: health }, null, 2));
  await writeFile("assets/data/_meta.json", JSON.stringify({ updated_at: now }, null, 2));
  console.log(`connectors ${connectors.length}, feed ${feed.length}, health ${health.length}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
