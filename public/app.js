// ── Config ──────────────────────────────────────────────────────────────────
const API_URL = "/api/convert"; // Relative — works on Vercel (same domain)
const RECAPTCHA_SITE_KEY = "YOUR_RECAPTCHA_SITE_KEY"; // Replace with your site key
const MAX_FILES = 10;
const MAX_FILE_SIZE_MB = 10;

// ── State ────────────────────────────────────────────────────────────────────
let files = []; // Array of File objects (ordered)

// ── DOM refs ─────────────────────────────────────────────────────────────────
const dropZone     = document.getElementById("drop-zone");
const fileInput    = document.getElementById("file-input");
const browseBtn    = document.getElementById("browse-btn");
const previewList  = document.getElementById("preview-list");
const convertBtn   = document.getElementById("convert-btn");
const clearBtn     = document.getElementById("clear-btn");
const statusEl     = document.getElementById("status");

// ── Drag & drop ──────────────────────────────────────────────────────────────
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  addFiles([...e.dataTransfer.files]);
});
dropZone.addEventListener("click", (e) => {
  if (e.target === browseBtn || e.target === dropZone || e.target.closest(".drop-hint")) {
    fileInput.click();
  }
});
browseBtn.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener("change", () => { addFiles([...fileInput.files]); fileInput.value = ""; });

// ── File management ──────────────────────────────────────────────────────────
function addFiles(newFiles) {
  const allowed = ["image/jpeg", "image/png", "image/webp"];
  for (const f of newFiles) {
    if (!allowed.includes(f.type)) { showStatus(`"${f.name}" is not a supported image type.`, "error"); continue; }
    if (f.size > MAX_FILE_SIZE_MB * 1024 * 1024) { showStatus(`"${f.name}" exceeds ${MAX_FILE_SIZE_MB}MB.`, "error"); continue; }
    if (files.length >= MAX_FILES) { showStatus(`Maximum ${MAX_FILES} images allowed.`, "error"); break; }
    files.push(f);
  }
  renderPreviews();
  updateButtons();
}

function removeFile(index) {
  files.splice(index, 1);
  renderPreviews();
  updateButtons();
}

function renderPreviews() {
  previewList.innerHTML = "";
  files.forEach((file, i) => {
    const li = document.createElement("li");
    li.className = "preview-item";

    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    img.alt = file.name;

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = `${i + 1}. ${file.name}`;

    const size = document.createElement("span");
    size.className = "size";
    size.textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB`;

    const removeBtn = document.createElement("button");
    removeBtn.className = "remove-btn";
    removeBtn.textContent = "✕";
    removeBtn.title = "Remove";
    removeBtn.onclick = () => removeFile(i);

    li.append(img, name, size, removeBtn);
    previewList.appendChild(li);
  });
}

function updateButtons() {
  const hasFiles = files.length > 0;
  convertBtn.disabled = !hasFiles;
  clearBtn.disabled = !hasFiles;
}

clearBtn.addEventListener("click", () => {
  files = [];
  renderPreviews();
  updateButtons();
  hideStatus();
});

// ── Convert ──────────────────────────────────────────────────────────────────
convertBtn.addEventListener("click", async () => {
  if (files.length === 0) return;

  showStatus("🔄 Extracting text and generating PDF…", "loading");
  convertBtn.disabled = true;

  try {
    // Get reCAPTCHA v3 token if configured, else use fallback
    let token = "dev_bypass_token";
    if (typeof grecaptcha !== "undefined" && RECAPTCHA_SITE_KEY && !RECAPTCHA_SITE_KEY.startsWith("YOUR_")) {
      try {
        token = await grecaptcha.execute(RECAPTCHA_SITE_KEY, { action: "convert" });
      } catch (recaptchaErr) {
        console.warn("reCAPTCHA execution skipped or failed:", recaptchaErr);
      }
    }

    const formData = new FormData();
    files.forEach((f) => formData.append("images", f));
    formData.append("recaptcha_token", token);

    const response = await fetch(API_URL, { method: "POST", body: formData });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: "Unknown error" }));
      throw new Error(err.detail || `Server error ${response.status}`);
    }

    // Trigger download
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "document.pdf";
    a.click();
    URL.revokeObjectURL(url);

    showStatus("✅ PDF downloaded!", "success");
  } catch (err) {
    showStatus(`❌ ${err.message}`, "error");
  } finally {
    convertBtn.disabled = files.length === 0;
  }
});

// ── Status helpers ────────────────────────────────────────────────────────────
function showStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = `status ${type}`;
}
function hideStatus() {
  statusEl.className = "status hidden";
}
