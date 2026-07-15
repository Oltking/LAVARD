"""TheHouse landing page — the public face, served at GET /.

Deliberately monochrome: black ink on white marble, sections inverting to white on black.
No accent color anywhere; hierarchy is carried by type, rules, and inversion. Self-contained
(system font stacks, inline SVG seal, no external assets)."""

from __future__ import annotations

_CSS = """
:root{
  --paper:#FFFFFF; --ink:#0A0A0A; --grey:#6E6E6E; --hair:#E3E3E3; --hair-dark:#2A2A2A;
  --display:"Didot","Bodoni 72","Bodoni MT","Iowan Old Style",Georgia,serif;
  --body:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
:root[data-theme="dark"]{ --paper:#0A0A0A; --ink:#FFFFFF; --grey:#9A9A9A; --hair:#2A2A2A; --hair-dark:#E3E3E3; }
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){ --paper:#0A0A0A; --ink:#FFFFFF; --grey:#9A9A9A; --hair:#2A2A2A; --hair-dark:#E3E3E3; }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%; scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){ html{scroll-behavior:auto} }
body{margin:0; background:var(--paper); color:var(--ink); font-family:var(--body); font-size:16px; line-height:1.6}
.wrap{max-width:1080px; margin:0 auto; padding:0 28px}

.eyebrow{font-family:var(--mono); font-size:.66rem; letter-spacing:.32em; text-transform:uppercase; color:var(--grey)}
.rules-double{border:0; border-top:1px solid var(--ink); position:relative; margin:0}
.rules-double::after{content:""; display:block; border-top:1px solid var(--ink); margin-top:3px}

/* ---- buttons: black on white, white on black, always sharp ---- */
.btn{
  display:inline-block; font-family:var(--mono); font-size:.72rem; letter-spacing:.22em;
  text-transform:uppercase; text-decoration:none; padding:15px 30px; border:1px solid var(--ink);
  background:var(--ink); color:var(--paper); transition:background .18s ease, color .18s ease;
}
.btn:hover{background:var(--paper); color:var(--ink)}
.btn.ghost{background:transparent; color:var(--ink)}
.btn.ghost:hover{background:var(--ink); color:var(--paper)}
.btn:focus-visible{outline:2px solid var(--ink); outline-offset:3px}
.on-ink .btn{border-color:var(--paper); background:var(--paper); color:var(--ink)}
.on-ink .btn:hover{background:transparent; color:var(--paper)}

/* ---- top bar ---- */
.topbar{display:flex; align-items:center; justify-content:space-between; gap:20px; padding:22px 0}
.brand{display:flex; align-items:center; gap:14px; text-decoration:none; color:var(--ink)}
.brand .mark{width:44px; height:44px}
.brand .name{font-family:var(--display); font-size:1.25rem; letter-spacing:.12em; text-transform:uppercase}
nav.links{display:flex; gap:28px; align-items:center}
nav.links a{font-family:var(--mono); font-size:.66rem; letter-spacing:.22em; text-transform:uppercase; color:var(--grey); text-decoration:none}
nav.links a:hover{color:var(--ink)}
@media (max-width:640px){ nav.links a:not(.btn){display:none} }
nav.links .btn{padding:11px 20px}

/* ---- hero ---- */
.hero{text-align:center; padding:72px 0 84px}
.hero .seal{width:104px; height:104px; margin:0 auto 34px; display:block}
h1.title{
  font-family:var(--display); font-weight:400; margin:26px 0 0;
  font-size:clamp(3.2rem,11vw,7.5rem); line-height:.98; letter-spacing:.08em; text-transform:uppercase; text-wrap:balance;
}
h1.title .the{display:block; font-size:.32em; letter-spacing:.58em; margin-bottom:14px; color:var(--grey)}
.hero .sub{
  font-family:var(--display); font-size:clamp(1.05rem,2.6vw,1.45rem); font-style:italic;
  color:var(--grey); max-width:30em; margin:30px auto 0; text-wrap:balance;
}
.hero .cta{display:flex; gap:14px; justify-content:center; flex-wrap:wrap; margin-top:44px}
@media (prefers-reduced-motion: no-preference){
  .hero > *{opacity:0; transform:translateY(10px); animation:settle .7s cubic-bezier(.2,.6,.2,1) forwards}
  .hero .seal{animation-delay:.05s}.hero h1{animation-delay:.15s}
  .hero .sub{animation-delay:.3s}.hero .cta{animation-delay:.45s}
  @keyframes settle{to{opacity:1; transform:none}}
}

/* ---- colonnade divider ---- */
.colonnade{height:56px; border-block:1px solid var(--ink); background:
  repeating-linear-gradient(90deg, transparent 0 26px, var(--ink) 26px 30px) center/auto 32px no-repeat border-box;
  background-size:100% 32px; background-position:center;
}

/* ---- inverted stat band ---- */
.band{background:var(--ink); color:var(--paper)}
.band .wrap{display:grid; grid-template-columns:repeat(4,1fr); gap:0}
.stat{padding:42px 26px; text-align:center; border-left:1px solid var(--hair-dark)}
.stat:first-child{border-left:0}
.stat .n{font-family:var(--display); font-size:clamp(1.9rem,4.4vw,3rem); line-height:1; display:block}
.stat .l{font-family:var(--mono); font-size:.62rem; letter-spacing:.24em; text-transform:uppercase; color:color-mix(in srgb, var(--paper) 62%, var(--ink)); display:block; margin-top:12px}
@media (max-width:760px){ .band .wrap{grid-template-columns:1fr 1fr} .stat:nth-child(3){border-left:0} .stat{border-top:1px solid var(--hair-dark)} .stat:nth-child(-n+2){border-top:0} }

/* ---- articles ---- */
.section{padding:88px 0}
.section-head{text-align:center; margin-bottom:56px}
.section-head h2{font-family:var(--display); font-weight:400; font-size:clamp(1.7rem,4vw,2.5rem); letter-spacing:.1em; text-transform:uppercase; margin:16px 0 0}
.articles{display:grid; grid-template-columns:repeat(3,1fr); gap:0; border:1px solid var(--ink)}
.article{padding:44px 36px 48px; border-left:1px solid var(--ink)}
.article:first-child{border-left:0}
.article .num{font-family:var(--display); font-size:2.1rem; display:block}
.article h3{font-family:var(--mono); font-size:.7rem; letter-spacing:.3em; text-transform:uppercase; margin:14px 0 18px; border-top:1px solid var(--hair); padding-top:16px}
.article p{margin:0; color:var(--grey); font-size:.92rem}
.article p b{color:var(--ink); font-weight:600}
@media (max-width:820px){ .articles{grid-template-columns:1fr} .article{border-left:0; border-top:1px solid var(--ink)} .article:first-child{border-top:0} }

/* ---- ledger table ---- */
.ledger{border:1px solid var(--ink); max-width:760px; margin:0 auto; overflow-x:auto}
table.math{width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; min-width:520px}
table.math th{font-family:var(--mono); font-size:.62rem; letter-spacing:.22em; text-transform:uppercase; font-weight:400; color:var(--grey); padding:16px 22px; border-bottom:1px solid var(--ink); text-align:right}
table.math th:first-child{text-align:left}
table.math td{font-family:var(--mono); font-size:.9rem; padding:15px 22px; border-bottom:1px solid var(--hair); text-align:right}
table.math td:first-child{text-align:left; font-family:var(--body); color:var(--grey)}
table.math tr:last-child td{border-bottom:0}
table.math tr.total td{border-top:1px solid var(--ink); font-weight:700}
.foot-note{text-align:center; font-family:var(--mono); font-size:.66rem; letter-spacing:.18em; text-transform:uppercase; color:var(--grey); margin-top:26px}

/* ---- house rules (inverted) ---- */
.on-ink{background:var(--ink); color:var(--paper)}
.on-ink .section-head h2{color:var(--paper)}
.rules{max-width:680px; margin:0 auto; list-style:none; padding:0; counter-reset:rule}
.rules li{
  counter-increment:rule; display:flex; gap:26px; align-items:baseline;
  padding:24px 4px; border-top:1px solid var(--hair-dark); font-family:var(--display);
  font-size:clamp(1rem,2.2vw,1.25rem);
}
.rules li:first-child{border-top:0}
.rules li::before{content:counter(rule,upper-roman) "."; font-family:var(--mono); font-size:.75rem; letter-spacing:.2em; color:color-mix(in srgb, var(--paper) 55%, var(--ink)); min-width:2.6em}
.on-ink .cta{display:flex; justify-content:center; margin-top:56px}

/* ---- footer ---- */
footer{padding:56px 0 72px}
.foot{display:flex; align-items:center; justify-content:space-between; gap:18px; flex-wrap:wrap; margin-top:26px}
.foot .fine{font-family:var(--mono); font-size:.62rem; letter-spacing:.2em; text-transform:uppercase; color:var(--grey)}
"""

# Classic seal layout, two arcs on the same ring:
# - top arc runs clockwise (starts at the bottom, startOffset 50% = 12 o'clock) so
#   THEHOUSE sits due north, upright;
# - bottom arc runs counterclockwise (starts at the top, startOffset 50% = 6 o'clock) so
#   AGENT SERVICE PROVIDER reads left-to-right along the bottom, upright;
# - dots at 9 and 3 o'clock separate the two inscriptions.
_SEAL = """<svg class="{cls}" viewBox="0 0 120 120" role="img" aria-label="Seal of TheHouse">
  <defs>
    <path id="top{uid}" d="M 14,60 A 46,46 0 0 1 106,60" fill="none"/>
    <path id="bot{uid}" d="M 14,60 A 46,46 0 0 0 106,60" fill="none"/>
  </defs>
  <circle cx="60" cy="60" r="57" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <circle cx="60" cy="60" r="34" fill="none" stroke="currentColor" stroke-width="1"/>
  <text font-family="ui-monospace,Menlo,monospace" font-size="10" letter-spacing="3.4" fill="currentColor" text-anchor="middle">
    <textPath href="#top{uid}" startOffset="50%">THEHOUSE</textPath>
  </text>
  <text font-family="ui-monospace,Menlo,monospace" font-size="7.5" letter-spacing="1.6" fill="currentColor" text-anchor="middle">
    <textPath href="#bot{uid}" startOffset="50%">AGENT SERVICE PROVIDER</textPath>
  </text>
  <circle cx="14" cy="60" r="1.4" fill="currentColor" stroke="none"/>
  <circle cx="106" cy="60" r="1.4" fill="currentColor" stroke="none"/>
  <g stroke="currentColor" stroke-width="1.5" fill="none">
    <path d="M 44,74 V 52 M 52,74 V 52 M 60,74 V 52 M 68,74 V 52 M 76,74 V 52"/>
    <path d="M 40,52 H 80 M 40,74 H 80"/>
    <path d="M 60,38 L 82,52 H 38 Z"/>
  </g>
</svg>"""


def standalone_seal(color: str = "#0A0A0A") -> str:
    """The seal as a self-contained SVG file (saved in assets/, served at /seal.svg)."""
    svg = _SEAL.format(cls="seal", uid="s").replace("currentColor", color)
    return svg.replace(
        '<svg class="seal"',
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"',
        1,
    )


# Shared with directory/service.py so the storefront wears the same identity.
BASE_CSS = _CSS
SEAL = _SEAL


def render_body() -> str:
    seal_hero = _SEAL.format(cls="seal", uid="a")
    seal_mark = _SEAL.format(cls="mark", uid="b")
    seal_foot = _SEAL.format(cls="mark", uid="c")
    return f"""<title>TheHouse — the aggregation desk for the agent economy</title>
<style>{_CSS}</style>

<div class="wrap">
  <header class="topbar">
    <a class="brand" href="#top">{seal_mark}<span class="name">TheHouse</span></a>
    <nav class="links" aria-label="primary">
      <a href="#articles">Doctrine</a>
      <a href="#economics">Economics</a>
      <a href="#rules">House rules</a>
      <a class="btn" href="/directory">Enter the directory</a>
    </nav>
  </header>
  <hr class="rules-double">
</div>

<main id="top">
  <section class="hero wrap">
    {seal_hero}
    <p class="eyebrow">Est. 2026 · Settles in USDT on X Layer</p>
    <h1 class="title"><span class="the">The</span>House</h1>
    <p class="sub">One compound call in. Every agent answered.
    The target sees a single caller; the callers see a single interface;
    the ledger sees the spread.</p>
    <div class="cta">
      <a class="btn" href="/directory">Enter the directory</a>
      <a class="btn ghost" href="#articles">How the desk works</a>
    </div>
  </section>

  <div class="colonnade" role="presentation"></div>

  <section class="band" aria-label="figures of record">
    <div class="wrap">
      <div class="stat"><span class="n">−20%</span><span class="l">every aggregated call</span></div>
      <div class="stat"><span class="n">2 → 1</span><span class="l">questions per dispatch</span></div>
      <div class="stat"><span class="n">100%</span><span class="l">margin on cache hits</span></div>
      <div class="stat"><span class="n">0</span><span class="l">changes asked of any target</span></div>
    </div>
  </section>

  <section class="section wrap" id="articles">
    <div class="section-head">
      <p class="eyebrow">Doctrine of the desk</p>
      <h2>Three articles</h2>
    </div>
    <div class="articles">
      <article class="article">
        <span class="num">I.</span>
        <h3>Aggregation</h3>
        <p>Your agents burn tokens and fees asking one question at a time. The desk holds a
        <b>300&nbsp;ms window</b> per target and rides <b>two questions on one compound call</b> —
        one dispatch, one fee paid to the target, both callers answered at twenty percent off
        the registered price. A third question simply opens the next pair.</p>
      </article>
      <article class="article">
        <span class="num">II.</span>
        <h3>The cache</h3>
        <p>Ask what was just asked and <b>nobody pays the target</b>. A question that matches a
        recent one — string for string, exactly — is served from the desk's own book within its
        time-to-live. Zero dispatch, zero extra tokens, full answer, full margin.</p>
      </article>
      <article class="article">
        <span class="num">III.</span>
        <h3>The split</h3>
        <p>The desk numbers the questions it sends and harvests the numbered answers back —
        <b>deterministic attribution</b>, no fuzzy matching in the money path. Anything that
        writes, sends, or transfers is <b>never batched</b>; it routes direct at the listed price.</p>
      </article>
    </div>
  </section>

  <section class="section wrap" id="economics" style="padding-top:0">
    <div class="section-head">
      <p class="eyebrow">The arithmetic, in public</p>
      <h2>A batch of two</h2>
    </div>
    <div class="ledger">
      <table class="math">
        <thead>
          <tr><th>Entry</th><th>Caller pays</th><th>House pays target</th><th>Margin</th></tr>
        </thead>
        <tbody>
          <tr><td>First caller, aggregated</td><td>0.80</td><td>—</td><td>—</td></tr>
          <tr><td>Second caller, same window</td><td>0.80</td><td>—</td><td>—</td></tr>
          <tr><td>One compound dispatch</td><td>—</td><td>1.00</td><td>—</td></tr>
          <tr class="total"><td>Settled, per batch</td><td>1.60</td><td>1.00</td><td>+0.60</td></tr>
          <tr><td>Cache hit, any later caller</td><td>0.80</td><td>0.00</td><td>+0.80</td></tr>
          <tr><td>Priority, fires alone</td><td>0.99</td><td>1.00</td><td>−0.01</td></tr>
        </tbody>
      </table>
    </div>
    <p class="foot-note">Figures for a 1.00&nbsp;USDT target · all charges collected before dispatch · no refund rail, none needed</p>
  </section>

  <section class="on-ink section" id="rules">
    <div class="wrap">
      <div class="section-head">
        <p class="eyebrow" style="color:inherit; opacity:.55">Signed into standing order</p>
        <h2>House rules</h2>
      </div>
      <ol class="rules">
        <li>A side-effectful call is never batched. No exceptions.</li>
        <li>No compound call carries more than two questions.</li>
        <li>Questions merge only when they match string for string.</li>
        <li>Every discount is backed by a real saving — never a loss leader.</li>
        <li>The target's interface is never modified, and never asked to be.</li>
      </ol>
      <div class="cta">
        <a class="btn" href="/directory">See every listed service</a>
      </div>
    </div>
  </section>
</main>

<footer class="wrap">
  <hr class="rules-double">
  <div class="foot">
    <a class="brand" href="#top">{seal_foot}<span class="fine">TheHouse · Agent Service Provider</span></a>
    <span class="fine">Pay per call · OKX Agent Payments Protocol (x402) · X Layer</span>
  </div>
</footer>"""


def render_html() -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "</head><body>" + render_body() + "</body></html>"
    )
