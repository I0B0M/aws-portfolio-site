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
