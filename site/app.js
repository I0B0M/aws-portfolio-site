// Talks to the Lambda API through CloudFront (same origin, so no CORS and no key in the browser).
(function () {
  var footerCount = document.getElementById("visit-count");
  var logEl = document.getElementById("live-log");

  function num(n) { return Number(n).toLocaleString(); }
  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // Call the API, time it, and always resolve with what happened (even on errors).
  function api(method, path, body) {
    var started = performance.now();
    var init = { method: method };
    if (body) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetch(path, init)
      .then(function (r) {
        return r.text().then(function (text) {
          var json = null;
          try { json = JSON.parse(text); } catch (e) {}
          return { method: method, path: path, status: r.status, ok: r.ok, ms: Math.round(performance.now() - started), json: json, text: text };
        });
      })
      .catch(function () {
        return { method: method, path: path, status: 0, ok: false, ms: Math.round(performance.now() - started), json: null, text: "Network error" };
      });
  }

  // Show one request/response pair in the "Raw API responses" panel.
  function logCall(res) {
    if (!logEl) return;
    var wait = logEl.querySelector(".live__wait");
    if (wait) wait.remove();

    var entry = document.createElement("div");
    entry.className = "live__entry";

    var req = document.createElement("div");
    req.className = "live__req";
    var line = document.createElement("span");
    line.textContent = res.method + " " + res.path;
    var status = document.createElement("span");
    status.className = "live__status " + (res.ok ? "live__status--ok" : "live__status--err");
    status.textContent = res.status || "failed";
    var ms = document.createElement("span");
    ms.className = "live__ms";
    ms.textContent = res.ms + " ms";
    req.append(line, status, ms);

    var pre = document.createElement("pre");
    pre.className = "live__json";
    pre.textContent = res.json ? JSON.stringify(res.json, null, 2) : res.text;

    entry.append(req, pre);
    logEl.append(entry);
  }

  function showStats(res) {
    if (!res.ok || !res.json) {
      setText("live-visits", "n/a");
      setText("live-messages", "n/a");
      setText("live-snapshot", "n/a");
      setText("live-latency", res.ms + " ms");
      if (footerCount) footerCount.textContent = "";
      return;
    }
    var d = res.json;
    setText("live-visits", num(d.visits));
    setText("live-messages", num(d.messages));
    setText("live-snapshot", d.latestSnapshot ? num(d.latestSnapshot.visits) : "pending");
    // Words are wider than digits in the display font; shrink so they stay inside the card.
    document.getElementById("live-snapshot").classList.toggle("stat__num--text", !d.latestSnapshot);
    setText("live-latency", res.ms + " ms");
    if (footerCount) footerCount.textContent = "Visits: " + num(d.visits);
  }

  // Count one visit per browser tab session, then read the stats.
  var counted = false;
  try { counted = sessionStorage.getItem("visit-counted") === "1"; } catch (e) {}

  var first = counted
    ? Promise.resolve(null)
    : api("POST", "/api/visit").then(function (res) {
        logCall(res);
        if (res.ok) { try { sessionStorage.setItem("visit-counted", "1"); } catch (e) {} }
      });

  first.then(function () { return api("GET", "/api/stats"); }).then(function (res) {
    logCall(res);
    showStats(res);
  });

  // ---- Market Pulse: stock quotes served by the Lambda API ----
  var NAMES = { AMZN: "Amazon", NVDA: "NVIDIA", AAPL: "Apple", MSFT: "Microsoft", GOOGL: "Alphabet", TSLA: "Tesla" };
  var pulseEl = document.getElementById("pulse");
  var metaEl = document.getElementById("pulse-meta");
  var rawEl = document.getElementById("pulse-json");
  var quotesLogged = false;
  var lastUpdated = null;

  function money(n) {
    return "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function ago(iso) {
    var secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 90) return "just now";
    var mins = Math.round(secs / 60);
    if (mins < 90) return mins + " min ago";
    var hours = Math.round(mins / 60);
    return hours < 48 ? hours + " h ago" : Math.round(hours / 24) + " days ago";
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function tickerCard(q) {
    var card = el("article", "card ticker");
    var top = el("div", "ticker__top");
    top.append(el("span", "ticker__symbol", q.symbol), el("span", "ticker__name", NAMES[q.symbol] || ""));
    card.append(top, el("div", "ticker__price", money(q.price)));

    var dir = q.change === null ? "flat" : q.change > 0 ? "up" : q.change < 0 ? "down" : "flat";
    var arrow = dir === "up" ? "▲ " : dir === "down" ? "▼ " : "• ";
    var label = q.change === null
      ? "No prior close"
      : arrow + (q.change > 0 ? "+" : "") + q.change.toFixed(2) + " (" + (q.changePercent > 0 ? "+" : "") + q.changePercent.toFixed(2) + "%)";
    card.append(el("span", "ticker__change ticker__change--" + dir, label));

    var detail = el("div", "ticker__detail");
    if (q.prevClose !== null) detail.append(el("span", "", "Prev close " + money(q.prevClose)));
    if (q.open !== null) detail.append(el("span", "", "Open " + money(q.open)));
    card.append(detail);
    return card;
  }

  function renderMeta() {
    if (metaEl && lastUpdated) metaEl.textContent = "Updated " + ago(lastUpdated) + " · Alpaca Market Data (IEX) · via AWS Lambda + EventBridge";
  }

  function showQuotes(res) {
    if (!pulseEl) return;
    if (rawEl) rawEl.textContent = res.json ? JSON.stringify(res.json, null, 2) : res.text;
    pulseEl.replaceChildren();
    if (!res.ok || !res.json || !res.json.quotes) {
      var box = el("div", "card pulse__error");
      box.append(el("p", "", (res.json && res.json.error) || "Quotes are unavailable right now. Please try again shortly."));
      pulseEl.append(box);
      if (metaEl) metaEl.textContent = "";
      return;
    }
    res.json.quotes.forEach(function (q) { pulseEl.append(tickerCard(q)); });
    lastUpdated = res.json.updatedAt;
    renderMeta();
  }

  function loadQuotes() {
    return api("GET", "/api/quotes").then(function (res) {
      if (!quotesLogged) { logCall(res); quotesLogged = true; }
      showQuotes(res);
    });
  }
  loadQuotes();
  setInterval(loadQuotes, 60000);
  setInterval(renderMeta, 30000);

  var form = document.getElementById("contact-form");
  if (!form) return;
  var statusEl = document.getElementById("contact-status");
  var button = document.getElementById("contact-submit");

  function say(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle("is-error", !!isError);
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var data = Object.fromEntries(new FormData(form).entries());
    button.disabled = true;
    say("Sending…", false);
    api("POST", "/api/contact", data).then(function (res) {
      logCall(res);
      if (res.ok) {
        form.reset();
        say("Thanks! Your message was sent. (API replied " + res.status + " in " + res.ms + " ms; see Live API above.)", false);
        return api("GET", "/api/stats").then(function (s) { logCall(s); showStats(s); });
      }
      say((res.json && res.json.error) || "Something went wrong. Please try again.", true);
    }).then(function () { button.disabled = false; });
  });
})();
