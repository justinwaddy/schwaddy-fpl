/*
 * "Something is broken" / "it should do this", from any page.
 *
 * Self-contained on purpose: it brings its own markup and its own styles,
 * so it drops into the six manager pages, the engine dashboard and the
 * landing page alike without any of them agreeing on a stylesheet. A
 * manager's page names its owner and the box files under him; the landing
 * page cannot know, so it asks rather than guessing and putting Ed's bug
 * report under Justin's name.
 *
 * It posts to the same worker endpoint as the roast box, because that one
 * is already deployed and already knows the six entry ids. The kind is
 * carried as a "[bug]" or "[idea]" prefix on the text, which the workflow
 * strips and routes on - a new endpoint would have meant another manual
 * redeploy in the Cloudflare dashboard for no behaviour that this does not
 * already have. Nothing here is trusted downstream: the workflow scrubs
 * and caps the text the same way it does a roast.
 */
(function () {
  const URL = "https://schwaddy-cron.justinl-waddy.workers.dev/suggest";
  const MAX = 460;                    // the worker's own cap is 500, less the prefix
  const ENTRIES = {
    45811: "Edward", 282287: "Small Ben", 299912: "Marcus",
    363607: "Big Ben", 372099: "Justin", 421435: "Robert",
  };
  // A manager's page knows whose it is. The landing page does not, and
  // guessing there would file Ed's bug report under Justin's name, so it
  // asks instead.
  const known = (window.TEAM && window.TEAM.me) || window.FEEDBACK_FROM || null;

  const css = `
.fbbtn{position:absolute;top:14px;right:12px;z-index:5;background:var(--panel2,#16213c);
color:var(--ink,#e9eef8);border:1px solid var(--line,#26355a);border-radius:8px;
padding:5px 10px;font:600 11px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.03em;
text-transform:uppercase;cursor:pointer;line-height:1.4}
.fbbtn:hover{border-color:var(--blue,#5b8dff)}
.fbback{position:fixed;inset:0;background:rgba(6,11,24,.72);z-index:60;display:flex;
align-items:flex-start;justify-content:center;padding:24px 12px;overflow:auto}
.fbbox{background:var(--panel,#101a30);border:1px solid var(--line,#26355a);border-radius:12px;
padding:16px;max-width:520px;width:100%;color:var(--ink,#e9eef8);
font:400 14px "IBM Plex Sans",system-ui}
.fbbox h3{font:600 20px "Barlow Condensed",system-ui;letter-spacing:.03em;text-transform:uppercase;
margin:0 0 2px}
.fbbox p{color:var(--dim,#8fa3c4);font-size:12px;margin:0 0 12px;line-height:1.5}
.fbrow{display:flex;gap:8px;margin-bottom:10px}
.fbkind{flex:1;background:var(--panel2,#16213c);color:var(--dim,#8fa3c4);
border:1px solid var(--line,#26355a);border-radius:8px;padding:7px;cursor:pointer;
font:600 11px "IBM Plex Mono",ui-monospace,monospace;text-transform:uppercase}
.fbkind.on{border-color:var(--blue,#5b8dff);color:var(--blue,#5b8dff)}
.fbwho{width:100%;margin-bottom:10px;background:var(--panel2,#16213c);color:var(--ink,#e9eef8);
border:1px solid var(--line,#26355a);border-radius:8px;padding:7px;
font:600 12px "IBM Plex Mono",ui-monospace,monospace}
.fbbox textarea{width:100%;min-height:110px;background:var(--panel2,#16213c);
color:var(--ink,#e9eef8);border:1px solid var(--line,#26355a);border-radius:8px;padding:9px;
font:400 14px "IBM Plex Sans",system-ui;resize:vertical;box-sizing:border-box}
.fbfoot{display:flex;align-items:center;gap:10px;margin-top:10px}
.fbmsg{color:var(--dim,#8fa3c4);font-size:12px;flex:1}
.fbmsg.bad{color:var(--amber,#f5b544)}
.fbsend,.fbx{background:var(--panel2,#16213c);color:var(--ink,#e9eef8);
border:1px solid var(--line,#26355a);border-radius:8px;padding:6px 12px;cursor:pointer;
font:600 12px "IBM Plex Mono",ui-monospace,monospace}
.fbsend{border-color:var(--blue,#5b8dff);color:var(--blue,#5b8dff)}
.fbsend:disabled{opacity:.5;cursor:default}
@media(max-width:520px){.fbbtn{position:static;display:block;margin:8px 0 0}}
`;

  let kind = "bug";

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function open() {
    const back = el("div", "fbback");
    const box = el("div", "fbbox");
    box.innerHTML = `<h3>Something wrong, or an idea?</h3>
      <p>Goes on a list we go through at the end of the week. Say which page and what you
      were doing - that is usually the whole of the bug report.</p>
      <div class="fbrow">
        <button class="fbkind on" data-k="bug">Something is broken</button>
        <button class="fbkind" data-k="idea">It should do this</button>
      </div>
      ${known ? "" : `<select class="fbwho"><option value="">Who are you?</option>${
        Object.entries(ENTRIES).map(([id, nm]) =>
          `<option value="${id}">${nm}</option>`).join("")}</select>`}
      <textarea maxlength="${MAX}" placeholder="On the Live tab, the ticker..."></textarea>
      <div class="fbfoot"><span class="fbmsg"></span>
        <button class="fbx">Close</button><button class="fbsend">Send</button></div>`;
    back.appendChild(box);
    document.body.appendChild(back);

    const ta = box.querySelector("textarea");
    const msg = box.querySelector(".fbmsg");
    const send = box.querySelector(".fbsend");
    kind = "bug";
    box.querySelectorAll(".fbkind").forEach(b => b.addEventListener("click", () => {
      kind = b.dataset.k;
      box.querySelectorAll(".fbkind").forEach(x => x.classList.toggle("on", x === b));
    }));
    const close = () => back.remove();
    box.querySelector(".fbx").addEventListener("click", close);
    back.addEventListener("click", e => { if (e.target === back) close(); });
    setTimeout(() => ta.focus(), 30);

    send.addEventListener("click", () => {
      const sel = box.querySelector(".fbwho");
      const from = known || (sel && +sel.value) || null;
      if (!from) { msg.className = "fbmsg bad"; msg.textContent = "Say who you are first."; return; }
      const text = ta.value.trim().replace(/\s+/g, " ");
      if (text.length < 4) { msg.className = "fbmsg bad"; msg.textContent = "Say a bit more than that."; return; }
      msg.className = "fbmsg"; msg.textContent = "Sending…";
      send.disabled = true;
      fetch(URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from, text: `[${kind}] ${text}` }),
      })
        .then(r => r.json().then(j => ({ ok: r.ok, j })))
        .then(({ ok, j }) => {
          if (!ok || (j && j.error)) throw new Error((j && j.error) || "could not send that");
          msg.textContent = "Filed. Thanks.";
          setTimeout(close, 1100);
        })
        .catch(e => {
          msg.className = "fbmsg bad";
          msg.textContent = String(e.message || e);
          send.disabled = false;
        });
    });
  }

  function mount() {
    if (document.querySelector(".fbbtn")) return;
    document.head.appendChild(el("style", null, css));
    const head = document.querySelector("header") || document.body;
    if (getComputedStyle(head).position === "static") head.style.position = "relative";
    const btn = el("button", "fbbtn", "Bug or idea");
    btn.title = known ? `Report something broken, or suggest a feature (as ${ENTRIES[known]})`
      : "Report something broken, or suggest a feature";
    btn.addEventListener("click", open);
    head.appendChild(btn);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
