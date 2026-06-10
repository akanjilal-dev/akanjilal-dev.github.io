(function () {
  "use strict";

  var statuses = ["operational", "degraded", "down", "unknown", "token_required"];
  var labels = {
    operational: "operational",
    degraded: "degraded",
    down: "down",
    unknown: "unknown",
    token_required: "token required"
  };
  var state = {
    data: null,
    activeTab: "health",
    open: false,
    lastFocus: null
  };

  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "class") node.className = attrs[key];
      else if (key === "text") node.textContent = attrs[key];
      else node.setAttribute(key, attrs[key]);
    });
    for (var i = 2; i < arguments.length; i += 1) {
      var child = arguments[i];
      if (child == null) continue;
      if (Array.isArray(child)) child.forEach(function (item) { node.appendChild(item); });
      else node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function fmtDate(value) {
    if (!value) return "not reported";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "not reported";
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function relative(value) {
    if (!value) return "pending";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "pending";
    var diff = date.getTime() - Date.now();
    var abs = Math.abs(diff);
    var hours = Math.round(abs / 36e5);
    var minutes = Math.max(1, Math.round(abs / 6e4));
    var unit = hours >= 1 ? hours + "h" : minutes + "m";
    return diff >= 0 ? "in " + unit : unit + " ago";
  }

  function statusClass(status) {
    return statuses.indexOf(status) >= 0 ? status : "unknown";
  }

  function badge(text, status) {
    return el("span", { class: "ls-badge " + statusClass(status), text: text || labels[status] || "signal" });
  }

  function dot(status) {
    return el("span", { class: "ls-dot " + statusClass(status), "aria-hidden": "true" });
  }

  function empty(message) {
    return el("div", { class: "ls-empty", text: message });
  }

  function card(title, meta, status, summary, sourceUrl, extra) {
    var head = el("div", { class: "ls-card-head" },
      el("div", {},
        el("h3", { text: title || "Unknown source" }),
        el("div", { class: "ls-meta", text: meta || "signal" })
      ),
      el("div", { class: "ls-status" }, dot(status), el("span", { text: labels[status] || status || "unknown" }))
    );
    var body = el("p", { text: summary || "No summary reported." });
    var foot = el("div", { class: "ls-card-foot" });
    (extra || []).forEach(function (item) { foot.appendChild(item); });
    if (sourceUrl) {
      foot.appendChild(el("a", { href: sourceUrl, target: "_blank", rel: "noreferrer", text: "source" }));
    }
    return el("article", { class: "ls-card" }, head, body, foot);
  }

  function summaryTab(data) {
    var summary = data.summary || {};
    var counts = [
      ["operational", "Operational", summary.operational || 0],
      ["degraded", "Degraded", summary.degraded || 0],
      ["down", "Down", summary.down || 0],
      ["unknown", "Unknown", summary.unknown || 0],
      ["token_required", "Token", summary.token_required || 0]
    ];
    var grid = el("div", { class: "ls-summary-grid" }, counts.map(function (item) {
      return el("div", { class: "ls-summary-tile" }, dot(item[0]), el("strong", { text: String(item[2]) }), el("span", { text: item[1] }));
    }));
    var health = (data.health || []).map(function (item) {
      return card(
        item.vendor,
        (item.category || "status") + " / " + (item.status_label || labels[item.status] || "unknown"),
        item.status,
        item.notes || item.queue_summary || "Public health signal.",
        item.source_url,
        [
          badge(item.status_label || labels[item.status], item.status),
          badge(item.devices_available == null ? "devices n/a" : item.devices_available + " available", "unknown"),
          el("span", { text: "checked " + fmtDate(item.last_checked) })
        ]
      );
    });
    return el("div", {}, grid, el("div", { class: "ls-list" }, health.length ? health : [empty("No health data yet.")]));
  }

  function quantumTab(data) {
    var items = (data.quantum_feed || []).map(function (item) {
      return card(
        item.vendor,
        item.type || "announcement",
        item.status,
        item.title + (item.summary ? " - " + item.summary : ""),
        item.url,
        [
          badge(item.published_at ? "fresh" : "public", item.status),
          el("span", { text: item.published_at ? "published " + fmtDate(item.published_at) : "checked " + fmtDate(item.last_checked) })
        ]
      );
    });
    return el("div", { class: "ls-list" }, items.length ? items : [empty("No quantum feed data yet.")]);
  }

  function mcpTab(data) {
    var items = (data.mcp || []).map(function (item) {
      var recent = (item.recent || []).slice(0, 3);
      var summary = item.notes || "Official MCP repository signal.";
      if (recent.length) summary += " Recent: " + recent.join("; ");
      return card(
        item.provider,
        item.repo,
        item.status,
        summary,
        item.source_url,
        [
          badge(item.status_label || labels[item.status], item.status),
          badge(item.server_count == null ? "count pending" : item.server_count + " servers", "unknown"),
          el("span", { text: "checked " + fmtDate(item.last_checked) })
        ]
      );
    });
    return el("div", { class: "ls-list" }, items.length ? items : [empty("No MCP catalog data yet.")]);
  }

  function renderPanel(panel) {
    var data = state.data;
    var body = panel.querySelector(".ls-body");
    body.textContent = "";
    if (!data) {
      body.appendChild(empty("Loading live signals..."));
      return;
    }
    if (state.activeTab === "quantum") body.appendChild(quantumTab(data));
    else if (state.activeTab === "mcp") body.appendChild(mcpTab(data));
    else body.appendChild(summaryTab(data));

    var stamp = panel.querySelector(".ls-stamp");
    stamp.textContent = "Refreshed " + fmtDate(data.generated_at) + " / next check " + relative(data.next_refresh_at);
  }

  function setOpen(open, panel, scrim, button) {
    state.open = open;
    document.body.classList.toggle("live-signals-open", open);
    panel.classList.toggle("open", open);
    scrim.classList.toggle("open", open);
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    if ("inert" in panel) panel.inert = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      state.lastFocus = document.activeElement;
      renderPanel(panel);
      window.setTimeout(function () {
        var active = panel.querySelector(".ls-tab.active") || panel.querySelector(".ls-close");
        if (active) active.focus({ preventScroll: true });
      }, 40);
    } else if (state.lastFocus && state.lastFocus.focus) {
      state.lastFocus.focus({ preventScroll: true });
    }
  }

  function trapFocus(event, panel) {
    if (!state.open || event.key !== "Tab") return;
    var focusables = panel.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])");
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function aggregate(data, key) {
    var list = key === "mcp" ? data.mcp || [] : key === "quantum" ? data.quantum_feed || [] : data.health || [];
    if (list.some(function (item) { return item.status === "down"; })) return "down";
    if (list.some(function (item) { return item.status === "degraded"; })) return "degraded";
    if (list.some(function (item) { return item.status === "operational"; })) return "operational";
    if (list.some(function (item) { return item.status === "token_required"; })) return "token_required";
    return "unknown";
  }

  function init() {
    var button = el("button", {
      class: "live-signals-trigger mono",
      type: "button",
      "aria-label": "Open Live Signals dashboard",
      "aria-controls": "live-signals-drawer",
      "aria-expanded": "false"
    },
      el("span", { class: "ls-trigger-dots" }, dot("unknown"), dot("unknown"), dot("unknown")),
      el("span", { class: "ls-trigger-main", text: "LIVE SIGNALS" }),
      el("span", { class: "ls-trigger-sub", text: "MCP · QUANTUM · HEALTH" })
    );
    var scrim = el("div", { class: "live-signals-scrim", tabindex: "-1" });
    var panel = el("aside", {
      class: "live-signals-drawer",
      id: "live-signals-drawer",
      role: "dialog",
      "aria-modal": "true",
      "aria-hidden": "true",
      "aria-label": "Live Signals dashboard"
    });
    if ("inert" in panel) panel.inert = true;
    var close = el("button", { class: "ls-close", type: "button", "aria-label": "Close Live Signals", text: "×" });
    var header = el("div", { class: "ls-head" },
      el("div", {},
        el("div", { class: "ls-kicker", text: "LIVE SIGNALS" }),
        el("h2", { text: "MCP · Quantum · Vendor Health" }),
        el("p", { class: "ls-stamp", text: "Refreshed pending / next check pending" }),
        el("p", { class: "ls-policy", text: "Official sources · API-backed where configured" })
      ),
      close
    );
    var tabs = el("div", { class: "ls-tabs", role: "tablist", "aria-label": "Live Signals views" });
    ["health", "quantum", "mcp"].forEach(function (name) {
      var tab = el("button", {
        class: "ls-tab" + (name === state.activeTab ? " active" : ""),
        type: "button",
        role: "tab",
        "aria-selected": name === state.activeTab ? "true" : "false",
        text: name.charAt(0).toUpperCase() + name.slice(1)
      });
      tab.addEventListener("click", function () {
        state.activeTab = name;
        tabs.querySelectorAll(".ls-tab").forEach(function (node) {
          var active = node === tab;
          node.classList.toggle("active", active);
          node.setAttribute("aria-selected", active ? "true" : "false");
        });
        renderPanel(panel);
      });
      tabs.appendChild(tab);
    });
    panel.appendChild(header);
    panel.appendChild(tabs);
    panel.appendChild(el("div", { class: "ls-body" }));
    document.body.appendChild(button);
    document.body.appendChild(scrim);
    document.body.appendChild(panel);

    button.addEventListener("click", function () { setOpen(true, panel, scrim, button); });
    close.addEventListener("click", function () { setOpen(false, panel, scrim, button); });
    scrim.addEventListener("click", function () { setOpen(false, panel, scrim, button); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && state.open) setOpen(false, panel, scrim, button);
      trapFocus(event, panel);
    });

    fetch("/data/live-signals.json", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("Live signals unavailable");
        return response.json();
      })
      .then(function (data) {
        state.data = data;
        var dots = button.querySelectorAll(".ls-trigger-dots .ls-dot");
        [aggregate(data, "mcp"), aggregate(data, "quantum"), aggregate(data, "health")].forEach(function (status, index) {
          dots[index].className = "ls-dot " + statusClass(status);
        });
        renderPanel(panel);
      })
      .catch(function () {
        state.data = {
          generated_at: null,
          next_refresh_at: null,
          summary: { operational: 0, degraded: 0, down: 0, unknown: 1, token_required: 0 },
          health: [{
            vendor: "Live Signals",
            category: "status",
            status: "unknown",
            status_label: "unavailable",
            notes: "Static JSON could not be loaded.",
            source_url: "/data/live-signals.json",
            last_checked: null
          }],
          quantum_feed: [],
          mcp: []
        };
        renderPanel(panel);
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
