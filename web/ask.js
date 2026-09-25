// Shared by the main screen's Ask panel and the full /ask page.
// Expects #ask-form, #question, #send, #suggest and #answer in the page.
const ASK_SUGGESTIONS = [
  "Where's the nearest open shelter to Hilo?",
  "Do I still have time to buy supplies in Hilo?",
  "Which supplies are running out in 96720?",
  "Is it safe to drive to a store in Kona right now?",
];

function initAsk({ onAnswered } = {}) {
  const answerPanel = document.getElementById("answer");
  const questionBox = document.getElementById("question");
  const sendButton = document.getElementById("send");

  function renderAnswer(data) {
    const sources = (data.sources || []).map((row) => `<li>${link(row.url, row.label || row.title)}</li>`).join("");
    answerPanel.innerHTML = `
      <p class="answer">${esc(data.answer)}</p>
      ${data.safety ? `<p class="panel amber">${esc(data.safety)}</p>` : ""}
      <p class="small muted">Answered from the snapshot saved at ${esc(data.snapshot_hst || "an unknown time")}
        ${data.online ? "" : "&middot; internet is down, nothing live was fetched"}
        &middot; ${esc(data.answered_by || "")}</p>
      ${sources ? `<h2>Sources</h2><ul class="links">${sources}</ul>` : ""}
      <details><summary>Facts the model was given (${(data.facts_used || []).length})</summary>
        <pre>${esc((data.facts_used || []).join("\n"))}</pre></details>`;
    answerPanel.classList.remove("hidden");
  }

  async function submit() {
    const question = questionBox.value.trim();
    if (!question) return;
    sendButton.disabled = true;
    answerPanel.classList.remove("hidden");
    answerPanel.setAttribute("aria-live", "polite");
    answerPanel.innerHTML = `<div class="skel" role="status" aria-label="Answering"><i></i><i class="w80"></i><i class="w60"></i></div>` +
      `<p class="skel-note">Reading the saved data and asking the local model…</p>`;
    try {
      renderAnswer(await postJSON("/api/ask", { question }));
    } catch (error) {
      answerPanel.innerHTML = navigator.onLine === false
        ? `<p>This phone has no connection, and asking needs to reach Shelfwatch. The saved data on this screen still works. In an emergency call 911.</p>`
        : `<p class="muted">The answer did not come back (${esc(error.message)}). The local model server may be down — the rest of this screen still shows the saved data.</p>`;
    } finally {
      sendButton.disabled = false;
      if (onAnswered) onAnswered();
    }
  }

  document.getElementById("suggest").innerHTML = ASK_SUGGESTIONS
    .map((text, index) => `<button type="button" data-index="${index}">${esc(text)}</button>`).join("");
  document.querySelectorAll("#suggest button").forEach((button) => {
    button.onclick = () => {
      questionBox.value = ASK_SUGGESTIONS[Number(button.dataset.index)];
      submit();
    };
  });
  document.getElementById("ask-form").onsubmit = (event) => {
    event.preventDefault();
    submit();
  };
  questionBox.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
  });

  // ?q=... loads with the question ready and asks it, so the demo needs no typing.
  const preset = new URLSearchParams(window.location.search).get("q");
  if (preset) {
    questionBox.value = preset;
    submit();
  }
}
