/*
 * The ACA gate. Marcus's page only.
 *
 * On 15 September Marcus told LinkedIn, and by extension everybody, that
 * he had passed all fifteen ACA exams, thanked his family, his friends and
 * his colleagues at KPMG for the three-year journey, and mentioned the
 * assistant manager job starting in October. The league's response is this
 * file: ten of the harder things an ICAEW paper actually asks, sat on his
 * own front door, before he is allowed at his squad again.
 *
 * Self-contained in the way feedback.js is - own markup, own styles - so
 * it drops onto one page without team.css or team.js knowing about it, and
 * so removing one script tag removes the whole joke.
 *
 * Nothing here is a real lock. The pages are static and public, the state
 * lives in this browser's localStorage, and the gate has a "later" door in
 * the corner that opens without an answer. It is a bit, not a password:
 * the point is the result, which goes to the same worker the roast box
 * uses and lands in data/roasts.json for the whole league to read.
 */
(function () {
  const OWNER = 299912;                 // Marcus, and nobody else
  const KEY = "aca.gate.v1";
  const SUGGEST = "https://schwaddy-cron.justinl-waddy.workers.dev/suggest";
  const PASS = 8;                       // ICAEW's own pass mark is 55%; the league is harder

  if (((window.TEAM || {}).me) !== OWNER) return;

  /* The paper. Professional and Advanced level material, four options,
     one defensible answer and a reason for it, because a quiz that only
     tells you that you are wrong is just a rude website. */
  const PAPER = [
    {
      topic: "IFRS 16 leases",
      q: `Kestrel Ltd leases a warehouse from 1 January 2026 for five years, paying
          &pound;20,000 annually in arrears. The rate implicit in the lease is 5% (five-year
          annuity factor 4.3295). Kestrel pays &pound;3,000 of initial direct costs and
          receives a &pound;1,000 cash incentive from the lessor. At what amount is the
          right-of-use asset initially recognised?`,
      opts: ["&pound;86,590", "&pound;88,590", "&pound;89,590", "&pound;100,000"],
      a: 1,
      why: `Liability first: 20,000 &times; 4.3295 = &pound;86,590. The asset is then that
            liability plus initial direct costs and <em>less</em> incentives received:
            86,590 + 3,000 &minus; 1,000 = &pound;88,590. Forgetting the incentive gives
            89,590, which is the answer three quarters of people pick.`,
      jab: "The incentive comes off. It is the first thing on the page and you walked past it.",
    },
    {
      topic: "IAS 12 deferred tax",
      q: `A building is carried at &pound;500,000, which includes a revaluation gain of
          &pound;80,000 recognised in the year. Its tax base is &pound;380,000 and the tax
          rate is 25%. What is recognised at the reporting date?`,
      opts: [
        "A deferred tax liability of &pound;30,000, all charged to profit or loss",
        "A deferred tax liability of &pound;30,000, of which &pound;20,000 is charged to other comprehensive income",
        "A deferred tax liability of &pound;20,000, charged entirely to other comprehensive income",
        "A deferred tax asset of &pound;30,000",
      ],
      a: 1,
      why: `The taxable temporary difference is 500,000 &minus; 380,000 = &pound;120,000, so
            the liability is &pound;30,000. Deferred tax follows the transaction it relates
            to: the &pound;80,000 revaluation went to OCI, so 80,000 &times; 25% =
            &pound;20,000 of the charge goes there too, and the remaining &pound;10,000 to
            profit or loss.`,
      jab: "Right number, wrong home. Backwards tracing is the entire examinable point.",
    },
    {
      topic: "corporation tax marginal relief",
      q: `Tarn Ltd has no associated companies, a 12-month accounting period and augmented
          profits (all trading) of &pound;100,000. Using a main rate of 25%, a small profits
          rate of 19%, limits of &pound;50,000 and &pound;250,000 and a standard fraction of
          3/200, what is its corporation tax liability?`,
      opts: ["&pound;19,000", "&pound;22,750", "&pound;23,500", "&pound;25,000"],
      a: 1,
      why: `Above the lower limit, so it is the main rate less marginal relief:
            25% &times; 100,000 = &pound;25,000, less 3/200 &times; (250,000 &minus; 100,000)
            = &pound;2,250. Liability &pound;22,750, an effective rate of 22.75%.`,
      jab: "Marginal relief. In a tax paper. Within touching distance of an assistant manager badge.",
    },
    {
      topic: "goodwill and the NCI",
      q: `Pike plc acquires 80% of Salmon Ltd for &pound;900,000 cash plus contingent
          consideration with an acquisition-date fair value of &pound;50,000. The
          non-controlling interest is measured at its fair value of &pound;200,000.
          Identifiable net assets at acquisition are &pound;950,000. What is goodwill?`,
      opts: ["&pound;150,000", "&pound;190,000", "&pound;200,000", "&pound;250,000"],
      a: 2,
      why: `Consideration 900,000 + 50,000, plus NCI at fair value 200,000, less net assets
            950,000 = &pound;200,000. Contingent consideration is measured at fair value on
            day one whether or not it is ever paid; dropping it gives &pound;150,000.`,
      jab: "Three years of group accounts and the contingent consideration still fell off the table.",
    },
    {
      topic: "ungearing a beta",
      q: `Hart plc has an equity beta of 1.6 and a debt to equity ratio of 1:2 by market
          value. Its debt can be assumed risk free and corporation tax is 25%. What is the
          asset (ungeared) beta?`,
      opts: ["1.07", "1.16", "1.20", "1.28"],
      a: 1,
      why: `&beta;<sub>a</sub> = &beta;<sub>e</sub> &times; E / (E + D(1 &minus; t))
            = 1.6 &times; 2 / (2 + 1 &times; 0.75) = 3.2 / 2.75 = 1.16. Ignoring the tax
            shield gives 1.07, which is the classic dropped mark.`,
      jab: "The tax relief on the debt is the only reason the formula has a bracket in it.",
    },
    {
      topic: "diluted EPS",
      q: `A company reports profit after tax of &pound;2,000,000 and has 5,000,000 &pound;1
          ordinary shares in issue throughout the year. It also has &pound;1,000,000 of 5%
          convertible loan notes, convertible into 500,000 ordinary shares. Corporation tax
          is 25%. What is diluted EPS?`,
      opts: ["36.4p", "37.0p", "37.3p", "40.0p"],
      a: 1,
      why: `On conversion the interest is saved, net of tax: 50,000 &times; 75% =
            &pound;37,500. Earnings &pound;2,037,500 over 5,500,000 shares = 37.0p. Adding
            back the interest gross gives 37.3p; 40.0p is basic EPS.`,
      jab: "The interest saved is post-tax. That is the whole adjustment, and it is one multiplication.",
    },
    {
      topic: "IFRS 15 allocation",
      q: `A contract bundles a machine (stand-alone selling price &pound;900) with two years
          of servicing (stand-alone selling price &pound;450) for a single price of
          &pound;1,200. Both are distinct performance obligations. How much of the
          transaction price is allocated to the machine?`,
      opts: ["&pound;750", "&pound;800", "&pound;900", "&pound;1,200"],
      a: 1,
      why: `The discount is allocated across both obligations in proportion to stand-alone
            selling prices: 1,200 &times; 900/1,350 = &pound;800, leaving &pound;400 for the
            servicing to be recognised over two years. Allocating the whole discount to the
            service is the step-four error the examiner is fishing for.`,
      jab: "Step four of five. There are only five.",
    },
    {
      topic: "ISA 570 going concern",
      q: `The auditor concludes that a material uncertainty related to going concern exists
          and that the financial statements disclose it adequately. What is the effect on the
          auditor's report?`,
      opts: [
        "A qualified opinion, with the uncertainty described in the basis for qualified opinion",
        "An adverse opinion, because the going concern basis is in doubt",
        "An unmodified opinion, with a separate 'Material Uncertainty Related to Going Concern' section",
        "An unmodified opinion, with the uncertainty raised in an Emphasis of Matter paragraph",
      ],
      a: 2,
      why: `Adequate disclosure means the financial statements are not misstated, so the
            opinion is unmodified. ISA (UK) 570 requires a separate section under its own
            heading - not an Emphasis of Matter, which is exactly what this used to be
            before the standard was revised.`,
      jab: "Audit. Your actual day job. The one on the LinkedIn headline.",
    },
    {
      topic: "fee dependency",
      q: `An audit firm expects the total fees from one listed audit client to regularly
          exceed 10% of the firm's annual fee income. Under the FRC Ethical Standard, what
          must the firm do?`,
      opts: [
        "Disclose the position to those charged with governance and continue as auditor",
        "Not act as auditor of that entity",
        "Continue, provided an engagement quality control review is carried out",
        "Nothing: the threshold for a listed client is 15%",
      ],
      a: 1,
      why: `For a public interest entity the 10% line is a prohibition, not a safeguard: the
            firm shall not act. Disclosure to the Ethics Partner and those charged with
            governance, plus an engagement quality control review, is what the 5% to 10%
            band requires. 15% is the non-PIE figure.`,
      jab: "Independence. The bit of the job that gets firms fined rather than embarrassed.",
    },
    {
      topic: "IAS 37 restructuring",
      q: `On 20 December the board resolves to close a division and approves a detailed formal
          plan. Nothing is announced to the employees affected, or anyone else, until
          15 January. The year end is 31 December. What is the treatment at 31 December?`,
      opts: [
        "Provide in full for the cost of the restructuring",
        "Provide for redundancy costs only, as those are the sole obligating event",
        "No provision; disclose as a non-adjusting event after the reporting period if material",
        "No provision; disclose the restructuring as a contingent liability",
      ],
      a: 2,
      why: `A constructive obligation needs a detailed formal plan <em>and</em> a valid
            expectation raised in those affected, by starting to implement it or announcing
            its main features. On 31 December only half of that existed, so there is no
            obligation to provide for - it is a non-adjusting event under IAS 10.`,
      jab: "A board minute nobody has been told about is a plan, not a liability.",
    },
  ];

  const css = `
.acaback{position:fixed;inset:0;z-index:90;background:var(--bg,#0F1A31);overflow:auto;
padding:0 0 40px;font:400 15px/1.55 "IBM Plex Sans",system-ui,sans-serif;color:var(--ink,#E9EEF8)}
.acawrap{max-width:660px;margin:0 auto;padding:18px 14px}
.acalock{border:1px solid var(--red,#D95757);border-radius:12px;background:var(--panel,#16233F);
padding:14px;margin-bottom:12px}
.acalock h2{font:600 26px "Barlow Condensed",system-ui;letter-spacing:.04em;text-transform:uppercase;
color:var(--red,#D95757);margin-bottom:2px}
.acalock p{color:var(--dim,#8FA0C4);font-size:13px;margin-top:6px}
.acakick{font:600 11px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.06em;
text-transform:uppercase;color:var(--amber,#E8A13C)}
.acacard{background:var(--panel,#16233F);border:1px solid var(--line,#26365C);border-radius:12px;
padding:14px;margin-bottom:10px}
.acabar{height:4px;background:var(--panel2,#1C2B4D);border-radius:3px;overflow:hidden;margin:10px 0 12px}
.acabar i{display:block;height:100%;background:var(--blue,#4A7FE0);transition:width .25s}
.acastep{font:600 11px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.06em;
text-transform:uppercase;color:var(--dim,#8FA0C4);display:flex;gap:10px}
.acastep b{color:var(--ink,#E9EEF8);font-weight:600}
.acastep .t{margin-left:auto;color:var(--blue,#4A7FE0)}
.acaq{font-size:15px;line-height:1.6;margin:4px 0 12px}
.acaopt{display:block;width:100%;text-align:left;background:var(--panel2,#1C2B4D);
color:var(--ink,#E9EEF8);border:1px solid var(--line,#26365C);border-radius:9px;
padding:10px 12px;margin-bottom:7px;font:400 14px/1.45 "IBM Plex Sans",system-ui;cursor:pointer;
display:flex;gap:10px;align-items:baseline}
.acaopt:hover:not(:disabled){border-color:var(--blue,#4A7FE0)}
.acaopt:disabled{cursor:default}
.acaopt span.k{font:600 11px "IBM Plex Mono",ui-monospace,monospace;color:var(--dim,#8FA0C4);
padding-top:2px}
.acaopt.right{border-color:var(--green,#5FBF77);background:rgba(95,191,119,.12)}
.acaopt.right span.k,.acaopt.wrong span.k{color:var(--ink,#E9EEF8)}
.acaopt.wrong{border-color:var(--red,#D95757);background:rgba(217,87,87,.12)}
.acaopt.faded{opacity:.5}
.acawhy{border-left:2px solid var(--line,#26365C);padding:2px 0 2px 11px;margin:10px 0 0;
color:var(--dim,#8FA0C4);font-size:13px;line-height:1.6}
.acawhy b.v{display:block;font:600 12px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.05em;
text-transform:uppercase;margin-bottom:4px}
.acawhy b.v.ok{color:var(--green,#5FBF77)}
.acawhy b.v.no{color:var(--red,#D95757)}
.acajab{color:var(--amber,#E8A13C);display:block;margin-top:6px;font-style:italic}
.acafoot{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
.acabtn{background:var(--panel2,#1C2B4D);border:1px solid var(--blue,#4A7FE0);border-radius:8px;
color:var(--ink,#E9EEF8);padding:8px 14px;cursor:pointer;
font:600 12px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.03em}
.acabtn:hover{background:var(--blue,#4A7FE0)}
.acabtn:disabled{opacity:.5;cursor:default}
.acabtn.ghost{border-color:var(--line,#26365C);color:var(--dim,#8FA0C4)}
.acabtn.ghost:hover{background:var(--panel2,#1C2B4D);color:var(--ink,#E9EEF8)}
.acalater{background:none;border:none;color:var(--dim,#8FA0C4);text-decoration:underline;
cursor:pointer;font:400 12px "IBM Plex Sans",system-ui;padding:0;margin-left:auto}
.acalater:hover{color:var(--ink,#E9EEF8)}
.acascore{font:600 52px "Barlow Condensed",system-ui;letter-spacing:.02em;line-height:1}
.acascore em{font-style:normal;color:var(--dim,#8FA0C4);font-size:28px}
.acaverdict{font:600 17px "Barlow Condensed",system-ui;letter-spacing:.04em;text-transform:uppercase;
margin:6px 0 2px}
.acarow{display:flex;gap:9px;align-items:baseline;padding:6px 2px;
border-bottom:1px solid var(--line,#26365C);font-size:13px}
.acarow:last-child{border-bottom:none}
.acarow .mk{font:600 11px "IBM Plex Mono",ui-monospace,monospace;padding:2px 6px;border-radius:4px;
color:#0F1A31}
.acarow .mk.ok{background:var(--green,#5FBF77)}
.acarow .mk.no{background:var(--red,#D95757)}
.acaquote{border:1px solid var(--amber,#E8A13C);border-radius:9px;background:rgba(232,161,60,.08);
padding:11px 12px;margin:10px 0 0;font-size:14px;line-height:1.55}
.acaquote b.h{display:block;font:600 11px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.06em;
text-transform:uppercase;color:var(--amber,#E8A13C);margin-bottom:5px}
.acasend{font-size:12px;color:var(--dim,#8FA0C4);margin-top:8px}
.acasend.bad{color:var(--amber,#E8A13C)}
.acasend.ok{color:var(--green,#5FBF77)}
/* the permanent notice on the News tab, dressed as a feed item */
.acanews{background:var(--panel,#16233F);border:1px solid var(--amber,#E8A13C);border-radius:10px;
padding:10px 12px;margin:8px 0;font:400 14px/1.55 "IBM Plex Sans",system-ui,sans-serif}
.acanews .acabadge{font:600 10px "IBM Plex Mono",ui-monospace,monospace;padding:2px 6px;border-radius:4px;
background:var(--amber,#E8A13C);color:#0F1A31;margin-right:7px}
.acanews .acatext{font-size:14px;line-height:1.55}
.acanews .acaline{display:flex;gap:9px;align-items:center;margin-top:9px;flex-wrap:wrap}
@media(max-width:520px){.acascore{font-size:42px}}
`;

  /* ---- what the browser remembers ----
   * One object: whether the gate is open, and the last sitting. It is
   * localStorage, so clearing it is a right-click away - which is fine,
   * because the score that matters was posted to the league, not kept here.
   */
  function state() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null") || {}; }
    catch { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private window */ }
  }

  const esc = x => String(x == null ? "" : x)
    .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // The paper's own text carries entities and a little markup on purpose
  // (&pound;, <sub>, <em>), so it is written here rather than escaped.
  const strip = h => String(h).replace(/<[^>]*>/g, "").replace(/&pound;/g, "£")
    .replace(/&minus;/g, "-").replace(/&times;/g, "x").replace(/&amp;/g, "&")
    .replace(/\s+/g, " ").trim();

  const LETTER = ["A", "B", "C", "D"];

  /* The line that goes to the league. Same endpoint as the roast box, so
     it lands in data/roasts.json and gets read out on a matchday like any
     other suggestion - from Marcus, about Marcus, which is the joke. */
  function roastFor(score, wrong) {
    const bands = [
      [10, "Full marks, which he will be insufferable about until October at the earliest. The qualification is safe. The personality is not."],
      [8, "Short of the man who told LinkedIn he had passed all fifteen, and beaten on his own specialist subject by a fantasy football website."],
      [5, "Referred. Three years, fifteen papers, one green tick emoji, and the league had to mark him down on the bits he does for a living."],
      [3, "Failed. ICAEW exam-qualified, assistant manager from October, and he cannot allocate a transaction price across two performance obligations."],
      [0, "Somebody check that certificate. Fifteen exams, a three-year journey, a thank-you to his family, and he cannot do goodwill with the NCI at fair value."],
    ];
    const line = (bands.find(b => score >= b[0]) || bands[4])[1];
    let t = `Marcus sat ten ACA questions on his own FPL page and scored ${score}/10. ${line}`;
    if (wrong.length) {
      const topics = wrong.slice(0, 3).map(i => PAPER[i].topic).join(", ");
      t += ` Fell over on: ${topics}${wrong.length > 3 ? ", and more" : ""}.`;
    }
    return t.length > 480 ? t.slice(0, 477) + "..." : t;
  }
  function verdictFor(score) {
    if (score === 10) return ["Passed", "var(--green,#5FBF77)"];
    if (score >= PASS) return ["Passed, narrowly", "var(--green,#5FBF77)"];
    if (score >= 5) return ["Referred", "var(--amber,#E8A13C)"];
    return ["Failed", "var(--red,#D95757)"];
    // the bands in roastFor() are the same four, deliberately
  }

  function post(text, done) {
    fetch(SUGGEST, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from: OWNER, from_name: "Marcus", about: OWNER, about_name: "Marcus", text,
      }),
    })
      .then(r => r.json().then(j => ({ ok: r.ok, j })).catch(() => ({ ok: r.ok, j: null })))
      .then(({ ok, j }) => {
        if (!ok || (j && j.error)) throw new Error((j && j.error) || "could not file that");
        done(null);
      })
      .catch(e => done(e.message || String(e)));
  }

  /* ---- the gate ---- */
  let back = null, idx = 0, answers = [];

  function open(resit) {
    if (back) return;
    idx = 0; answers = [];
    back = document.createElement("div");
    back.className = "acaback";
    back.innerHTML = `<div class="acawrap">
      <div class="acalock">
        <div class="acakick">${resit ? "Voluntary re-sit" : "Account locked &middot; verification required"}</div>
        <h2>ACA verification</h2>
        <div>${resit
          ? `You asked for this one. Ten questions, same paper, and the result goes to the league again.`
          : `Marcus, your squad, your league table and your waiver claims are behind ten questions.
             You went on LinkedIn and told several hundred people you had passed all fifteen ACA
             exams, thanked your family, your friends and your colleagues at KPMG, and mentioned
             the assistant manager job starting in October. The league has one question about that,
             and then nine more.`}</div>
        <p>Multiple choice, no marks back for working, no calculator anybody can see.
           The score is published whatever it is.</p>
        <div class="acafoot">${resit ? "" :
          `<button class="acalater" id="acalater">Let me in, I will sit it later</button>`}</div>
      </div>
      <div id="acabody"></div>
    </div>`;
    document.body.appendChild(back);
    document.body.style.overflow = "hidden";
    // The door in the corner. It opens the account without an answer and
    // leaves the notice on the News tab saying so, which is the point: a
    // page nobody can get into is a broken page, not a joke.
    const later = document.getElementById("acalater");
    if (later) later.addEventListener("click", () => { save(Object.assign(state(), { open: true })); close(); });
    question();
  }

  function close() {
    if (!back) return;
    back.remove(); back = null;
    document.body.style.overflow = "";
    notice();
  }

  function question() {
    const q = PAPER[idx];
    const body = document.getElementById("acabody");
    body.innerHTML = `<div class="acacard">
      <div class="acastep"><b>Question ${idx + 1}</b> of ${PAPER.length}
        <span class="t">${esc(q.topic)}</span></div>
      <div class="acabar"><i style="width:${(idx / PAPER.length) * 100}%"></i></div>
      <div class="acaq">${q.q}</div>
      <div id="acaopts">${q.opts.map((o, i) =>
        `<button class="acaopt" data-i="${i}"><span class="k">${LETTER[i]}</span>
          <span>${o}</span></button>`).join("")}</div>
      <div id="acaafter"></div>
    </div>`;
    body.querySelectorAll(".acaopt").forEach(b =>
      b.addEventListener("click", () => answer(+b.dataset.i)));
  }

  function answer(pick) {
    const q = PAPER[idx];
    const right = pick === q.a;
    answers.push(pick);
    document.querySelectorAll("#acaopts .acaopt").forEach(b => {
      const i = +b.dataset.i;
      b.disabled = true;
      if (i === q.a) b.classList.add("right");
      else if (i === pick) b.classList.add("wrong");
      else b.classList.add("faded");
    });
    const last = idx === PAPER.length - 1;
    document.getElementById("acaafter").innerHTML = `
      <div class="acawhy">
        <b class="v ${right ? "ok" : "no"}">${right ? "Correct" : `Wrong &middot; the answer is ${LETTER[q.a]}`}</b>
        ${q.why}
        ${right ? "" : `<span class="acajab">${q.jab}</span>`}
      </div>
      <div class="acafoot">
        <button class="acabtn" id="acanext">${last ? "See the damage" : "Next question"}</button>
      </div>`;
    document.getElementById("acanext").addEventListener("click", () => {
      if (last) { results(); } else { idx++; question(); }
    });
    // A score of nine or ten out of ten deserves no suspense; the rest get
    // the running total kept off the screen until the end on purpose.
  }

  function results() {
    const wrong = answers.map((p, i) => (p === PAPER[i].a ? -1 : i)).filter(i => i >= 0);
    const score = PAPER.length - wrong.length;
    const [verdict, colour] = verdictFor(score);
    const text = roastFor(score, wrong);
    const sitting = { done: true, score, wrong, text, ts: new Date().toISOString() };
    save(Object.assign(state(), { open: true, last: sitting }));

    document.getElementById("acabody").innerHTML = `<div class="acacard">
      <div class="acastep"><b>Result</b><span class="t">${esc(new Date().toLocaleDateString("en-GB",
        { day: "numeric", month: "long", year: "numeric" }))}</span></div>
      <div class="acascore" style="color:${colour}">${score}<em>/10</em></div>
      <div class="acaverdict" style="color:${colour}">${esc(verdict)}</div>
      <div class="acawhy" style="border-color:${colour}">${score >= PASS
        ? "The account is open. Nobody is impressed, but it is open."
        : "The account is open anyway, because locking a man out of his own fantasy football team over a beta is not proportionate. The score still goes up."}</div>
      <div style="margin-top:12px">${PAPER.map((q, i) => {
        const ok = answers[i] === q.a;
        return `<div class="acarow"><span class="mk ${ok ? "ok" : "no"}">${ok ? "OK" : "X"}</span>
          <span style="flex:1">${esc(q.topic)}</span>
          <span class="acastep">${ok ? "" : `you said ${LETTER[answers[i]]} &middot; `}answer ${LETTER[q.a]}</span></div>`;
      }).join("")}</div>
      <div class="acaquote"><b class="h">Filed to the league</b>${esc(text)}</div>
      <div class="acasend" id="acasend">Sending it to the roast pile&hellip;</div>
      <div class="acafoot">
        <button class="acabtn" id="acaopen">Unlock the account</button>
        <button class="acabtn ghost" id="acaagain">Sit it again</button>
      </div>
    </div>`;

    const s = document.getElementById("acasend");
    post(text, err => {
      if (err) {
        s.className = "acasend bad";
        s.textContent = `Could not file it (${err}). It is still on this page, and the league has "Suggest a roast".`;
      } else {
        s.className = "acasend ok";
        s.textContent = "Filed. It goes into data/roasts.json with everything else and gets read out on a matchday.";
      }
    });
    document.getElementById("acaopen").addEventListener("click", close);
    document.getElementById("acaagain").addEventListener("click", () => { close(); open(true); });
  }

  /* ---- the notice that stays behind on the News tab ----
   * team.js rewrites the whole of #news on every render - a filter chip, a
   * fresh public.json - so this cannot be appended once and forgotten. It
   * is put back whenever it goes missing. Re-inserting is itself a
   * mutation, but the next callback finds the card present and stops, so
   * there is no loop.
   */
  function noticeHTML() {
    const st = state();
    if (st.last) {
      const [verdict] = verdictFor(st.last.score);
      return `<span class="acabadge">ACA</span><span class="acatext"><b>${st.last.score}/10 &middot;
        ${esc(verdict)}.</b> ${esc(st.last.text)}</span>
        <div class="acaline"><button class="acabtn ghost" data-aca="sit">Sit it again</button>
        <span class="acasend">Sat ${esc(String(st.last.ts).slice(0, 10))}. The result was filed to the league.</span></div>`;
    }
    return `<span class="acabadge">ACA</span><span class="acatext"><b>Verification outstanding.</b>
      You waved the ten questions away rather than answering them, which the league has
      noted. The paper is still here.</span>
      <div class="acaline"><button class="acabtn" data-aca="sit">Sit the paper</button></div>`;
  }
  function notice() {
    const sec = document.getElementById("news");
    if (!sec) return;
    const st = state();
    const sig = st.last ? `${st.last.ts}:${st.last.score}` : "none";
    let d = document.getElementById("acanotice");
    if (d && d.dataset.sig === sig) return;      // already saying the right thing
    if (!d) {
      d = document.createElement("div");
      d.className = "acanews"; d.id = "acanotice";
      sec.insertBefore(d, sec.firstChild);
    }
    d.dataset.sig = sig;
    d.innerHTML = noticeHTML();
    d.querySelectorAll("[data-aca]").forEach(b =>
      b.addEventListener("click", () => open(true)));
  }

  function mount() {
    document.head.appendChild(Object.assign(document.createElement("style"), { textContent: css }));
    const sec = document.getElementById("news");
    if (sec && window.MutationObserver) {
      new MutationObserver(() => notice()).observe(sec, { childList: true });
    }
    if (state().open) { notice(); return; }
    open(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
