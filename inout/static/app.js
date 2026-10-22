// In-Out page behaviour. Plain JS, no dependencies.
(function () {
  "use strict";

  // ---- Clock page: keep the time and running totals current -----------------
  // Timestamps are server local time. Measuring everything against the server's
  // clock keeps the numbers right even if this device's clock is off.
  var clock = document.querySelector("[data-server-now]");
  if (clock) {
    var offset = Date.parse(clock.dataset.serverNow) - Date.now();
    var nowEl = clock.querySelector("[data-now]");
    var totalEl = clock.querySelector("[data-total]");
    var runningEls = clock.querySelectorAll("[data-running]");
    var startDay = new Date(Date.now() + offset).getDate();

    var hm = function (seconds) {
      var minutes = Math.max(0, Math.floor(seconds / 60));
      var m = minutes % 60;
      return Math.floor(minutes / 60) + "h " + (m < 10 ? "0" : "") + m + "m";
    };

    var tick = function () {
      var t = Date.now() + offset;
      var d = new Date(t);
      // A tab left open overnight would show yesterday; reload on a new day.
      if (d.getDate() !== startDay) return location.reload();

      var h = d.getHours(), m = d.getMinutes();
      nowEl.textContent = (h % 12 || 12) + ":" + (m < 10 ? "0" : "") + m + (h < 12 ? " AM" : " PM");
      runningEls.forEach(function (el) {
        el.textContent = hm((t - Date.parse(el.dataset.running)) / 1000);
      });
      if (totalEl.dataset.openSince) {
        totalEl.textContent = hm(Number(totalEl.dataset.total) +
          (t - Date.parse(totalEl.dataset.openSince)) / 1000);
      }
    };

    tick();
    setInterval(tick, 1000);
  }

  // ---- PIN pad: on-screen keys, and submit as soon as 4 digits are in --------
  var pinForm = document.querySelector("[data-pin-form]");
  if (pinForm) {
    var pinInput = pinForm.querySelector("[data-pin-input]");
    var maybeSubmit = function () {
      pinInput.value = pinInput.value.replace(/\D/g, "").slice(0, 4);
      if (pinInput.value.length === 4) pinForm.requestSubmit();
    };
    pinInput.addEventListener("input", maybeSubmit);
    pinForm.querySelectorAll("[data-key]").forEach(function (key) {
      key.addEventListener("click", function () {
        var k = key.dataset.key;
        if (k === "clear") pinInput.value = "";
        else if (k === "back") pinInput.value = pinInput.value.slice(0, -1);
        else pinInput.value += k;
        pinInput.focus();
        maybeSubmit();
      });
    });
  }

  // ---- Shared screen: sign out after a stretch with no taps or typing --------
  var idleHost = document.querySelector("[data-idle-signout]");
  var signoutForm = document.querySelector("[data-signout-form]");
  if (idleHost && signoutForm) {
    var limit = Number(idleHost.dataset.idleSignout) || 30;
    var leftEl = idleHost.querySelector("[data-idle-left]");
    var idleSince = Date.now();
    var reset = function () { idleSince = Date.now(); };
    ["pointerdown", "keydown", "input", "scroll"].forEach(function (type) {
      document.addEventListener(type, reset, { passive: true });
    });
    // Sent with fetch rather than by submitting the form: on a plain-http site some
    // browsers (iPhone especially) stop every form submission with a "not secure"
    // warning, and a script-sent request doesn't trigger it.
    var signOut = function () {
      fetch(signoutForm.action, { method: "POST", body: new FormData(signoutForm), credentials: "same-origin" })
        .then(function (r) { location.replace(r.url); })
        // If that failed, reload; the server signs idle people out on its own anyway.
        .catch(function () { location.reload(); });
    };
    var timer = setInterval(function () {
      var left = limit - Math.floor((Date.now() - idleSince) / 1000);
      if (left <= 0) {
        clearInterval(timer); // fire once, never on repeat
        return signOut();
      }
      if (leftEl) {
        leftEl.textContent = left <= 10 ? "in " + left + " second" + (left === 1 ? "" : "s")
                                        : "after " + limit + " seconds";
      }
    }, 1000);
  }

  // ---- Confirm buttons: first click asks, second click submits ---------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-confirm]");
    if (!btn || btn.classList.contains("is-armed")) return;
    e.preventDefault();
    var original = btn.textContent;
    btn.textContent = btn.dataset.confirm;
    btn.classList.add("is-armed");
    setTimeout(function () {
      btn.classList.remove("is-armed");
      btn.textContent = original;
    }, 4000);
  });

  // ---- Submit each form once: stops double clicks clocking in twice ----------
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-once")) return;
    if (form.dataset.sent) return e.preventDefault();
    form.dataset.sent = "1";
    var buttons = form.querySelectorAll("button");
    // Disable after the browser has read the clicked button's values.
    setTimeout(function () {
      buttons.forEach(function (b) { b.disabled = true; });
    }, 0);
    // If the page is still here a few seconds later, the submit never left (for example
    // someone cancelled a browser "not secure" warning), so let them try again. A real
    // double tap is still caught by the server's "expect" check.
    setTimeout(function () {
      delete form.dataset.sent;
      buttons.forEach(function (b) { b.disabled = false; });
    }, 4000);
  });

  // Going back to a page can restore it with buttons still disabled.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-once]").forEach(function (form) {
      delete form.dataset.sent;
      form.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
    });
  });
})();
