const DEFAULT_RELAY_URL = "http://127.0.0.1:18792";

async function load() {
  const stored = await chrome.storage.local.get(["relayBaseUrl", "relayPort", "relayToken"]);
  const fallbackUrl = stored.relayPort ? `http://127.0.0.1:${stored.relayPort}` : DEFAULT_RELAY_URL;
  document.getElementById("relay-url").value = String(stored.relayBaseUrl || fallbackUrl);
  document.getElementById("token").value = String(stored.relayToken || "");
}

async function save() {
  const relayBaseUrl = String(document.getElementById("relay-url").value || "").trim().replace(/\/+$/, "") || DEFAULT_RELAY_URL;
  const token = String(document.getElementById("token").value || "").trim();
  await chrome.storage.local.set({ relayBaseUrl, relayToken: token });
  document.getElementById("status").textContent = `Saved relay settings for ${relayBaseUrl}`;
}

document.getElementById("save").addEventListener("click", () => void save());
void load();
