let PRESETS = [];   // loaded from /api/presets
let tab="en", spec=null;
let ACTIVE_UNIVERSE_ID = "nifty500";  // filters which presets show (ROADMAP Item 15)

function pickPreset(){
  const id = $("presetSel").value;
  const p = PRESETS.find(x=>x.id===id);
  const d = $("pdesc");
  if(!p){d.style.display="none";return}
  setTab("js");
  $("qJs").value = JSON.stringify(p.spec, null, 2);
  spec = null;
  $("interp").style.display="none";
  $("btnRun").disabled = false;
  const ev = p.evidence;
  const evHtml = ev ? `
    <div class="evfinding">${ev.finding}${ev.sources && ev.sources.length ? ` <span class="mini">(${ev.sources.join("; ")})</span>` : ""}</div>
    <div class="evcaveat">Caveat: ${ev.caveat}</div>` : "";
  d.innerHTML = `<b style="color:var(--text)">${p.name}.</b>`
    + (ev ? `<span class="evtag ${ev.basis}">${ev.basis}</span>` : "")
    + ` ${p.description}<br>Compiles to: ${p.english}${evHtml}`;
  d.style.display = "block";
}

let RECENT_LOADED=false, RECENT_LOG=[];
async function toggleRecent(){
  const p=$("recentPanel"), opening = p.style.display==="none";
  p.style.display = opening?"block":"none";
  $("btnRecent").textContent = opening?"hide recent":"recent screens";
  if(!opening || RECENT_LOADED) return;
  p.innerHTML=`<div class="pdesc" style="display:block">loading…</div>`;
  try{
    RECENT_LOG = await api("/api/log");
    RECENT_LOADED=true;
    p.innerHTML = RECENT_LOG.length
      ? RECENT_LOG.map((e,i)=>`
        <div class="recent-row" tabindex="0" role="button"
             aria-label="Replay this screen"
             onclick="replayRecent(${i})"
             onkeydown="onCardKey(event,${i},replayRecent)">
          <span class="mini">${(e.ts||"").replace("T"," ").slice(0,16)}</span>
          <span class="mini">as_of <b>${e.as_of}</b></span>
          <span class="mini"><b>${e.stats.matched}</b>/${e.stats.evaluated}</span>
          <span class="cname">${e.english||JSON.stringify(e.spec)}</span>
        </div>`).join("")
      : `<div class="pdesc" style="display:block">No screens logged yet — run one first.</div>`;
  }catch(e){
    p.innerHTML=`<div class="pdesc" style="display:block">Could not load recent screens: ${e.message}</div>`;
  }
}
function replayRecent(i){
  const e=RECENT_LOG[i];
  setTab("js");
  $("qJs").value=JSON.stringify(e.spec,null,2);
  spec=null; $("interp").style.display="none";
  $("btnRun").disabled=false;
  $("asOf").value = (e.as_of && e.as_of!=="latest") ? e.as_of : "";
  $("pdesc").innerHTML=`<b style="color:var(--text)">Replaying logged screen.</b> ${e.english||""}`;
  $("pdesc").style.display="block";
  toggleRecent();
  window.scrollTo({top:0,behavior:"smooth"});
}

// ---------------------------------------------------------------- watchlist (ROADMAP Item 5)
let WATCHLIST_LOADED=false;
async function addToWatchlist(symbol,btnEl){
  if(!LAST_SPEC){err("Run a screen first.");return}
  try{
    await api("/api/watchlist",{symbol,spec:LAST_SPEC});
    if(btnEl){btnEl.textContent="★ watching";btnEl.disabled=true}
    WATCHLIST_LOADED=false;
    toast(`${symbol} added to watchlist`);
  }catch(e){err("Could not add to watchlist: "+e.message)}
}
async function toggleWatchlist(){
  const p=$("watchlistPanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnWatchlist").textContent=opening?"hide watchlist":"☆ watchlist";
  if(!opening) return;
  await loadWatchlist();
}
async function loadWatchlist(){
  const p=$("watchlistPanel");
  p.innerHTML=`<div class="pdesc" style="display:block">loading…</div>`;
  try{
    const items=await api("/api/watchlist");
    WATCHLIST_LOADED=true;
    p.innerHTML = items.length ? items.map(it=>`
      <div class="recent-row">
        <span class="sym" style="color:var(--amber)">${it.symbol}</span>
        <span class="mini">tagged ${it.tagged_date} @ ₹${it.close_at_tag}</span>
        <span class="mini">now ₹${fmt(it.current_close)} ${signed(it.move_pct)}</span>
        <span class="mini ${it.still_holds?"pos":"neg"}">${it.still_holds===null?"—":it.still_holds?"✓ still holds":"✗ decayed"}</span>
        <span class="cname">${it.spec.conditions?it.spec.conditions.length:0} condition(s)</span>
        <button class="btnsm" onclick="removeFromWatchlist('${it.id}')" type="button">remove</button>
      </div>`).join("")
      : `<div class="pdesc" style="display:block">Nothing on the watchlist yet — click ☆ watch on a match.</div>`;
  }catch(e){
    p.innerHTML=`<div class="pdesc" style="display:block">Could not load watchlist: ${e.message}</div>`;
  }
}
async function removeFromWatchlist(id){
  try{
    await fetch("/api/watchlist/"+id,{method:"DELETE"});
    await loadWatchlist();
    toast("Removed from watchlist");
  }catch(e){err("Could not remove: "+e.message)}
}

// ---------------------------------------------------------------- toasts (ROADMAP Item 11 component polish)
// A single reused element (aria-live="polite" so screen readers
// announce it without stealing focus) rather than stacking multiple —
// these are brief confirmations, not a notification center.
// ---------------------------------------------------------------- reset
// View reset only: returns the page to its initial state. Deliberately
// does NOT delete persisted data (watchlist, cohorts, saved screens,
// screen log) — destructive actions need their own confirmed flows.
function resetAll(){
  // inputs & spec state
  $("qEn").value=""; $("qJs").value=""; spec=null;
  $("asOf").value=""; $("presetSel").value="";
  $("pdesc").style.display="none"; $("pdesc").innerHTML="";
  setTab("en");                       // default tab; disables Run via setTab
  err("");
  // interpretation
  $("interp").style.display="none";
  $("english").textContent=""; $("specPre").textContent="";
  $("assumptions").innerHTML="";
  const w=$("screenWarnings"); w.style.display="none"; w.innerHTML="";
  // stats strip + attached panels
  $("stats").style.display="none"; $("cells").innerHTML="";
  [["diffBox",null],["allocatePanel","btnAllocate|💰 allocate"],
   ["backtestPanel","btnBacktest|🧪 backtest"]].forEach(([id,btn])=>{
    const el=$(id); el.style.display="none"; el.innerHTML="";
    if(btn){const [b,label]=btn.split("|"); $(b).textContent=label;}
  });
  $("btnTrackCohort").textContent="📈 track these matches";
  $("btnTrackCohort").disabled=false;
  // results & footer
  $("results").innerHTML=""; $("foot").innerHTML="";
  // collapsible panels + their toggle labels
  [["recentPanel","btnRecent","recent screens"],
   ["myScreensPanel","btnMyScreens","manage my screens"],
   ["dashboardPanel","btnDashboard","📊 dashboard"],
   ["watchlistPanel","btnWatchlist","☆ watchlist"],
   ["cohortsPanel","btnCohorts","📈 cohorts"]].forEach(([p,b,label])=>{
    $(p).style.display="none"; $(p).innerHTML=""; $(b).textContent=label;
  });
  // chart modal, if open
  if(typeof closeChartModal==="function") closeChartModal();
  window.scrollTo({top:0,behavior:"smooth"});
  toast("view reset — saved data untouched");
}

function toast(msg){
  let t=document.getElementById("toast");
  if(!t){
    t=document.createElement("div");
    t.id="toast"; t.className="toast";
    t.setAttribute("role","status"); t.setAttribute("aria-live","polite");
    document.body.appendChild(t);
  }
  t.textContent=msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer=setTimeout(()=>t.classList.remove("show"), 2600);
}

function $(id){return document.getElementById(id)}
function setTab(t){tab=t;
  $("tabEn").classList.toggle("active",t==="en");
  $("tabJs").classList.toggle("active",t==="js");
  $("qEn").style.display=t==="en"?"":"none";
  $("qJs").style.display=t==="js"?"":"none";
  $("btnInterpret").style.display=t==="en"?"":"none";
  $("btnRun").disabled = t==="en" && !spec;
}
function err(msg){const e=$("err");e.textContent=msg;e.style.display=msg?"block":"none"}
function busy(b){$("spin").classList.toggle("on",b);
  $("btnInterpret").disabled=b;$("btnRun").disabled=b||(tab==="en"&&!spec)}

async function api(path, body){
  const r = await fetch(path,{method:body?"POST":"GET",
    headers:{"Content-Type":"application/json"},
    body:body?JSON.stringify(body):undefined});
  const j = await r.json();
  if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
  return j;
}

let BUILTIN_PRESETS=[], USER_PRESETS=[];

function rebuildPresetDropdown(){
  PRESETS = [...BUILTIN_PRESETS, ...USER_PRESETS.map(u=>({
    id:"user:"+u.id, name:u.name, group:"My screens",
    description:u.notes||"user-saved screen", spec:u.spec,
    english:u.english,
  }))];
  const sel=$("presetSel");
  sel.innerHTML=`<option value="">— choose a pre-configured screen —</option>`;
  // presets with a universes tag (built-ins) are hidden when they don't
  // apply to the active universe (e.g. sector presets on nse_full,
  // which has no sector data — see evaluator.sector_data_gap_warning);
  // user-saved presets carry no tag and always show.
  const visible=PRESETS.filter(p=>!p.universes || p.universes.includes(ACTIVE_UNIVERSE_ID));
  const groups={};
  visible.forEach(p=>{(groups[p.group]=groups[p.group]||[]).push(p)});
  for(const [g,items] of Object.entries(groups)){
    const og=document.createElement("optgroup");og.label=g;
    items.forEach(p=>{const o=document.createElement("option");
      o.value=p.id;o.textContent=p.name;og.appendChild(o)});
    sel.appendChild(og);
  }
}

async function loadUserPresets(){
  try{ USER_PRESETS=await api("/api/user_presets") }catch(e){ USER_PRESETS=[] }
  rebuildPresetDropdown();
}

async function saveCurrentAsPreset(){
  if(!LAST_SPEC){ err("Run a screen first, then save it.");return }
  const name=prompt("Name for this saved screen:");
  if(!name) return;
  const notes=prompt("Notes (optional):")||"";
  try{
    await api("/api/user_presets",{name,notes,spec:LAST_SPEC});
    await loadUserPresets();
    err("");
    toast(`Saved "${name}" to My screens`);
  }catch(e){ err("Could not save: "+e.message) }
}

async function toggleMyScreens(){
  const p=$("myScreensPanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnMyScreens").textContent=opening?"hide my screens":"manage my screens";
  if(!opening) return;
  await loadUserPresets();
  renderMyScreensPanel();
}
function renderMyScreensPanel(){
  const p=$("myScreensPanel");
  p.innerHTML = USER_PRESETS.length ? USER_PRESETS.map(u=>`
    <div class="recent-row">
      <span class="sym" style="color:var(--amber)">${u.name}</span>
      <span class="cname">${u.notes||""}</span>
      <span class="mini">${u.english}</span>
      <button class="btnsm" onclick="renameMyScreen('${u.id}')" type="button">rename</button>
      <button class="btnsm" onclick="deleteMyScreen('${u.id}')" type="button">delete</button>
    </div>`).join("")
    : `<div class="pdesc" style="display:block">No saved screens yet — run one and click "save as my screen".</div>`;
}
async function renameMyScreen(id){
  const name=prompt("New name:");
  if(!name) return;
  try{
    await fetch("/api/user_presets/"+id,{method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name})});
    await loadUserPresets();
    renderMyScreensPanel();
    toast(`Renamed to "${name}"`);
  }catch(e){ err("Could not rename: "+e.message) }
}
async function deleteMyScreen(id){
  try{
    await fetch("/api/user_presets/"+id,{method:"DELETE"});
    await loadUserPresets();
    renderMyScreensPanel();
    toast("Screen deleted");
  }catch(e){ err("Could not delete: "+e.message) }
}

// ---------------------------------------------------------------- multi-screen dashboard (ROADMAP Item 5)
let DASHBOARD_SELECTED=new Set();

function toggleDashboard(){
  const p=$("dashboardPanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnDashboard").textContent=opening?"hide dashboard":"📊 dashboard";
  if(opening) renderDashboardPicker();
}
function renderDashboardPicker(){
  const p=$("dashboardPanel");
  const checks=PRESETS.map(pr=>`
    <label style="display:inline-flex;align-items:center;gap:4px;margin:2px 10px 2px 0;font-family:var(--sans);font-size:11.5px;color:var(--text)">
      <input type="checkbox" value="${pr.id}" ${DASHBOARD_SELECTED.has(pr.id)?"checked":""} onchange="toggleDashSel('${pr.id}',this.checked)">
      ${pr.name}
    </label>`).join("");
  p.innerHTML=`
    <div class="pdesc" style="display:block;margin-bottom:8px">Pick screens to run together — the morning view.</div>
    <div>${checks||"no presets loaded"}</div>
    <div class="row" style="margin-top:10px"><button class="btnsm" onclick="runDashboard()" type="button">run selected</button></div>
    <div id="dashboardResults" style="margin-top:12px"></div>`;
}
function toggleDashSel(id,checked){
  if(checked) DASHBOARD_SELECTED.add(id); else DASHBOARD_SELECTED.delete(id);
}
async function runDashboard(){
  if(!DASHBOARD_SELECTED.size){ err("Pick at least one screen.");return }
  const box=$("dashboardResults");
  box.innerHTML=`<div class="pdesc" style="display:block">running…</div>`;
  try{
    const j=await api("/api/screen_batch",{preset_ids:[...DASHBOARD_SELECTED]});
    box.innerHTML=`
      <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12.5px">
        <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
          <th style="padding:6px 8px">Screen</th><th style="padding:6px 8px">Matched</th>
          <th style="padding:6px 8px">Top 3</th><th style="padding:6px 8px">New since last run</th>
        </tr></thead>
        <tbody>${j.rows.map(r=>r.error?`
          <tr style="border-bottom:1px dashed var(--line)"><td style="padding:6px 8px" colspan="4" class="neg">${r.name}: ${r.error}</td></tr>`:`
          <tr style="border-bottom:1px dashed var(--line)">
            <td style="padding:6px 8px">${r.name}</td>
            <td style="padding:6px 8px"><b class="pos">${r.matched}</b></td>
            <td style="padding:6px 8px">${r.top3.join(", ")||"—"}</td>
            <td style="padding:6px 8px">${r.new_since_last_run===null?"—":r.new_since_last_run}</td>
          </tr>`).join("")}</tbody>
      </table>`;
  }catch(e){ box.innerHTML=`<div class="empty">${e.message}</div>` }
}

// ---------------------------------------------------------------- portfolio allocation (ROADMAP Item 10)
const RISK_PRESETS_UI={conservative:0.5, moderate:1.0, aggressive:2.0};
let LAST_ALLOCATION=null;

function toggleAllocate(){
  const p=$("allocatePanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnAllocate").textContent=opening?"hide allocate":"💰 allocate";
  if(opening) renderAllocateForm();
}
function renderAllocateForm(){
  const p=$("allocatePanel");
  if(!LAST_MATCHES.length){
    p.innerHTML=`<div class="empty">Run a screen with at least one match first.</div>`;
    return;
  }
  p.innerHTML=`
    <div class="pdesc" style="display:block;margin-bottom:8px">
      Position-sizing calculator, not a recommendation engine — it has no view on
      which stocks to buy, only on how much of each. Ranked in the current
      results order (best 3-month return first).
    </div>
    <div class="row" style="flex-wrap:wrap;gap:10px">
      <label class="asof">capital &#8377; <input type="number" id="allocCapital" value="100000" min="1" style="width:110px"></label>
      <label class="asof">risk preset
        <select id="allocRiskPreset" onchange="onAllocRiskPresetChange()">
          <option value="conservative">conservative (0.5%)</option>
          <option value="moderate" selected>moderate (1.0%)</option>
          <option value="aggressive">aggressive (2.0%)</option>
          <option value="custom">custom</option>
        </select>
      </label>
      <label class="asof" id="allocCustomRiskWrap" style="display:none">risk % <input type="number" id="allocRiskPct" value="1.0" min="0.1" step="0.1" style="width:70px"></label>
      <label class="asof">method
        <select id="allocMethod">
          <option value="risk" selected>fixed-fractional risk</option>
          <option value="inverse_vol">inverse-volatility</option>
          <option value="equal">equal weight (1/N)</option>
        </select>
      </label>
    </div>
    <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label class="asof">max positions <input type="number" id="allocMaxPositions" value="10" min="1" style="width:60px"></label>
      <label class="asof">max position % <input type="number" id="allocMaxPositionPct" value="15" min="1" style="width:60px"></label>
      <label class="asof">sector cap % <input type="number" id="allocSectorCapPct" value="30" min="1" style="width:60px"></label>
      <label class="asof">min ticket &#8377; <input type="number" id="allocMinTicket" value="5000" min="0" style="width:80px"></label>
      <button class="primary btnsm" onclick="runAllocate()" type="button">run allocation</button>
    </div>
    <div id="allocateResults" style="margin-top:12px"></div>`;
}
function onAllocRiskPresetChange(){
  const v=$("allocRiskPreset").value;
  $("allocCustomRiskWrap").style.display = v==="custom" ? "" : "none";
}
async function runAllocate(){
  const box=$("allocateResults");
  box.innerHTML=`<div class="pdesc" style="display:block">sizing…</div>`;
  const preset=$("allocRiskPreset").value;
  const risk_pct = preset==="custom"
    ? (parseFloat($("allocRiskPct").value)||1.0) : RISK_PRESETS_UI[preset];
  const body={
    symbols: LAST_MATCHES.map(m=>m.symbol),
    capital: parseFloat($("allocCapital").value)||0,
    method: $("allocMethod").value,
    risk_pct,
    max_positions: parseInt($("allocMaxPositions").value)||10,
    max_position_pct: parseFloat($("allocMaxPositionPct").value)||15,
    sector_cap_pct: parseFloat($("allocSectorCapPct").value)||30,
    min_ticket: parseFloat($("allocMinTicket").value)||0,
    spec: LAST_SPEC,
  };
  try{
    const j=await api("/api/allocate", body);
    LAST_ALLOCATION=j;
    box.innerHTML = renderAllocationResult(j);
  }catch(e){
    box.innerHTML=`<div class="empty">${e.message}</div>`;
  }
}
function allocTable(res, title){
  if(!res.positions.length){
    return `<div class="pdesc" style="display:block">${title}: no positions sized (see excluded below).</div>`;
  }
  const rows = res.positions.map(p=>`
    <tr style="border-bottom:1px dashed var(--line)">
      <td style="padding:5px 8px"><b>${p.symbol}</b></td>
      <td style="padding:5px 8px" class="mini">${p.sector}</td>
      <td style="padding:5px 8px">&#8377;${fmt(p.entry)}</td>
      <td style="padding:5px 8px">${p.shares}</td>
      <td style="padding:5px 8px">&#8377;${fmt(p.value)}</td>
      <td style="padding:5px 8px">${p.pct_of_capital}%</td>
      <td style="padding:5px 8px">&#8377;${fmt(p.stop_level)}</td>
      <td style="padding:5px 8px">&#8377;${fmt(p.risk)}</td>
    </tr>`).join("");
  return `
    <div class="eyebrow" style="margin:10px 0 4px">${title}</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px">
      <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
        <th style="padding:5px 8px">Symbol</th><th style="padding:5px 8px">Sector</th>
        <th style="padding:5px 8px">Entry</th><th style="padding:5px 8px">Shares</th>
        <th style="padding:5px 8px">Value</th><th style="padding:5px 8px">% capital</th>
        <th style="padding:5px 8px">Stop</th><th style="padding:5px 8px">Risk &#8377;</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="capnote">Deployed &#8377;${fmt(res.summary.deployed)} &middot; cash &#8377;${fmt(res.summary.cash)}
      &middot; portfolio risk if all stops hit &#8377;${fmt(res.summary.portfolio_risk)}
      &middot; largest sector ${res.summary.largest_sector||"—"}</div>`;
}
function renderAllocationResult(j){
  const label = j.method==="risk" ? "Fixed-fractional risk"
    : j.method==="inverse_vol" ? "Inverse-volatility" : "Equal weight";
  let html = allocTable(j, `${label} sizing`);
  if(j.baseline){
    html += allocTable(j.baseline, "Equal-weight baseline (for comparison)");
  }
  if(j.excluded && j.excluded.length){
    html += `<div class="pdesc" style="display:block;margin-top:8px">Excluded: ${
      j.excluded.map(e=>`${e.symbol} (${e.reason})`).join("; ")}</div>`;
  }
  html += `<div class="capnote" style="margin-top:6px">${j.disclaimer}</div>
    <button class="btnsm" style="margin-top:6px" onclick="exportAllocationCsv()" type="button">export allocation to CSV</button>
    <button class="btnsm" style="margin-top:6px" onclick="trackAllocationAsCohort(this)" type="button">📈 track this portfolio</button>`;
  return html;
}
function exportAllocationCsv(){
  if(!LAST_ALLOCATION || !LAST_ALLOCATION.positions.length){
    err("No allocation to export."); return;
  }
  const rows=[["symbol","sector","entry","shares","value","pct_of_capital","stop_level","risk","rationale"]];
  LAST_ALLOCATION.positions.forEach(p=>rows.push(
    [p.symbol,p.sector,p.entry,p.shares,p.value,p.pct_of_capital,p.stop_level,p.risk,p.rationale]));
  const csv=rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(",")).join("\n");
  const blob=new Blob([csv],{type:"text/csv"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob); a.download="allocation.csv"; a.click();
  toast("Allocation exported to CSV");
}

// ---------------------------------------------------------------- screen backtester (ROADMAP Item 14)
const EXPENSIVE_BT_TYPES=new Set(["near_support","near_resistance",
  "breakout_resistance","bb_squeeze","rs_percentile","sector_rank",
  "atr_pct_percentile"]);
let LAST_BACKTEST=null;

function toggleBacktest(){
  const p=$("backtestPanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnBacktest").textContent=opening?"hide backtest":"🧪 backtest";
  if(opening) renderBacktestForm();
}
function renderBacktestForm(){
  const p=$("backtestPanel");
  if(!LAST_SPEC){
    p.innerHTML=`<div class="empty">Run a screen first — the backtester replays its spec historically.</div>`;
    return;
  }
  const hasExpensive = (LAST_SPEC.conditions||[]).some(c=>EXPENSIVE_BT_TYPES.has(c.type));
  p.innerHTML=`
    <div class="pdesc" style="display:block;margin-bottom:8px">
      Event-study engine: what happened after this signal fired historically,
      versus just holding the universe on the same dates. Not a portfolio
      simulator — no sizing, no compounding, no stops.
      ${hasExpensive?`<br><b>This spec uses a slower condition type (support/resistance, `+
        `Bollinger squeeze, or a cross-sectional rank) — expect roughly `+
        `1-2 minutes, more with the sensitivity grid on.</b>`:""}
    </div>
    <div class="row" style="flex-wrap:wrap;gap:10px">
      <label class="asof">horizons (bars) <input type="text" id="btHorizons" value="5,20,60" style="width:80px"></label>
      <label class="asof">cooldown <input type="number" id="btCooldown" value="20" min="1" style="width:60px"></label>
      <label class="asof">cost % (round trip) <input type="number" id="btCostPct" value="0.30" min="0" step="0.05" style="width:70px"></label>
      <label class="asof">stride <input type="number" id="btStride" value="20" min="1" style="width:60px"></label>
      <label class="asof">min events <input type="number" id="btMinEvents" value="30" min="1" style="width:60px"></label>
    </div>
    <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label class="asof" style="flex:1">hypothesis (optional, logged with the run)
        <input type="text" id="btHypothesis" placeholder="e.g. expect +1-2% 20-bar excess, hit rate ~55%" style="width:100%">
      </label>
    </div>
    <div class="row" style="margin-top:8px">
      <label class="asof"><input type="checkbox" id="btSensitivity" checked> sensitivity grid (one-at-a-time parameter perturbation)</label>
      <button class="primary btnsm" onclick="runBacktest()" type="button">run backtest</button>
    </div>
    <div id="backtestResults" style="margin-top:12px"></div>`;
}
function btPct(x){ return x===null||x===undefined ? "—" : (x*100).toFixed(2)+"%" }
function btSigned(x){
  if(x===null||x===undefined) return "—";
  const v=x*100;
  return `<span class="${v>=0?"pos":"neg"}">${v>0?"+":""}${v.toFixed(2)}%</span>`;
}
async function runBacktest(){
  const box=$("backtestResults");
  box.innerHTML=`<div class="pdesc" style="display:block">running — this can take a while for support/resistance or percentile-based screens…</div>`;
  const horizons=$("btHorizons").value.split(",").map(s=>parseInt(s.trim())).filter(n=>n>0);
  const body={
    spec: LAST_SPEC,
    horizons: horizons.length?horizons:[5,20,60],
    cooldown: parseInt($("btCooldown").value)||20,
    cost_pct: parseFloat($("btCostPct").value)||0,
    stride: parseInt($("btStride").value)||20,
    min_events: parseInt($("btMinEvents").value)||30,
    hypothesis: $("btHypothesis").value||null,
    sensitivity: $("btSensitivity").checked,
  };
  try{
    const j=await api("/api/backtest", body);
    LAST_BACKTEST=j;
    box.innerHTML=renderBacktestResult(j);
  }catch(e){
    box.innerHTML=`<div class="empty">${e.message}</div>`;
  }
}
function btHorizonTable(h, stats){
  if(stats.insufficient){
    return `<div class="pdesc" style="display:block">
      <b>${h}-bar horizon:</b> insufficient events (${stats.count}) — no stats shown.</div>`;
  }
  const eg=stats.excess_gross, en=stats.excess_net, raw=stats.raw, ci=stats.bootstrap_ci_excess_net_mean;
  return `
    <div class="eyebrow" style="margin:10px 0 4px">${h}-bar horizon — ${stats.count} events across ${stats.event_dates} dates</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px">
      <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
        <th style="padding:5px 8px"></th><th style="padding:5px 8px">mean</th>
        <th style="padding:5px 8px">median</th><th style="padding:5px 8px">hit rate</th>
        <th style="padding:5px 8px">p5 / p95</th><th style="padding:5px 8px">worst 5% mean</th>
      </tr></thead>
      <tbody>
        <tr style="border-bottom:1px dashed var(--line)">
          <td style="padding:5px 8px">excess (gross)</td>
          <td style="padding:5px 8px">${btSigned(eg.mean)}</td>
          <td style="padding:5px 8px">${btSigned(eg.median)}</td>
          <td style="padding:5px 8px">${btPct(eg.hit_rate)}</td>
          <td style="padding:5px 8px">${btPct(eg.p5)} / ${btPct(eg.p95)}</td>
          <td style="padding:5px 8px">${btPct(eg.worst5pct_mean)}</td>
        </tr>
        <tr style="border-bottom:1px dashed var(--line)">
          <td style="padding:5px 8px">excess (net)</td>
          <td style="padding:5px 8px">${btSigned(en.mean)}</td>
          <td style="padding:5px 8px">${btSigned(en.median)}</td>
          <td style="padding:5px 8px">${btPct(en.hit_rate)}</td>
          <td style="padding:5px 8px">${btPct(en.p5)} / ${btPct(en.p95)}</td>
          <td style="padding:5px 8px">${btPct(en.worst5pct_mean)}</td>
        </tr>
      </tbody>
    </table>
    <div class="capnote">raw: event ${btSigned(raw.event_gross_mean)} gross / ${btSigned(raw.event_net_mean)} net
      vs. same-date universe baseline ${btSigned(raw.baseline_mean)}
      &middot; bootstrap 90% CI on mean excess (net): [${btPct(ci.lo5)}, ${btPct(ci.hi95)}]</div>`;
}
function btHistogram(h, events){
  const vals = events.map(e=>e["excess_net_"+h]).filter(v=>v!==null && v!==undefined);
  if(vals.length < 5) return "";
  const lo=Math.min(...vals), hi=Math.max(...vals);
  if(lo===hi) return "";
  const nbins=12, width=(hi-lo)/nbins;
  const bins=new Array(nbins).fill(0);
  vals.forEach(v=>{
    let i=Math.floor((v-lo)/width);
    if(i>=nbins) i=nbins-1;
    if(i<0) i=0;
    bins[i]++;
  });
  const max=Math.max(...bins);
  const bars=bins.map((count,i)=>{
    const binLo=(lo+i*width)*100, binHi=(lo+(i+1)*width)*100;
    const straddlesZero = binLo<0 && binHi>=0;
    return `
    <div style="display:flex;align-items:center;gap:6px;font-family:var(--sans);font-size:10.5px">
      <span class="mini" style="width:96px">${binLo.toFixed(1)}% to ${binHi.toFixed(1)}%</span>
      <span style="background:${straddlesZero?'var(--muted)':binLo>=0?'var(--pass)':'var(--fail)'};height:8px;width:${Math.max(2,count/max*160)}px;border-radius:2px"></span>
      <span class="mini">${count}</span>
    </div>`;
  }).join("");
  return `<div class="eyebrow" style="margin:8px 0 4px">${h}-bar excess (net) distribution vs. baseline</div>
    <div style="display:flex;flex-direction:column;gap:2px">${bars}</div>`;
}
function btTimeline(timeline){
  const entries=Object.entries(timeline||{});
  if(!entries.length) return "";
  const max=Math.max(...entries.map(e=>e[1]));
  const bars=entries.map(([month,count])=>`
    <div style="display:flex;align-items:center;gap:6px;font-family:var(--sans);font-size:11px">
      <span class="mini" style="width:56px">${month}</span>
      <span style="background:var(--amber);height:8px;width:${Math.max(2,count/max*160)}px;border-radius:2px"></span>
      <span class="mini">${count}</span>
    </div>`).join("");
  return `<div class="eyebrow" style="margin:12px 0 4px">Events per month — a signal that stopped firing is a finding</div>
    <div style="display:flex;flex-direction:column;gap:2px;max-height:220px;overflow-y:auto">${bars}</div>`;
}
function btSensitivityTable(grid){
  if(!grid || !grid.length) return "";
  const rows=grid.map(row=>`
    <tr style="border-bottom:1px dashed var(--line)">
      <td style="padding:5px 8px">condition[${row.condition_index}].${row.param}</td>
      <td style="padding:5px 8px">${row.base_value}</td>
      <td style="padding:5px 8px" class="mini">${row.cells.map(c=>
        c.error ? "err" : `${c.value}→${c.count}ev/${btPct(c.mean_excess_net)}`).join("  ")}</td>
      <td style="padding:5px 8px" class="${row.verdict.startsWith('robust')?'pos':'neg'}">${row.verdict}</td>
    </tr>`).join("");
  return `<div class="eyebrow" style="margin:12px 0 4px">Sensitivity (one-at-a-time, 20-bar excess net)</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:11.5px">
      <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
        <th style="padding:5px 8px">parameter</th><th style="padding:5px 8px">base</th>
        <th style="padding:5px 8px">±2 steps (value→events/mean excess net)</th><th style="padding:5px 8px">verdict</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}
function renderBacktestResult(j){
  let html = `<div class="pdesc" style="display:block">${j.n_symbols} symbols, `+
    `<b>${j.n_events_total}</b> events total (${j.elapsed_sec}s)`+
    (j.hypothesis?`<br>Hypothesis: <i>${j.hypothesis}</i>`:"")+`</div>`;
  if(j.warnings && j.warnings.length){
    html += `<div class="evcaveat">${j.warnings.join(" ")}</div>`;
  }
  html += Object.entries(j.horizons).map(([h,stats])=>
    btHorizonTable(h,stats) + (stats.insufficient?"":btHistogram(h,j.events))).join("");
  html += btTimeline(j.event_timeline);
  if(j.sensitivity) html += btSensitivityTable(j.sensitivity);
  html += `<div class="evcaveat" style="margin-top:10px">${j.survivorship_note}</div>`;
  html += `<button class="btnsm" style="margin-top:6px" onclick="exportBacktestEventsCsv()" type="button">export events to CSV</button>`;
  return html;
}
function exportBacktestEventsCsv(){
  if(!LAST_BACKTEST || !LAST_BACKTEST.events.length){
    err("No backtest events to export."); return;
  }
  const cols=Object.keys(LAST_BACKTEST.events[0]);
  const rows=[cols, ...LAST_BACKTEST.events.map(e=>cols.map(c=>e[c]))];
  const csv=rows.map(r=>r.map(v=>`"${String(v===null||v===undefined?"":v).replace(/"/g,'""')}"`).join(",")).join("\n");
  const blob=new Blob([csv],{type:"text/csv"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob); a.download="backtest_events.csv"; a.click();
  toast("Backtest events exported to CSV");
}

async function refreshStatus(){
  try{
    const s=await api("/api/status");
    $("status").innerHTML=`data as of <b>${s.as_of}</b> · <b>${s.universe_size}</b> symbols · ${s.history_years}y daily`+
      (s.mode==="demo"?`<span class="badge">DEMO DATA</span>`:``);
    ACTIVE_UNIVERSE_ID = s.universe_id || "nifty500";
    return s;
  }catch(e){$("status").textContent="backend unreachable"; return null}
}

// ---------------------------------------------------------------- universe selector (ROADMAP Item 15 Phase A)
async function loadUniverses(){
  try{
    const list=await api("/api/universes");
    const sel=$("universeSel");
    if(list.length<2){ sel.style.display="none"; return } // nothing to pick between yet
    sel.innerHTML=list.map(u=>`<option value="${u.id}" ${u.active?"selected":""}>${u.name}</option>`).join("");
    sel.style.display="";
  }catch(e){ /* selector just stays hidden */ }
}
async function switchUniverse(){
  const id=$("universeSel").value;
  const name=$("universeSel").selectedOptions[0].textContent;
  busy(true);
  const prevStatus=$("status").innerHTML;
  $("status").innerHTML=`switching to <b>${name}</b>… first load of a `+
    `universe in this server session can take a couple of minutes `+
    `(building indicators for every symbol); instant after that.`;
  try{
    await api("/api/universe",{id});
    await refreshStatus();
    rebuildPresetDropdown();  // some presets may not apply to the new universe
    toast(`Switched to ${name}`);
    // a screen/allocation/backtest from the previous universe no
    // longer applies — same reset a fresh screen run does.
    $("interp").style.display="none";
    $("stats").style.display="none";
    $("results").innerHTML="";
    LAST_MATCHES=[]; LAST_SPEC=null; spec=null;
    $("btnRun").disabled = tab==="en" ? !spec : false;
    $("cohortsPanel").style.display="none"; $("cohortsPanel").innerHTML="";
    $("btnCohorts").textContent="📈 cohorts";
    COHORTS_CACHE=[]; COHORTS_VIEW="list";
  }catch(e){
    err("Could not switch universe: "+e.message);
    $("status").innerHTML=prevStatus;
  }
  busy(false);
}

// ---------------------------------------------------------------- cohort tracker (ROADMAP Item 16)
// Walk-forward out-of-sample tracking — the complement to the backtester's
// in-sample event study. A cohort freezes a set of matches (or a sized
// allocation) at signal time and tracks them forward; nothing is ever
// dropped, even names that later delist or get suspended (flagged stale
// instead) — see screener/cohorts.py's module docstring for the full
// methodology this view surfaces.
let COHORTS_CACHE=[], COHORTS_VIEW="list", COHORTS_DETAIL_ID=null,
    COHORTS_SCORECARD_HASH=null, COHORTS_SCORECARD_CACHE={};

function _trackAsOf(){
  // ROADMAP Item 17: a screen run with an explicit as-of date (not
  // "latest") produces a REPLAY cohort when tracked — the future was
  // already visible when the screen ran, so it's in-sample by
  // construction and excluded from the OOS scorecard. `run()` always
  // stamps LAST_SPEC.as_of to either an explicit date or "latest".
  const a = LAST_SPEC && LAST_SPEC.as_of;
  return (a && a !== "latest") ? a : undefined;
}
async function trackMatchesAsCohort(btnEl){
  if(!LAST_SPEC || !LAST_MATCHES.length){ err("Run a screen with matches first."); return }
  try{
    const symbols = LAST_MATCHES.map(m=>m.symbol);
    const as_of = _trackAsOf();
    const j = await api("/api/cohorts", {spec: LAST_SPEC, symbols, as_of});
    if(btnEl){btnEl.textContent="📈 tracking"; btnEl.disabled=true}
    toast(j.mode==="replay"
      ? `Tracking ${symbols.length} matches as of ${j.as_of} (replay) — cohort ${j.cohort_id}`
      : `Tracking ${symbols.length} matches — cohort ${j.cohort_id}`);
  }catch(e){ err("Could not create cohort: "+e.message) }
}
async function trackAllocationAsCohort(btnEl){
  if(!LAST_SPEC || !LAST_ALLOCATION || !LAST_ALLOCATION.positions.length){
    err("Run an allocation first."); return
  }
  try{
    const positions = LAST_ALLOCATION.positions.map(p=>({symbol:p.symbol, value:p.value}));
    const as_of = _trackAsOf();
    const j = await api("/api/cohorts",
      {spec: LAST_SPEC, positions, method: LAST_ALLOCATION.method, as_of});
    if(btnEl){btnEl.textContent="📈 tracking"; btnEl.disabled=true}
    toast(j.mode==="replay"
      ? `Tracking portfolio as of ${j.as_of} (replay) — cohort ${j.cohort_id}`
      : `Tracking portfolio (${positions.length} positions) — cohort ${j.cohort_id}`);
  }catch(e){ err("Could not create cohort: "+e.message) }
}

function toggleCohorts(){
  const p=$("cohortsPanel"), opening=p.style.display==="none";
  p.style.display=opening?"block":"none";
  $("btnCohorts").textContent=opening?"hide cohorts":"📈 cohorts";
  if(opening){ COHORTS_VIEW="list"; loadCohortsList(); }
}
async function loadCohortsList(){
  const p=$("cohortsPanel");
  p.innerHTML=`<div class="pdesc" style="display:block">loading…</div>`;
  try{
    COHORTS_CACHE = await api("/api/cohorts");
    renderCohortsPanel();
  }catch(e){
    p.innerHTML=`<div class="pdesc" style="display:block">Could not load cohorts: ${e.message}</div>`;
  }
}
function backToCohortsList(){ COHORTS_VIEW="list"; renderCohortsPanel(); }
function openCohortSymbolChart(sym){
  const c=COHORTS_CACHE.find(x=>x.cohort_id===COHORTS_DETAIL_ID);
  if(!c) return;
  openChart(sym, c.spec, c.entry_date);
}
let COHORTS_PERF_CACHE={}, COHORTS_PERF_END=null, COHORTS_PERF_DATA=null,
    COHORTS_PERF_ERROR=null;
function openCohortDetail(id){
  COHORTS_VIEW="detail"; COHORTS_DETAIL_ID=id;
  COHORTS_PERF_END=null; COHORTS_PERF_DATA=null; COHORTS_PERF_ERROR=null;
  renderCohortsPanel();
  loadCohortDetailPerf();
}
async function loadCohortDetailPerf(){
  const c=COHORTS_CACHE.find(x=>x.cohort_id===COHORTS_DETAIL_ID);
  if(!c || !c.entry_date) return;  // pending — nothing to fetch yet
  // capture what THIS fetch is for, so a slower, now-stale in-flight
  // request (e.g. the initial default-window load, still pending when
  // the user clicks "apply" on a different end date) can't clobber a
  // newer one that already landed — only apply the result if nothing
  // has changed while it was in flight.
  const requestedId=c.cohort_id, requestedEnd=COHORTS_PERF_END;
  const key=requestedId+"|"+(requestedEnd||"");
  let data=null, error=null;
  try{
    if(!COHORTS_PERF_CACHE[key]){
      const qs = requestedEnd ? ("?end="+encodeURIComponent(requestedEnd)) : "";
      COHORTS_PERF_CACHE[key] = await api("/api/cohorts/"+requestedId+"/performance"+qs);
    }
    data = COHORTS_PERF_CACHE[key];
  }catch(e){
    error = e.message;
  }
  if(COHORTS_VIEW==="detail" && COHORTS_DETAIL_ID===requestedId
     && COHORTS_PERF_END===requestedEnd){
    COHORTS_PERF_DATA = data; COHORTS_PERF_ERROR = error;
    renderCohortsPanel();
  }
}
function applyCohortPerfEnd(){
  COHORTS_PERF_END = $("cohortPerfEnd").value || null;
  COHORTS_PERF_DATA = null; COHORTS_PERF_ERROR = null;
  renderCohortsPanel();
  loadCohortDetailPerf();
}
function resetCohortPerfEnd(){
  COHORTS_PERF_END = null; COHORTS_PERF_DATA = null; COHORTS_PERF_ERROR = null;
  renderCohortsPanel();
  loadCohortDetailPerf();
}
const PERF_SCOL={cohort:"var(--amber)", baseline:"var(--pass)", nifty:"#5B9BD5"};
function renderEquityCurveSvg(ec){
  if(!ec || !ec.dates || !ec.dates.length) return "";
  const W=680,H=160,P=8;
  const series=[["cohort",ec.cohort]];
  if(ec.baseline) series.push(["baseline",ec.baseline]);
  if(ec.nifty) series.push(["nifty",ec.nifty]);
  let vals=[100];
  series.forEach(([,a])=>{vals=vals.concat(a)});
  const mn=Math.min(...vals),mx=Math.max(...vals),sp=(mx-mn)||1;
  const n=ec.dates.length;
  const X=i=>P+(W-2*P)*i/((n-1)||1);
  const Y=v=>H-P-(H-2*P)*(v-mn)/sp;
  let svg=`<line x1="${P}" x2="${W-P}" y1="${Y(100).toFixed(1)}" y2="${Y(100).toFixed(1)}" stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1"/>`;
  let leg="";
  series.forEach(([name,a])=>{
    const color=PERF_SCOL[name];
    const path=a.map((v,i)=>`${i?"L":"M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    svg+=`<path d="${path}" fill="none" stroke="${color}" stroke-width="1.6"/>`;
    leg+=`<span style="color:${color}">— ${name}</span>`;
  });
  return `<div class="spark"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="equity curve, indexed to 100 at entry">${svg}</svg>
    <div class="sleg">${leg}<span style="float:right">${ec.dates[0]} → ${ec.dates[ec.dates.length-1]}</span></div></div>`;
}
function renderPerfMetrics(perf){
  const dd=perf.max_drawdown;
  const cells=[
    ["Cumulative gross", btSigned(perf.gross)],
    ["Cumulative net", btSigned(perf.net)],
    ["Excess vs. baseline (net)", btSigned(perf.excess_net_baseline)],
    ["Excess vs. Nifty (net)", perf.excess_net_nifty!==null?btSigned(perf.excess_net_nifty):"—"],
    ["Annualised vol", perf.annualized_vol!==null?btPct(perf.annualized_vol):"—"],
    ["Sharpe", perf.sharpe!==null?perf.sharpe.toFixed(2):"—"],
    ["Max drawdown", btPct(dd.pct)],
    ["Hit rate (positive)", btPct(perf.hit_rate_positive)],
    ["Hit rate (vs. baseline)", btPct(perf.hit_rate_vs_baseline)],
  ];
  return `<div class="mgrid" style="margin-top:8px">
      ${cells.map(([k,v])=>`<div class="m"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")}
    </div>
    ${perf.sharpe===null?`<div class="capnote">Sharpe: ${perf.sharpe_note}</div>`:""}
    <div class="capnote">Window ${perf.entry_date} → ${perf.end_date} (${perf.n_bars} bars) `+
    `· max drawdown peak ${dd.peak_date} → trough ${dd.trough_date}</div>`;
}
function renderContributorsTable(contributors){
  const rows=contributors.map(c=>`
    <tr style="border-bottom:1px dashed var(--line)">
      <td style="padding:5px 8px"><b>${c.symbol}</b></td>
      <td style="padding:5px 8px" class="mini">${(c.weight*100).toFixed(1)}%</td>
      <td style="padding:5px 8px">${btSigned(c.return_gross)}</td>
      <td style="padding:5px 8px">${btSigned(c.contribution_gross)}</td>
      <td style="padding:5px 8px" class="mini">${btPct(c.max_drawdown_pct)}</td>
    </tr>`).join("");
  return `<table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px;margin-top:6px">
    <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
      <th style="padding:5px 8px">Symbol</th><th style="padding:5px 8px">Weight</th>
      <th style="padding:5px 8px">Return</th><th style="padding:5px 8px">Contribution</th>
      <th style="padding:5px 8px">Own max DD</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}
function renderPerformanceSection(c){
  if(!c.entry_date){
    return `<div class="pdesc" style="display:block;margin-top:8px">Performance panel `+
      `available once this cohort is active (pending — no entry bar yet).</div>`;
  }
  const endControl = `<div class="row" style="margin-top:4px">
      <label class="asof">evaluate to <input type="date" id="cohortPerfEnd" value="${COHORTS_PERF_END||""}"></label>
      <button class="btnsm" onclick="applyCohortPerfEnd()" type="button">apply</button>
      ${COHORTS_PERF_END?`<button class="btnsm" onclick="resetCohortPerfEnd()" type="button">reset to latest</button>`:""}
    </div>`;
  if(COHORTS_PERF_ERROR){
    return endControl+`<div class="pdesc" style="display:block;margin-top:8px">Could not load performance: ${COHORTS_PERF_ERROR}</div>`;
  }
  if(!COHORTS_PERF_DATA){
    return endControl+`<div class="pdesc" style="display:block;margin-top:8px">loading performance…</div>`;
  }
  const perf=COHORTS_PERF_DATA;
  return endControl + renderEquityCurveSvg(perf.equity_curve) + renderPerfMetrics(perf)
    + `<div class="eyebrow" style="margin-top:10px">Contributors (weighted, best to worst)</div>`
    + renderContributorsTable(perf.contributors);
}
function openCohortScorecard(specHash){
  COHORTS_VIEW="scorecard"; COHORTS_SCORECARD_HASH=specHash;
  renderCohortsPanel();
  loadScorecard(specHash);
}
async function loadScorecard(specHash){
  try{
    const j = await api("/api/scorecard/"+specHash);
    COHORTS_SCORECARD_CACHE[specHash]=j;
    if(COHORTS_VIEW==="scorecard" && COHORTS_SCORECARD_HASH===specHash) renderCohortsPanel();
  }catch(e){
    if(COHORTS_VIEW==="scorecard" && COHORTS_SCORECARD_HASH===specHash){
      $("cohortsPanel").innerHTML=`<button class="btnsm" onclick="backToCohortsList()" type="button">← back to cohorts</button>
        <div class="pdesc" style="display:block;margin-top:8px">Could not load scorecard: ${e.message}</div>`;
    }
  }
}

function cohortAgeDays(c){
  if(!c.entry_date) return null;
  return Math.floor((Date.now()-new Date(c.entry_date).getTime())/86400000);
}
function cohortSpecSummary(c){
  if(c.notes) return c.notes;
  const conds=(c.spec.conditions||[]);
  return conds.map(x=>x.type).join(" + ")||"(no conditions)";
}
function replayBadge(c){
  return c.mode==="replay"
    ? `<span class="badge" title="Replay: as of ${c.as_of}, in-sample by construction — excluded from the OOS scorecard">REPLAY</span>`
    : "";
}
function nextMilestoneLabel(c){
  for(const h of [5,20,60]){ if(c.milestones[String(h)]===null) return `${h}-bar pending` }
  return "complete";
}
function renderCohortsPanel(){
  const p=$("cohortsPanel");
  if(COHORTS_VIEW==="detail"){ renderCohortDetail(p); return }
  if(COHORTS_VIEW==="scorecard"){ renderCohortScorecard(p); return }
  if(!COHORTS_CACHE.length){
    p.innerHTML=`<div class="pdesc" style="display:block">No cohorts tracked yet — click `+
      `"track these matches" on a screen's results, or "track this `+
      `portfolio" after allocating.</div>`;
    return;
  }
  const rows=COHORTS_CACHE.map(c=>{
    const age=cohortAgeDays(c), cur=c.current;
    return `
    <div class="recent-row" tabindex="0" role="button"
         aria-label="Open cohort ${c.cohort_id}"
         onclick="openCohortDetail('${c.cohort_id}')"
         onkeydown="onCardKey(event,'${c.cohort_id}',openCohortDetail)">
      <span class="sym" style="color:var(--amber);min-width:auto">${c.cohort_id}</span>${replayBadge(c)}
      <span class="mini">${c.status}${age!==null?` · ${age}d`:""}</span>
      <span class="mini">${c.symbols.length} symbols</span>
      <span class="mini">${cur && cur.net!==null?`current net ${btSigned(cur.net)}`:"—"}</span>
      <span class="mini">${nextMilestoneLabel(c)}</span>
      <span class="cname">${cohortSpecSummary(c)}</span>
      <button class="btnsm" onclick="event.stopPropagation();openCohortScorecard('${c.spec_hash}')" type="button">scorecard</button>
    </div>`;
  }).join("");
  p.innerHTML=`<div class="pdesc" style="display:block;margin-bottom:8px">Walk-forward `+
    `out-of-sample tracking — every tracked symbol stays in its cohort `+
    `even if later delisted or suspended (survivorship-free by `+
    `construction).</div>${rows}`;
}
function renderCohortDetail(p){
  const c=COHORTS_CACHE.find(x=>x.cohort_id===COHORTS_DETAIL_ID);
  if(!c){ p.innerHTML=`<button class="btnsm" onclick="backToCohortsList()" type="button">← back to cohorts</button>
    <div class="empty" style="margin-top:8px">Cohort not found.</div>`; return }
  const milestoneSummary=[5,20,60].map(h=>{
    const m=c.milestones[String(h)];
    if(!m) return `<div class="pdesc" style="display:block"><b>${h}-bar:</b> not reached yet</div>`;
    return `<div class="pdesc" style="display:block"><b>${h}-bar</b> `+
      `(frozen ${m.frozen_at.replace("T"," ").slice(0,16)}): gross ${btSigned(m.gross)} / `+
      `net ${btSigned(m.net)} vs. baseline ${btSigned(m.baseline)} → `+
      `excess (net) ${btSigned(m.excess_net)} · ${m.n_stale}/${m.n_symbols} stale</div>`;
  }).join("");
  const symRows=c.symbols.map(sym=>{
    const w=c.weights.by_symbol[sym];
    const cells=[5,20,60].map(h=>{
      const m=c.milestones[String(h)];
      const ps=m && m.per_symbol[sym];
      if(!ps || ps.return===null) return `<td style="padding:5px 8px" class="mini">—</td>`;
      return `<td style="padding:5px 8px">${btSigned(ps.return)}${ps.stale?
        ` <span class="mini" title="stopped trading before this milestone">stale</span>`:""}</td>`;
    }).join("");
    const cur=c.current && c.current.per_symbol[sym];
    const curCell=cur && cur.return!==null ? btSigned(cur.return) : "—";
    return `<tr style="border-bottom:1px dashed var(--line)">
      <td style="padding:5px 8px"><b class="sym" style="font-size:inherit;cursor:pointer" tabindex="0" role="button"
          aria-label="Chart ${sym} with entry marker"
          onclick="openCohortSymbolChart('${sym}')"
          onkeydown="onCardKey(event,'${sym}',openCohortSymbolChart)">${sym}</b></td>
      <td style="padding:5px 8px" class="mini">${(w*100).toFixed(1)}%</td>
      ${cells}
      <td style="padding:5px 8px">${curCell}</td>
    </tr>`;
  }).join("");
  p.innerHTML=`
    <button class="btnsm" onclick="backToCohortsList()" type="button">← back to cohorts</button>
    <div class="pdesc" style="display:block;margin-top:8px">
      <b style="color:var(--text)">${c.cohort_id}</b>${replayBadge(c)} · ${c.status} · universe ${c.universe}
      · created ${c.created_ts.replace("T"," ").slice(0,16)}
      ${c.mode==="replay"?` · as of ${c.as_of}`:""}
      ${c.entry_date?` · entered ${c.entry_date}`:" · pending entry"}
      ${c.notes?`<br>${c.notes}`:""}
    </div>
    ${c.survivorship_note?`<div class="evcaveat" style="opacity:.85;margin-top:4px">${c.survivorship_note}</div>`:""}
    <div class="row" style="margin-top:8px">
      <button class="btnsm" onclick="openCohortScorecard('${c.spec_hash}')" type="button">view scorecard for this spec</button>
    </div>
    <div class="eyebrow" style="margin-top:14px">Milestones</div>
    ${milestoneSummary}
    <div class="eyebrow" style="margin-top:14px">Symbols (${c.symbols.length}, ${c.weights.method} weighted)</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px">
      <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
        <th style="padding:5px 8px">Symbol</th><th style="padding:5px 8px">Weight</th>
        <th style="padding:5px 8px">5-bar</th><th style="padding:5px 8px">20-bar</th>
        <th style="padding:5px 8px">60-bar</th><th style="padding:5px 8px">Current</th>
      </tr></thead>
      <tbody>${symRows}</tbody>
    </table>
    <div class="evcaveat" style="margin-top:10px;opacity:.85">Survivorship-free by `+
      `construction — every tracked symbol stays in this cohort even if `+
      `later delisted or suspended (flagged stale above), never dropped.</div>
    <div class="eyebrow" style="margin-top:16px">Performance (deep dive)</div>
    ${renderPerformanceSection(c)}`;
}
function renderCohortScorecard(p){
  const j=COHORTS_SCORECARD_CACHE[COHORTS_SCORECARD_HASH];
  if(!j){
    p.innerHTML=`<button class="btnsm" onclick="backToCohortsList()" type="button">← back to cohorts</button>
      <div class="pdesc" style="display:block;margin-top:8px">loading…</div>`;
    return;
  }
  const rows=[5,20,60].map(h=>{
    const oos=j.horizons[String(h)];
    const is=j.in_sample ? j.in_sample[String(h)] : null;
    const isCell = !is ? `<span class="mini">no backtest logged</span>`
      : is.insufficient ? `<span class="mini">insufficient events</span>`
      : `${btSigned(is.excess_net.mean)} mean / ${btSigned(is.excess_net.median)} median `+
        `/ ${btPct(is.excess_net.hit_rate)} hit (${is.count} events)`;
    const oosCell = oos.insufficient
      ? `<span class="mini">insufficient sample (${oos.n_names} names)</span>`
      : `${btSigned(oos.mean_excess_net)} mean / ${btSigned(oos.median_excess_net)} median `+
        `/ ${btPct(oos.hit_rate)} hit (${oos.n_names} names, ${oos.n_cohorts} cohorts)`;
    return `<tr style="border-bottom:1px dashed var(--line)">
      <td style="padding:5px 8px">${h}-bar</td>
      <td style="padding:5px 8px">${isCell}</td>
      <td style="padding:5px 8px">${oosCell}</td>
    </tr>`;
  }).join("");
  p.innerHTML=`
    <button class="btnsm" onclick="backToCohortsList()" type="button">← back to cohorts</button>
    <div class="eyebrow" style="margin-top:10px">Scorecard — spec ${j.spec_hash}</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px;margin-top:6px">
      <thead><tr style="border-bottom:1px solid var(--line);text-align:left;color:var(--muted)">
        <th style="padding:5px 8px">Horizon</th><th style="padding:5px 8px">In-sample (backtest)</th>
        <th style="padding:5px 8px">Out-of-sample (cohorts)</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="capnote" style="margin-top:6px">${j.footnote}</div>
    <div class="evcaveat" style="margin-top:6px;opacity:.85">${j.survivorship_free_note}</div>`;
}

async function init(){
  try{
    BUILTIN_PRESETS = await api("/api/presets");
  }catch(e){ BUILTIN_PRESETS=[] }
  await refreshStatus();  // sets ACTIVE_UNIVERSE_ID before presets filter on it
  await loadUserPresets();
  await loadUniverses();
}

async function interpret(){
  err("");spec=null;$("interp").style.display="none";
  const q=$("qEn").value.trim();
  if(!q){err("Enter a screen description first.");return}
  busy(true);
  try{
    const r=await api("/api/parse",{query:q});
    spec=r.spec;
    $("english").textContent=r.english;
    $("specPre").textContent=JSON.stringify(r.spec,null,2);
    const a=$("assumptions");
    if(r.assumptions && r.assumptions.length){
      a.innerHTML=`<b style="color:var(--text)">Interpreted with defaults:</b> ${r.assumptions.join("; ")}`;
      a.style.display="block";
    } else { a.style.display="none" }
    $("interp").style.display="block";
    $("btnRun").disabled=false;
  }catch(e){err(e.message)}
  busy(false);
}

async function run(){
  err("");
  let s=spec;
  if(tab==="js"){
    try{s=JSON.parse($("qJs").value)}catch(e){err("Spec is not valid JSON: "+e.message);return}
  }
  if(!s){err("Interpret the query first.");return}
  const d = $("asOf").value;
  s = {...s, as_of: d || "latest"};
  busy(true);
  try{ render(await api("/api/screen",{spec:s})) }catch(e){err(e.message)}
  busy(false);
}

const SCOL=["#5B9BD5","#B07CC6","#6FBF9F","#D5A15B"]; // overlay palette
function spark(m){
  const s=m.spark; if(!s||!s.close||s.close.length<2) return "";
  const W=680,H=110,P=6;
  let vals=[...s.low,...s.high];
  Object.values(s.series).forEach(a=>vals=vals.concat(a.filter(v=>v!==null)));
  Object.values(s.levels).forEach(v=>vals.push(v));
  vals=vals.filter(v=>v!==null&&isFinite(v));
  const mn=Math.min(...vals),mx=Math.max(...vals),sp=(mx-mn)||1;
  const X=i=>P+(W-2*P)*i/(s.close.length-1);
  const Y=v=>H-P-(H-2*P)*(v-mn)/sp;
  const path=a=>a.map((v,i)=>v===null?"":`${i&&a[i-1]!==null?"L":"M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  let svg=`<path d="${path(s.close)}" fill="none" stroke="var(--amber)" stroke-width="1.6"/>`;
  let leg=`<span style="color:var(--amber)">— close</span>`;
  Object.entries(s.series).forEach(([k,a],n)=>{
    svg+=`<path d="${path(a)}" fill="none" stroke="${SCOL[n%4]}" stroke-width="1.1" opacity=".9"/>`;
    leg+=`<span style="color:${SCOL[n%4]}">— ${k.replace("_"," ").toUpperCase()}</span>`;});
  Object.entries(s.levels).forEach(([k,v])=>{
    svg+=`<line x1="${P}" x2="${W-P}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1"/>`;
    leg+=`<span>┄ ${k} ${v}</span>`;});
  return `<div class="spark"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="price chart">${svg}</svg>
    <div class="sleg">${leg}<span style="float:right">${s.dates[0]} → ${s.dates[s.dates.length-1]}</span></div></div>`;
}
const fmt=(v,suf="")=>v===null||v===undefined?"—":v+suf;
const signed=v=>v===null||v===undefined?"—":
  `<span class="${v>=0?"pos":"neg"}">${v>0?"+":""}${v}%</span>`;

// ---------------------------------------------------------------- full chart modal (ROADMAP Item 5)
let CHART_DATA=null, CHART_ZOOM=null;
const CHART_W=900, CHART_HP=340, CHART_HV=100, CHART_GAP=28, CHART_PAD=10;

let CHART_OPENER_EL=null, CHART_ENTRY_DATE=null;
async function openChart(symbol, spec=LAST_SPEC, entryDate=null){
  const modal=$("chartModal");
  CHART_OPENER_EL=document.activeElement;  // restore focus here on close
  CHART_ENTRY_DATE=entryDate;
  $("chartModalTitle").textContent=symbol+" — loading…";
  $("chartModalBody").innerHTML="";
  modal.style.display="flex";
  $("chartModalClose").focus();
  try{
    const j=await api("/api/chart",{symbol, spec});
    CHART_DATA=j; CHART_DATA.symbol=symbol; CHART_ZOOM=null;
    renderChart();
  }catch(e){
    $("chartModalTitle").textContent=symbol;
    $("chartModalBody").innerHTML=`<div class="empty">${e.message}</div>`;
  }
}
function closeChartModal(){
  $("chartModal").style.display="none";
  CHART_DATA=null; CHART_ZOOM=null; CHART_ENTRY_DATE=null;
  if(CHART_OPENER_EL){ CHART_OPENER_EL.focus(); CHART_OPENER_EL=null; }
}
document.addEventListener("keydown", e=>{
  if(e.key==="Escape" && $("chartModal").style.display!=="none"){
    closeChartModal();
  }
});

function renderChart(){
  const d=CHART_DATA; if(!d) return;
  const [s,e]=CHART_ZOOM||[0,d.dates.length-1];
  const n=e-s+1;
  const dates=d.dates.slice(s,e+1), open=d.open.slice(s,e+1), high=d.high.slice(s,e+1),
        low=d.low.slice(s,e+1), close=d.close.slice(s,e+1), vol=d.volume.slice(s,e+1);
  $("chartModalTitle").textContent=`${d.symbol} — ${dates[0]} to ${dates[dates.length-1]}`;

  let allVals=[...high,...low];
  Object.entries(d.series).forEach(([k,a])=>{allVals=allVals.concat(a.slice(s,e+1).filter(v=>v!==null))});
  Object.values(d.levels).forEach(v=>allVals.push(v));
  allVals=allVals.filter(v=>v!==null&&isFinite(v));
  const mn=Math.min(...allVals), mx=Math.max(...allVals), sp=(mx-mn)||1;
  const X=i=>CHART_PAD+(CHART_W-2*CHART_PAD)*i/((n-1)||1);
  const Y=v=>CHART_HP-CHART_PAD-(CHART_HP-2*CHART_PAD)*(v-mn)/sp;
  const bw=Math.max(1,(CHART_W-2*CHART_PAD)/n*0.6);

  let svg=`<svg viewBox="0 0 ${CHART_W} ${CHART_HP+CHART_GAP+CHART_HV}" preserveAspectRatio="none" style="width:100%;height:auto;cursor:crosshair;display:block" id="chartSvg">`;
  for(let idx=0; idx<n; idx++){
    const o=open[idx], c=close[idx], h=high[idx], l=low[idx];
    if(o===null||c===null||h===null||l===null) continue;
    const up=c>=o, color=up?"var(--pass)":"var(--fail)", x=X(idx);
    svg+=`<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${Y(h).toFixed(1)}" y2="${Y(l).toFixed(1)}" stroke="${color}" stroke-width="1"/>`;
    const yTop=Y(Math.max(o,c)), yBot=Y(Math.min(o,c));
    svg+=`<rect x="${(x-bw/2).toFixed(1)}" y="${yTop.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,yBot-yTop).toFixed(1)}" fill="${color}"/>`;
  }
  let leg="";
  Object.entries(d.series).forEach(([k,a],ci)=>{
    const seg=a.slice(s,e+1);
    const path=seg.map((v,idx)=>v===null?"":`${idx&&seg[idx-1]!==null?"L":"M"}${X(idx).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    svg+=`<path d="${path}" fill="none" stroke="${SCOL[ci%4]}" stroke-width="1.3" opacity=".9"/>`;
    leg+=`<span style="color:${SCOL[ci%4]}">— ${k.replace("_"," ").toUpperCase()}</span>`;
  });
  Object.entries(d.levels).forEach(([k,v])=>{
    svg+=`<line x1="${CHART_PAD}" x2="${CHART_W-CHART_PAD}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1"/>`;
    leg+=`<span>┄ ${k} ${v}</span>`;
  });
  if(CHART_ENTRY_DATE){
    const entryIdx=dates.indexOf(CHART_ENTRY_DATE);
    if(entryIdx>=0){
      const ex=X(entryIdx).toFixed(1);
      svg+=`<line x1="${ex}" x2="${ex}" y1="${CHART_PAD}" y2="${CHART_HP-CHART_PAD}" stroke="var(--pass)" stroke-width="1.5" stroke-dasharray="2 3"/>`;
      leg+=`<span style="color:var(--pass)">┆ cohort entry ${CHART_ENTRY_DATE}</span>`;
    }
  }
  const finiteVol=vol.filter(v=>v!==null&&isFinite(v));
  const vmax=finiteVol.length?Math.max(...finiteVol):1;
  const vBase=CHART_HP+CHART_GAP+CHART_HV-CHART_PAD;
  const VY=v=>vBase-(CHART_HV-2*CHART_PAD)*(v/(vmax||1));
  for(let idx=0;idx<n;idx++){
    const v=vol[idx]; if(v===null) continue;
    const up=close[idx]>=open[idx], x=X(idx), y=VY(v);
    svg+=`<rect x="${(x-bw/2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0,vBase-y).toFixed(1)}" fill="${up?"var(--pass)":"var(--fail)"}" opacity=".55"/>`;
  }
  svg+=`<line x1="0" x2="${CHART_W}" y1="${CHART_HP+CHART_GAP/2}" y2="${CHART_HP+CHART_GAP/2}" stroke="var(--line)" stroke-width="1"/>`;
  svg+="</svg>";

  $("chartModalBody").innerHTML=`
    <div class="sleg" style="margin-bottom:6px">${leg}
      ${CHART_ZOOM?` <button class="btnsm" onclick="resetZoom()" type="button">reset zoom</button>`:""}
    </div>
    ${svg}
    <div class="sleg" style="margin-top:6px">candles + volume · drag on the chart to zoom${CHART_ZOOM?"":""}</div>`;
  wireZoomDrag(s, e);
}

function resetZoom(){ CHART_ZOOM=null; renderChart(); }

function wireZoomDrag(curS, curE){
  const svgEl=document.getElementById("chartSvg");
  if(!svgEl) return;
  const n=curE-curS+1;
  let dragStartIdx=null;
  const idxFromClientX=(clientX)=>{
    const rect=svgEl.getBoundingClientRect();
    const relX=(clientX-rect.left)/rect.width*CHART_W;
    const frac=(relX-CHART_PAD)/(CHART_W-2*CHART_PAD);
    return Math.max(0,Math.min(n-1,Math.round(frac*(n-1))));
  };
  svgEl.onmousedown=(e)=>{ dragStartIdx=idxFromClientX(e.clientX); };
  svgEl.onmouseup=(e)=>{
    if(dragStartIdx===null) return;
    const dragEndIdx=idxFromClientX(e.clientX);
    const lo=Math.min(dragStartIdx,dragEndIdx), hi=Math.max(dragStartIdx,dragEndIdx);
    dragStartIdx=null;
    if(hi-lo<3) return;
    CHART_ZOOM=[curS+lo, curS+hi];
    renderChart();
  };
}

let LAST_MATCHES=[], NEARMISS_SHOWN=true, LAST_SPEC=null;
function csvEscape(v){
  const s=v===null||v===undefined?"":String(v);
  return /[",\n]/.test(s)?`"${s.replace(/"/g,'""')}"`:s;
}
function exportCsv(){
  if(!LAST_MATCHES.length){err("No matches to export.");return}
  const cols=["symbol","name","industry","close","pct_vs_ema50","rsi","adx",
    "vol_ratio","atr_pct","ret_1m_pct","ret_3m_pct","pct_from_52w_high",
    "turnover_cr","conditions_passed","conditions_total"];
  const rows=[cols.join(",")];
  LAST_MATCHES.forEach(m=>{
    const row=cols.map(c=>{
      if(c in m) return csvEscape(m[c]);
      return csvEscape(m.metrics?.[c]);
    });
    rows.push(row.join(","));
  });
  const blob=new Blob([rows.join("\n")],{type:"text/csv"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download=`screen_matches_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Matches exported to CSV");
}
function toggleDropped(){
  const box=$("droppedList"), opening=box.style.display==="none";
  box.style.display=opening?"block":"none";
  $("btnDropped").textContent=opening?"hide dropped":"show dropped";
}
function toggleNearMiss(){
  NEARMISS_SHOWN=!NEARMISS_SHOWN;
  const box=$("nearMissBox");
  if(box) box.style.display=NEARMISS_SHOWN?"":"none";
  const btn=$("btnNearMiss");
  if(btn) btn.textContent=NEARMISS_SHOWN?"hide":"show";
}

// ---------------------------------------------------------------- table ergonomics (ROADMAP Item 5)
let SORT_KEY="ret_3m_desc", SECTOR_FILTER=null, LAST_NEW_SET=new Set();
const SORT_OPTIONS=[
  ["ret_3m_desc","3M return ↓"],["ret_3m_asc","3M return ↑"],
  ["rsi_desc","RSI ↓"],["rsi_asc","RSI ↑"],
  ["close_desc","Price ↓"],["close_asc","Price ↑"],
  ["symbol_asc","Symbol A→Z"],
];
const SORT_CMP={
  ret_3m_desc:(a,b)=>(b.metrics.ret_3m_pct??-Infinity)-(a.metrics.ret_3m_pct??-Infinity),
  ret_3m_asc:(a,b)=>(a.metrics.ret_3m_pct??Infinity)-(b.metrics.ret_3m_pct??Infinity),
  rsi_desc:(a,b)=>(b.metrics.rsi??-Infinity)-(a.metrics.rsi??-Infinity),
  rsi_asc:(a,b)=>(a.metrics.rsi??Infinity)-(b.metrics.rsi??Infinity),
  close_desc:(a,b)=>(b.metrics.close??-Infinity)-(a.metrics.close??-Infinity),
  close_asc:(a,b)=>(a.metrics.close??Infinity)-(b.metrics.close??Infinity),
  symbol_asc:(a,b)=>a.symbol.localeCompare(b.symbol),
};
function filteredSortedMatches(){
  let arr=LAST_MATCHES;
  if(SECTOR_FILTER) arr=arr.filter(m=>m.industry===SECTOR_FILTER);
  return [...arr].sort(SORT_CMP[SORT_KEY]);
}
function onSortChange(v){ SORT_KEY=v; renderMatchesSection(); }
function toggleSectorChip(sector){
  SECTOR_FILTER=(SECTOR_FILTER===sector)?null:sector;
  renderMatchesToolbar();
  renderMatchesSection();
}
function renderMatchesToolbar(){
  const box=$("matchesToolbar");
  if(!LAST_MATCHES.length){ box.innerHTML=""; return }
  const sectors=[...new Set(LAST_MATCHES.map(m=>m.industry))].sort();
  const esc=s=>s.replace(/'/g,"\\'");
  const chips=sectors.map(s=>`<span class="chip ${SECTOR_FILTER===s?"active":""}" tabindex="0" role="button" aria-pressed="${SECTOR_FILTER===s}" onclick="toggleSectorChip('${esc(s)}')" onkeydown="onCardKey(event,'${esc(s)}',toggleSectorChip)">${s}</span>`).join("");
  box.innerHTML=`<div class="row" style="margin:4px 0 12px;align-items:center;flex-wrap:wrap">
    <label class="mini">sort <select class="sortsel" onchange="onSortChange(this.value)">
      ${SORT_OPTIONS.map(([v,l])=>`<option value="${v}" ${v===SORT_KEY?"selected":""}>${l}</option>`).join("")}
    </select></label>
    <span class="mini">sector:</span> ${chips}
    ${SECTOR_FILTER?`<button class="btnsm" onclick="toggleSectorChip('${esc(SECTOR_FILTER)}')" type="button">clear filter</button>`:""}
  </div>`;
}
function renderMatchesSection(){
  const sec=$("matchesSection");
  if(!LAST_MATCHES.length){
    sec.innerHTML=`<div class="empty">No stocks matched every condition. The near-misses below failed exactly one — loosen that condition if they look right.</div>`;
    return;
  }
  const list=filteredSortedMatches();
  sec.innerHTML = list.length ? list.map(m=>renderCard(m,false)).join("")
    : `<div class="empty">No matches in "${SECTOR_FILTER}" — clear the filter to see all ${LAST_MATCHES.length}.</div>`;
}
function toggleMatchCard(mhead){
  const card = mhead.closest(".match");
  const opening = card.classList.toggle("open");
  mhead.setAttribute("aria-expanded", opening ? "true" : "false");
}
function onCardKey(e, el, fn){
  // space/enter activation for divs standing in for buttons (keyboard
  // parity for the mouse-only click handlers below — ROADMAP Item 11
  // accessibility floor: the define->run->expand->allocate path must
  // be fully keyboard-operable, not just clickable).
  if(e.key==="Enter" || e.key===" "){ e.preventDefault(); fn(el); }
}
function renderCard(m,miss){
  return `
    <div class="match ${miss?"nearmiss":""}">
      <div class="mhead" tabindex="0" role="button" aria-expanded="false"
           onclick="toggleMatchCard(this)"
           onkeydown="onCardKey(event,this,toggleMatchCard)">
        <span class="sym">${m.symbol}</span>
        ${LAST_NEW_SET.has(m.symbol)?`<span class="newbadge">NEW</span>`:""}
        <span class="cname">${m.name} · ${m.industry}</span>
        <span class="mini">₹<b>${fmt(m.metrics.close)}</b></span>
        <span class="mini">3M ${signed(m.metrics.ret_3m_pct)}</span>
        <span class="mini">RSI <b>${fmt(m.metrics.rsi)}</b></span>
        <span class="mini"><b class="${miss?"neg":"pos"}">${m.conditions_passed}/${m.conditions_total}</b> conditions</span>
        ${m.flags && m.flags.length ? `<span class="mini" style="color:var(--fail)" title="${m.flags.map(f=>f.reason).join(" | ").replace(/"/g,"&quot;")}">⚠ ${m.flags.length} data flag${m.flags.length>1?"s":""}</span>` : ""}
      </div>
      <div class="mbody">
        ${spark(m)}
        <button class="btnsm" onclick="event.stopPropagation();openChart('${m.symbol}')" type="button">full chart ⤢</button>
        <button class="btnsm" onclick="event.stopPropagation();addToWatchlist('${m.symbol}',this)" type="button">☆ watch</button>
        <div class="eyebrow">Evidence — why this ${miss?"almost matched":"matched"}</div>
        <div class="ledger">
          ${m.evidence.map(e=>`
            <div class="cond">
              <span class="mark ${e.passed?"ok":"no"}">${e.passed?"✓":"✗"}</span>
              <span class="cdesc">${e.description}</span>
              <span class="cev">observed: <span class="num">${e.evidence}</span></span>
            </div>`).join("")}
        </div>
        <div class="eyebrow" style="margin-top:16px">Snapshot metrics (latest bar)</div>
        <div class="mgrid">
          ${[["Close ₹",m.metrics.close],["vs EMA50 %",m.metrics.pct_vs_ema50],
             ["RSI(14)",m.metrics.rsi],["ADX(14)",m.metrics.adx],
             ["Vol ratio ×",m.metrics.vol_ratio],["ATR %",m.metrics.atr_pct],
             ["1M ret %",m.metrics.ret_1m_pct],["3M ret %",m.metrics.ret_3m_pct],
             ["vs 52w high %",m.metrics.pct_from_52w_high],
             ["Med turnover ₹cr",m.metrics.turnover_cr]]
            .map(([k,v])=>`<div class="m"><div class="k">${k}</div><div class="v">${fmt(v)}</div></div>`).join("")}
        </div>
      </div>
    </div>`;
}

function render(r){
  // interpretation always shown with results — the audit header
  $("english").textContent=r.english;
  $("specPre").textContent=JSON.stringify(r.spec,null,2);
  $("interp").style.display="block";
  const warnBox=$("screenWarnings");
  if(r.warnings && r.warnings.length){
    warnBox.textContent=r.warnings.join(" ");
    warnBox.style.display="block";
  }else{
    warnBox.style.display="none";
  }
  LAST_MATCHES=r.matches;
  LAST_SPEC=r.spec;
  SORT_KEY="ret_3m_desc"; SECTOR_FILTER=null;
  RECENT_LOADED=false; // this run just appended to the log — refetch next open
  LAST_ALLOCATION=null;
  $("allocatePanel").style.display="none";
  $("btnAllocate").textContent="💰 allocate";
  LAST_BACKTEST=null;
  $("backtestPanel").style.display="none";
  $("btnBacktest").textContent="🧪 backtest";
  $("btnTrackCohort").textContent="📈 track these matches";
  $("btnTrackCohort").disabled=false;

  const st=r.stats, cells=[
    ["Universe",st.universe],["Liquidity-excluded",st.liquidity_excluded],
    ["Evaluated",st.evaluated],["Matched",st.matched,true],
    ["Near misses",st.near_misses],["As of",r.as_of]];
  $("cells").innerHTML=cells.map(([k,v,hl])=>
    `<div class="cell"><div class="k">${k}</div><div class="v ${hl?"hl":""}">${v}</div></div>`).join("");
  $("stats").style.display="block";

  const diffBox=$("diffBox");
  LAST_NEW_SET=new Set((r.diff&&r.diff.new)||[]);
  if(r.diff){
    const dropped=r.diff.dropped||[];
    diffBox.style.display="block";
    diffBox.innerHTML=`<b style="color:var(--text)">Since last run</b> `+
      `(<span class="pos">+${r.diff.new.length} new</span>, `+
      `<span class="neg">-${dropped.length} dropped</span>)`+
      (dropped.length?` <button class="toggle" id="btnDropped" onclick="toggleDropped()" type="button" style="text-transform:none">show dropped</button>`:"")+
      `<div id="droppedList" style="display:none;margin-top:8px">`+
      dropped.map(d=>`<div class="dropped-row"><span class="sym neg">${d.symbol}</span><span>${d.reason}</span></div>`).join("")+
      `</div>`;
  } else {
    diffBox.style.display="none";
  }

  let html="";
  html+=`<div class="sechead sticky">Matches (${r.matches.length}) — click any row for the full evidence trail</div>`;
  if(r.stats.matched>r.matches.length){
    html+=`<div class="capnote">Showing the top ${r.matches.length} of ${r.stats.matched} matches (sorted by 3-month return) — refine the screen for a tighter list, or export CSV for the full displayed set.</div>`;
  }
  html+=`<div id="matchesToolbar"></div><div id="matchesSection"></div>`;
  if(r.near_misses.length){
    NEARMISS_SHOWN=true;
    html+=`<div class="sechead">Near misses — failed exactly one condition
      <button class="toggle" id="btnNearMiss" onclick="toggleNearMiss()" type="button">hide</button></div>`;
    html+=`<div id="nearMissBox">${r.near_misses.map(m=>renderCard(m,true)).join("")}</div>`;
  }
  $("results").innerHTML=html;
  renderMatchesToolbar();
  renderMatchesSection();

  $("foot").innerHTML=`<b style="color:var(--text)">Methodology.</b> ${r.methodology.data}. Liquidity gate: ${r.methodology.liquidity_gate}. Missing-data policy: ${r.methodology.nan_policy}. Every screen is fully determined by the compiled spec above plus the as-of date <i>and</i> the effective config (hash <code>${r.methodology.config_hash}</code>) — rerun with the same spec on the same data and config and you get the same result. Definitions of every condition: TECHNICAL_DESIGN.md.`;
  window.scrollTo({top:$("stats").offsetTop-16,behavior:"smooth"});
}
init();
