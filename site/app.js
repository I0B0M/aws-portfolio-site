// Talks to the Lambda API through CloudFront (same origin, so no CORS and no key in the browser).
(function () {
  var countEl = document.getElementById("visit-count");

  function showCount(n) {
    if (countEl) countEl.textContent = "Visits: " + Number(n).toLocaleString();
  }

  // Count one visit per browser tab session, then just read the total on reloads.
  var counted = false;
  try { counted = sessionStorage.getItem("visit-counted") === "1"; } catch (e) {}

  fetch(counted ? "/api/visits" : "/api/visit", { method: counted ? "GET" : "POST" })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (d) {
      showCount(d.count);
      try { sessionStorage.setItem("visit-counted", "1"); } catch (e) {}
    })
    .catch(function () { if (countEl) countEl.textContent = ""; });

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
    fetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, body: body }; });
      })
      .then(function (res) {
        if (res.ok) {
          form.reset();
          say("Thanks! Your message was sent.", false);
        } else {
          say(res.body.error || "Something went wrong. Please try again.", true);
        }
      })
      .catch(function () { say("Network error. Please try again.", true); })
      .then(function () { button.disabled = false; });
  });
})();
