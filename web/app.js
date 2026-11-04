const $ = id => document.getElementById(id);
let config, selected, currentBundle, originalBundleText, polling = false;
const stageIndex = { queued: -1, reproducing: 0, planning: 1, retrying: 1, testing: 2, verified: 4, failed: 3, unavailable: -1, interrupted: -1 };
const terminal = new Set(["verified", "failed", "unavailable", "interrupted"]);
const show = (id, visible = true) => $(id).classList.toggle("hidden", !visible);
const text = (id, value) => { $(id).textContent = value; };
function error(message) { text("error", message); show("error"); }
function node(tag, value, className) { const element = document.createElement(tag); element.textContent = value; if (className) element.className = className; return element; }

async function request(url, options) {
  const response = await fetch(url, { ...options, credentials: "same-origin", cache: "no-store" });
  const raw = await response.text();
  if (raw.length > 1024 * 1024) throw new Error("The response exceeds the evidence size limit.");
  let value;
  try { value = JSON.parse(raw); } catch { throw new Error("The repair service did not return a valid response."); }
  if (!response.ok) throw new Error(value.error || "The repair service could not complete the request.");
  return { value, raw };
}

function select(fixture) {
  if (polling) return;
  selected = fixture;
  text("fixture-title", fixture.title); text("fixture-description", fixture.description);
  text("source", fixture.source); text("example", JSON.stringify(fixture.example, null, 2));
  document.querySelectorAll(".fixture").forEach(button => { const active = button.dataset.id === fixture.id; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); });
  show("error", false);
}

function renderFixtures(fixtures) {
  $("fixture-list").replaceChildren();
  fixtures.forEach((fixture, index) => {
    const button = node("button", "", "fixture"); button.type = "button"; button.dataset.id = fixture.id;
    button.append(node("span", String(index + 1).padStart(2, "0"), "fixture-index"), node("strong", fixture.title), node("span", ["Calendar rollover", "Integer cents", "Event idempotency"][index] || "Independent checks"));
    button.addEventListener("click", () => select(fixture)); $("fixture-list").append(button);
  });
  select(fixtures[0]);
}

function progress(status, message, reference = false) {
  show("empty-result", false); show("result"); text("job-message", message);
  text("origin", reference ? "Reference sandbox execution · disclosed repair · no AI call" : "Live repair · Vultr Serverless Inference · independently evaluated output");
  text("proposal-label", reference ? "Reference repair" : "Propose");
  text("proposal-note", reference ? "Disclosed CI repair, no model call" : "A bounded repair candidate");
  const index = stageIndex[status] ?? -1;
  document.querySelectorAll("#timeline li").forEach((item, i) => { item.classList.toggle("done", i < index); item.classList.toggle("current", i === index && !terminal.has(status)); });
  text("result-badge", status === "verified" ? (reference ? "Reference passes" : "Repair passes") : (status === "failed" ? "Not verified" : status));
  $("result-badge").className = "badge" + (status === "verified" ? " pass" : status === "failed" ? " fail" : "");
}

function normalized(value) {
  if (Array.isArray(value)) return value.map(normalized);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, normalized(value[key])]));
  return value;
}
async function sha256(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
async function verify(value) {
  if (value.schema_version !== 2 || typeof value.canonical_payload !== "string" || !value.payload) throw new Error("Unsupported evidence schema.");
  if (JSON.stringify(normalized(JSON.parse(value.canonical_payload))) !== JSON.stringify(normalized(value.payload))) throw new Error("Displayed evidence differs from its canonical bytes.");
  if (await sha256(value.canonical_payload) !== value.sha256) throw new Error("Evidence integrity check failed.");
  const payload = value.payload;
  if (await sha256(payload.source) !== payload.source_sha256) throw new Error("Original source fingerprint differs.");
  if (payload.candidate && await sha256(payload.candidate.code) !== payload.candidate.sha256) throw new Error("Candidate source fingerprint differs.");
  for (const key of ["baseline", "evaluation"]) {
    const run = payload[key];
    if (run && (run.runtime !== "runsc" || !run.policy_verified || !run.teardown?.confirmed || !Object.values(run.policy || {}).every(Boolean))) throw new Error("Sandbox policy or removal was not confirmed.");
  }
  if (payload.baseline && payload.baseline.source_sha256 !== payload.source_sha256) throw new Error("Baseline source differs.");
  if (payload.evaluation && payload.evaluation.source_sha256 !== payload.candidate?.sha256) throw new Error("Evaluated candidate differs.");
  if (payload.status === "verified" && (!payload.baseline?.valid_protocol || payload.baseline.failed < 1 || !payload.evaluation?.valid_protocol || payload.evaluation.failed !== 0 || payload.evaluation.passed < 1)) throw new Error("The recorded checks did not pass.");
  return true;
}

async function displayEvidence(value, raw) {
  currentBundle = value; originalBundleText = raw;
  const payload = value.payload;
  const reference = payload.evidence_origin === "ci-reference-evaluation";
  progress(payload.status, payload.message, reference);
  show("totals", Boolean(payload.baseline || payload.evaluation));
  text("baseline-failed", String(payload.baseline?.failed ?? "—")); text("candidate-passed", String(payload.evaluation?.passed ?? "—"));
  show("bundle-check");
  try { await verify(value); text("bundle-check", "✓ Evidence integrity, source fingerprints and recorded sandbox checks agree."); $("bundle-check").classList.remove("fail"); }
  catch (failure) { text("bundle-check", failure.message); $("bundle-check").classList.add("fail"); text("result-badge", "Check needs review"); $("result-badge").className = "badge fail"; }
  show("evidence-actions");
  $("fingerprint-list").replaceChildren();
  for (const [name, hash] of [["Original source", payload.source_sha256], ["Candidate source", payload.candidate?.sha256], ["Sandbox image", payload.environment?.image_ref], ["Evidence", value.sha256]]) if (hash) $("fingerprint-list").append(node("dt", name), node("dd", hash));
  show("repair", Boolean(payload.candidate));
  if (payload.candidate) {
    text("candidate-code", payload.candidate.code); text("explanation", payload.candidate.explanation);
    text("candidate-provider", reference ? "Reference fixture · no AI call" : payload.candidate.provider?.model || "Vultr inference");
    text("replay-command", "python3 -m reprolab.cli replay reprolab-" + payload.fixture_id + ".json");
  }
}

$("example-run").addEventListener("click", async () => {
  show("error", false); $("example-run").disabled = true;
  try { const {value, raw} = await request("/examples/reference-" + selected.id + ".json"); await displayEvidence(value, raw); }
  catch (failure) { error(failure.message); }
  finally { $("example-run").disabled = !config?.examples_available; }
});

$("run").addEventListener("click", async () => {
  if (polling || !config?.available || !selected) return;
  show("error", false); show("repair", false); show("totals", false); show("bundle-check", false); show("evidence-actions", false);
  polling = true; $("run").disabled = true; $("example-run").disabled = true;
  document.querySelectorAll(".fixture").forEach(button => { button.disabled = true; });
  try {
    const headers = {"Content-Type": "application/json"};
    if (config.access_required) headers["X-Reprolab-Access"] = $("access").value;
    const {value: job} = await request("/api/jobs", {method:"POST", headers, body:JSON.stringify({fixture_id: selected.id})});
    progress("queued", "Waiting for the bounded repair worker.");
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline) {
      const {value: status} = await request("/api/jobs/" + job.id, {headers:{Authorization:"Bearer " + job.token}});
      progress(status.status, status.message);
      if (terminal.has(status.status)) {
        if (status.evidence) { const evidence = await request("/api/jobs/" + job.id + "/evidence", {headers:{Authorization:"Bearer " + job.token}}); await displayEvidence(evidence.value, evidence.raw); }
        else error(status.message);
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    if (Date.now() >= deadline) error("This job is still running. The server's execution and retry budgets remain bounded.");
  } catch (failure) { error(failure.message); }
  finally { polling = false; $("run").disabled = !config.available; $("example-run").disabled = !config.examples_available; document.querySelectorAll(".fixture").forEach(button => { button.disabled = false; }); }
});

$("download").addEventListener("click", () => {
  if (!currentBundle || !originalBundleText) return;
  const blob = new Blob([originalBundleText], {type:"application/json"});
  const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "reprolab-" + currentBundle.payload.fixture_id + ".json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});

try {
  const result = await request("/api/config"); config = result.value;
  renderFixtures(config.fixtures);
  text("connection", config.available ? "Vultr repairs connected" : "Live repairs awaiting connection"); $("connection").classList.toggle("live", config.available);
  text("mode-note", config.available ? "A real Vultr inference call proposes the repair. Every execution gets a fresh gVisor sandbox." : "Live repairs are not enabled in this preview. Sandbox examples contain real CI execution of disclosed reference repairs; no model was called.");
  $("run").disabled = !config.available; $("example-run").disabled = !config.examples_available; show("access-label", config.access_required);
} catch (failure) { error(failure.message); text("connection", "Repair service unavailable"); text("mode-note", "The repair service is not connected."); }
