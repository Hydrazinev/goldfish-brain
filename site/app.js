"use strict";

// Illustrations only. This public site never connects to the local app or captures a screen.
const demoNames = { identify: "Character file", ask: "Ask about this scene", react: "Watch buddy" };
const demoButtons = document.querySelectorAll("[data-demo]");
const demoAnnouncement = document.getElementById("demo-announcement");

demoButtons.forEach(button => {
  button.addEventListener("click", () => {
    const selected = button.dataset.demo;
    if (!Object.hasOwn(demoNames, selected)) return;
    demoButtons.forEach(item => {
      const active = item.dataset.demo === selected;
      item.setAttribute("aria-pressed", String(active));
      document.getElementById(`${item.dataset.demo}-demo`).hidden = !active;
    });
    document.getElementById("card-label").textContent = demoNames[selected];
    demoAnnouncement.textContent = `${demoNames[selected]} preview selected. Illustrative FROM preview.`;
  });
});

const positions = [
  { boundary: "AS OF START OF S1E3", note: "The town’s self-appointed sheriff, trying to keep the residents safe." },
  { boundary: "AS OF START OF S2E1", note: "Your notes would update here as you reach a new season. Later events stay hidden in this preview." },
  { boundary: "AS OF START OF S2E5", note: "Only context up to this episode would appear here. This public preview contains no later plot details." }
];
const episodeButtons = document.querySelectorAll("[data-episode]");
episodeButtons.forEach(button => {
  button.addEventListener("click", () => {
    const position = positions[Number(button.dataset.episode)];
    if (!position) return;
    episodeButtons.forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    document.getElementById("episode-boundary").textContent = position.boundary;
    document.getElementById("episode-note").textContent = position.note;
  });
});

const copyButton = document.getElementById("copy-command");
const copyStatus = document.getElementById("copy-status");
let resetCopy;
copyButton.addEventListener("click", async () => {
  const command = document.getElementById("install-command");
  const text = command.textContent.trim();
  clearTimeout(resetCopy);
  copyButton.disabled = true;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    copyStatus.textContent = "Copied. Paste into Terminal to get started.";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(command);
    const selection = window.getSelection();
    if (selection) { selection.removeAllRanges(); selection.addRange(range); }
    copyStatus.textContent = "Command selected. Press ⌘C (or Ctrl+C) to copy.";
  } finally { copyButton.disabled = false; }
  resetCopy = setTimeout(() => { copyStatus.textContent = ""; }, 6000);
});
