/* ==========================================================================
   Tennis Evolution — public site behaviour
   No dependencies, no build step. Everything degrades to a readable page.
   ========================================================================== */
(function () {
  "use strict";

  var doc = document;

  /* ---------- 1. missing demo assets fall back to a court-pattern block ----
     The media files are produced separately from this page, so the layout
     never depends on them existing. An <img>/<video> that 404s marks its
     frame and the frame keeps its shape. */
  function markMissing(el) {
    var host = el.closest ? el.closest("[data-media], .label-shot") : null;
    if (host) host.classList.add("is-missing");
  }

  doc.addEventListener(
    "error",
    function (event) {
      var el = event.target;
      if (el && (el.tagName === "IMG" || el.tagName === "VIDEO")) markMissing(el);
    },
    true
  );

  function auditAssets() {
    var images = doc.querySelectorAll("img");
    for (var i = 0; i < images.length; i++) {
      if (images[i].complete && images[i].naturalWidth === 0) markMissing(images[i]);
    }
    var videos = doc.querySelectorAll("video");
    for (var j = 0; j < videos.length; j++) {
      if (videos[j].error) markMissing(videos[j]);
    }
  }

  if (doc.readyState === "complete") auditAssets();
  else window.addEventListener("load", auditAssets, { once: true });

  /* ---------- 2. demo player: custom poster, click to play --------------- */
  var reel = doc.getElementById("reel");
  if (reel) {
    var player = reel.closest(".player");
    var cover = player ? player.querySelector(".player-cover-btn") : null;

    if (cover) {
      cover.addEventListener("click", function () {
        var attempt = reel.play();
        if (attempt && typeof attempt.catch === "function") {
          attempt.catch(function () {
            /* autoplay policy or a missing file — native controls still work */
          });
        }
      });
    }

    reel.addEventListener("play", function () {
      if (player) {
        player.classList.add("is-playing");
        player.classList.add("has-played");
      }
    });

    reel.addEventListener("pause", function () {
      if (player) player.classList.remove("is-playing");
    });

    reel.addEventListener("ended", function () {
      if (player) player.classList.remove("is-playing");
    });
  }

  /* ---------- 3. before / after: swap labelled <-> clean, keep position -- */
  var compare = doc.getElementById("compare-video");
  if (compare) {
    var buttons = doc.querySelectorAll(".seg-btn[data-mode]");
    var fileTag = doc.querySelector("[data-file-name]");
    var currentMode = compare.getAttribute("data-mode") || "labeled";
    var token = 0;
    var sources = {
      labeled: compare.getAttribute("data-src-labeled"),
      plain: compare.getAttribute("data-src-plain")
    };
    var posters = {
      labeled: compare.getAttribute("data-poster-labeled"),
      plain: compare.getAttribute("data-poster-plain")
    };
    var labels = { labeled: "demo-reel.mp4", plain: "demo-plain.mp4" };

    function paint(mode) {
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].setAttribute("aria-pressed", String(buttons[i].getAttribute("data-mode") === mode));
      }
      if (fileTag) fileTag.textContent = labels[mode] || labels.labeled;
      if (posters[mode]) compare.setAttribute("poster", posters[mode]);
    }

    function load(mode) {
      if (mode === currentMode || !sources[mode]) return;
      currentMode = mode;
      paint(mode);

      var wasPlaying = !compare.paused && !compare.ended;
      var position = compare.currentTime || 0;
      var mine = ++token;

      var resume = function () {
        if (!wasPlaying) return;
        var attempt = compare.play();
        if (attempt && typeof attempt.catch === "function") {
          attempt.catch(function () {});
        }
      };

      var onMeta = function () {
        compare.removeEventListener("loadedmetadata", onMeta);
        if (mine !== token) return;
        if (position > 0) {
          try {
            var end = isFinite(compare.duration) && compare.duration > 0 ? compare.duration : position;
            compare.currentTime = Math.min(position, Math.max(0, end - 0.05));
          } catch (err) {
            /* seek is best effort; the source change itself is the point */
          }
          /* resume only once the seek lands, so playback does not restart at 0 */
          compare.addEventListener("seeked", resume, { once: true });
          window.setTimeout(resume, 700);
        } else {
          resume();
        }
      };

      compare.addEventListener("loadedmetadata", onMeta);
      compare.setAttribute("src", sources[mode]);
      try {
        compare.load();
      } catch (err) {
        /* older engines: setting src is enough */
      }
      window.setTimeout(function () {
        compare.removeEventListener("loadedmetadata", onMeta);
      }, 8000);
    }

    for (var b = 0; b < buttons.length; b++) {
      buttons[b].addEventListener("click", function () {
        load(this.getAttribute("data-mode"));
      });
    }

    paint(currentMode);
  }

  /* ---------- 4. copy the install command -------------------------------- */
  function legacyCopy(text) {
    try {
      var area = doc.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "-1000px";
      area.style.opacity = "0";
      doc.body.appendChild(area);
      area.select();
      var ok = doc.execCommand("copy");
      doc.body.removeChild(area);
      return ok;
    } catch (err) {
      return false;
    }
  }

  var copyButtons = doc.querySelectorAll("[data-copy]");
  for (var c = 0; c < copyButtons.length; c++) {
    copyButtons[c].addEventListener("click", function () {
      var button = this;
      var text = button.getAttribute("data-copy") || "";
      var original = button.getAttribute("data-label") || "Copy";

      function done() {
        button.textContent = "Copied";
        button.setAttribute("data-copied", "true");
        window.setTimeout(function () {
          button.textContent = original;
          button.removeAttribute("data-copied");
        }, 1900);
      }

      function failed() {
        button.textContent = "Select it";
        window.setTimeout(function () {
          button.textContent = original;
        }, 1900);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {
          if (legacyCopy(text)) done();
          else failed();
        });
      } else if (legacyCopy(text)) {
        done();
      } else {
        failed();
      }
    });
  }

  /* ---------- 5. header hairline once the page has scrolled -------------- */
  var head = doc.querySelector(".site-head");
  if (head) {
    var onScroll = function () {
      head.classList.toggle("is-stuck", window.scrollY > 8);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---------- 6. deep links into a FAQ item open it ---------------------- */
  if (window.location.hash) {
    try {
      var target = doc.querySelector(window.location.hash);
      if (target && target.tagName === "DETAILS") target.open = true;
    } catch (err) {
      /* not a valid selector — ignore */
    }
  }
})();

/* ==========================================================================
   v0.2 — rally lab, agent tabs + chat, checkout, licence retrieval
   ========================================================================== */
(function () {
  "use strict";

  var doc = document;
  var zh = (doc.documentElement.lang || "").indexOf("zh") === 0;
  var FREE = 5;
  var SOURCE_SECONDS = 2849.2;
  var RECORDED = Date.UTC(2026, 9, 24, 9, 16, 54);

  function mmss(sec) {
    var s = Math.round(sec);
    var m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
  }

  function clock(offset) {
    var d = new Date(RECORDED + offset * 1000 + 8 * 3600 * 1000);
    return d.toISOString().slice(0, 10) + " " + d.toISOString().slice(11, 16);
  }

  /* ---------- rally lab ---------- */
  var lab = doc.querySelector("[data-lab]");
  if (lab) {
    var seed = 5155;
    var rand = function () {
      seed = (seed + 0x6d2b79f5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    var rallies = [
      { start: 135, end: 172, hits: 24 },
      { start: 890, end: 926, hits: 24 },
      { start: 1634, end: 1688, hits: 40 },
      { start: 2068, end: 2102, hits: 27 },
      { start: 2329, end: 2378, hits: 36 }
    ];
    while (rallies.length < 131) {
      var hits = Math.max(2, Math.round(2 + 21 * Math.pow(rand(), 2.4)));
      var start = rand() * (SOURCE_SECONDS - 40);
      var dur = hits * 1.15 + 2 + rand() * 3;
      var clash = rallies.some(function (r) {
        return start < r.end + 4 && start + dur > r.start - 4;
      });
      if (!clash) rallies.push({ start: start, end: start + dur, hits: hits });
    }
    rallies.sort(function (a, b) {
      return a.start - b.start;
    });
    var ranked = rallies.slice().sort(function (a, b) {
      return b.hits - a.hits || b.end - b.start - (a.end - a.start) || a.start - b.start;
    });
    ranked.forEach(function (r, i) {
      r.rank = i + 1;
    });

    var ticksHost = doc.getElementById("lab-ticks");
    var maxHits = 40;
    rallies.forEach(function (r) {
      var tick = doc.createElement("i");
      tick.style.left = ((r.start + r.end) / 2 / SOURCE_SECONDS) * 100 + "%";
      tick.style.height = 10 + (r.hits / maxHits) * 88 + "%";
      r.tick = tick;
      ticksHost.appendChild(tick);
    });

    var range = doc.getElementById("lab-range");
    var out = doc.getElementById("lab-n");
    var strip = doc.getElementById("lab-strip");
    var proBar = doc.getElementById("lab-pro");
    var sortBtns = lab.querySelectorAll("[data-sort]");
    var sortMode = "hits";
    var unit = lab.getAttribute("data-unit");
    var srcLabel = lab.getAttribute("data-src");
    var badge = lab.querySelector(".lab-badge");
    var hot = null;

    var preview = function (r) {
      if (hot) hot.tick.classList.remove("hot");
      hot = r;
      r.tick.classList.add("hot");
      doc.getElementById("lp-clock").textContent = clock(r.start);
      doc.getElementById("lp-src").textContent = srcLabel + " " + mmss(r.start);
      doc.getElementById("lp-hits").textContent = r.hits;
      badge.classList.remove("pop");
      void badge.offsetWidth;
      badge.classList.add("pop");
    };

    var render = function () {
      var n = Number(range.value);
      out.textContent = n;
      range.style.setProperty("--pct", ((Math.min(n, FREE) - 1) / 19) * 100 + "%");
      var chosen = ranked.slice(0, n);
      rallies.forEach(function (r) {
        var on = r.rank <= n;
        r.tick.classList.toggle("on", on);
        r.tick.classList.toggle("pro", on && r.rank > FREE);
      });
      var order = chosen.slice();
      if (sortMode === "start") {
        order.sort(function (a, b) {
          return a.start - b.start;
        });
      }
      strip.textContent = "";
      order.forEach(function (r, i) {
        var li = doc.createElement("li");
        li.tabIndex = 0;
        if (r.rank > FREE) li.className = "pro";
        li.style.animationDelay = Math.min(i, 12) * 28 + "ms";
        li.innerHTML =
          '<span class="r-rank">#' + String(i + 1).padStart(2, "0") + "</span>" +
          '<b class="r-hits">' + r.hits + "<small>" + unit + "</small></b>" +
          '<span class="r-time">' + mmss(r.start) + "–" + mmss(r.end) + "</span>";
        var show = function () {
          preview(r);
        };
        li.addEventListener("mouseenter", show);
        li.addEventListener("focus", show);
        strip.appendChild(li);
      });
      var total = chosen.reduce(function (sum, r) {
        return sum + (r.end - r.start) + 3.8;
      }, 0);
      doc.getElementById("lab-len").textContent = mmss(total);
      doc.getElementById("lab-count").textContent = n;
      doc.getElementById("lab-hits").textContent = chosen.reduce(function (s, r) {
        return s + r.hits;
      }, 0);
      var pro = n > FREE;
      lab.classList.toggle("is-pro", pro);
      proBar.hidden = !pro;
      if (!hot || hot.rank > n) preview(order[0]);
    };

    range.addEventListener("input", render);
    for (var s = 0; s < sortBtns.length; s++) {
      sortBtns[s].addEventListener("click", function () {
        sortMode = this.getAttribute("data-sort");
        for (var k = 0; k < sortBtns.length; k++) {
          sortBtns[k].setAttribute("aria-pressed", String(sortBtns[k] === this));
        }
        render();
      });
    }
    render();
  }

  /* ---------- agent setup tabs ---------- */
  var tabsHost = doc.querySelector("[data-tabs]");
  if (tabsHost) {
    var tabs = tabsHost.querySelectorAll("[data-tab]");
    var panes = tabsHost.querySelectorAll("[data-pane]");
    for (var t = 0; t < tabs.length; t++) {
      tabs[t].addEventListener("click", function () {
        var name = this.getAttribute("data-tab");
        for (var a = 0; a < tabs.length; a++) {
          tabs[a].setAttribute("aria-selected", String(tabs[a] === this));
        }
        for (var b = 0; b < panes.length; b++) {
          panes[b].hidden = panes[b].getAttribute("data-pane") !== name;
        }
      });
    }
  }

  /* ---------- agent chat plays once when scrolled into view ---------- */
  var chat = doc.querySelector("[data-chat]");
  if (chat) {
    var rows = chat.querySelectorAll(".chat-row");
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      chat.classList.add("is-static");
    } else {
      var io = new IntersectionObserver(
        function (entries) {
          if (!entries[0].isIntersecting) return;
          io.disconnect();
          Array.prototype.forEach.call(rows, function (row, i) {
            window.setTimeout(function () {
              row.classList.add("is-on");
            }, 250 + i * 650);
          });
        },
        { threshold: 0.35 }
      );
      io.observe(chat);
    }
  }

  /* ---------- checkout ---------- */
  var msg = doc.getElementById("pay-msg");
  var say = function (text, ok) {
    if (!msg) return;
    msg.textContent = text;
    msg.classList.toggle("ok", !!ok);
  };
  var payButtons = doc.querySelectorAll("[data-pay]");
  var thanksPath = zh ? "/zh/thanks/" : "/thanks/";
  for (var p = 0; p < payButtons.length; p++) {
    payButtons[p].addEventListener("click", function () {
      var provider = this.getAttribute("data-pay");
      // afdian never redirects back, so it opens in a new tab while this tab waits for the key.
      var popup = provider === "afdian" ? window.open("", "_blank") : null;
      for (var q = 0; q < payButtons.length; q++) payButtons[q].disabled = true;
      say(zh ? "正在打开收银台…" : "Opening checkout…", true);
      fetch("/api/checkout", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: provider })
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok || !data.url) throw new Error(data.error || "checkout");
            return data;
          });
        })
        .then(function (data) {
          try {
            localStorage.setItem("te-order", data.order_id);
          } catch (e) {
            /* private mode: the order id is in the URL anyway */
          }
          if (popup) {
            popup.location.href = data.url;
            window.location.href = thanksPath + "?order=" + encodeURIComponent(data.order_id) + "&via=afdian";
          } else {
            window.location.href = data.url;
          }
        })
        .catch(function () {
          if (popup) popup.close();
          for (var q = 0; q < payButtons.length; q++) payButtons[q].disabled = false;
          say(
            zh
              ? "收银台暂时打不开，请稍后再试，或在 GitHub 提 issue 联系我们。"
              : "Checkout is unavailable right now. Try again in a minute, or open a GitHub issue."
          );
        });
    });
  }

  /* ---------- licence lookups (claim box + thanks page) ---------- */
  var activation = function (key) {
    return (
      key +
      "\n\n" +
      (zh ? "# 激活（任选其一）\n" : "# activate (either works)\n") +
      "tennis-evolution license activate " + key + "\n" +
      "export TENNIS_EVOLUTION_LICENSE=" + key
    );
  };

  var claimBtn = doc.getElementById("claim-btn");
  if (claimBtn) {
    claimBtn.addEventListener("click", function () {
      var input = doc.getElementById("claim-input");
      var box = doc.getElementById("claim-out");
      var no = (input.value || "").replace(/\D/g, "");
      box.hidden = false;
      if (no.length < 10) {
        box.textContent = zh ? "订单号是爱发电订单详情里那串数字。" : "Use the long number on the afdian order page.";
        return;
      }
      box.textContent = zh ? "查询中…" : "Looking it up…";
      fetch("/api/license?afdian=" + encodeURIComponent(no))
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          box.textContent =
            data.status === "paid"
              ? activation(data.key)
              : zh
                ? "没找到已支付的订单。刚付完的话等一分钟再试。"
                : "No paid order with that number yet. If you just paid, retry in a minute.";
        })
        .catch(function () {
          box.textContent = zh ? "查询失败，请稍后再试。" : "Lookup failed, try again shortly.";
        });
    });
  }

  var thanks = doc.querySelector("[data-thanks]");
  if (thanks) {
    var params = new URLSearchParams(window.location.search);
    var order = params.get("order");
    try {
      order = order || localStorage.getItem("te-order");
    } catch (e) {
      /* ignore */
    }
    var status = doc.getElementById("thanks-status");
    var keyBox = doc.getElementById("thanks-key");
    var done = doc.getElementById("thanks-done");
    var copyKey = doc.getElementById("thanks-copy");
    var tries = 0;
    var poll = function () {
      if (!order) {
        status.textContent = zh ? "没有找到订单号。用下面的爱发电订单号找回。" : "No order id found. Use the afdian lookup below.";
        return;
      }
      fetch("/api/license?order=" + encodeURIComponent(order))
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          if (data.status === "paid") {
            status.hidden = true;
            done.hidden = false;
            keyBox.textContent = data.key;
            copyKey.setAttribute("data-copy", data.key);
            var hint = thanks.querySelector(".pay-note");
            if (hint) hint.hidden = true;
            return;
          }
          tries += 1;
          if (tries > 450) {
            status.textContent = zh ? "还没收到付款通知。付完款后刷新本页，或用下面的订单号找回。" : "Payment not confirmed yet. Refresh after paying, or use the lookup below.";
            return;
          }
          window.setTimeout(poll, 2000);
        })
        .catch(function () {
          window.setTimeout(poll, 4000);
        });
    };
    poll();
  }
})();
