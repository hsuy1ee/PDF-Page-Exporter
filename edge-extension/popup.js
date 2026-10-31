let currentFilePath = null;
let busy = false;
let ready = false;
const pagesInput = document.getElementById("pages");
const folderNameInput = document.getElementById("folder-name");
const allPagesInput = document.getElementById("all-pages");
const exportButton = document.getElementById("export");

function filePathFromUrl(url) {
  if (!url || !url.startsWith("file:///")) return null;
  let path = decodeURIComponent(new URL(url).pathname);
  if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
  return path.replaceAll("/", "\\");
}

function setStatus(message, isError = false) {
  const status = document.getElementById("status");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

async function identifyCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentFilePath = filePathFromUrl(tab?.url);
  const source = document.getElementById("source");
  if (currentFilePath?.toLowerCase().endsWith(".pdf")) {
    source.textContent = `当前文件：${currentFilePath.split("\\").pop()}`;
  } else {
    currentFilePath = null;
    source.textContent = "未识别到本地 PDF；点击导出后请选择文件。";
  }
}

function updateExportAvailability() {
  const exportingAll = allPagesInput.checked;
  pagesInput.disabled = exportingAll;
  exportButton.disabled = !ready || busy || (!exportingAll && !pagesInput.value.trim());
}

function showExportState(state) {
  busy = state?.status === "running";
  if (state) setStatus(state.message, state.status === "error");
  updateExportAvailability();
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.exportState) {
    showExportState(changes.exportState.newValue);
  }
});

pagesInput.addEventListener("input", updateExportAvailability);
allPagesInput.addEventListener("change", updateExportAvailability);
function exportOnEnter(event) {
  if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229 && !exportButton.disabled) {
    event.preventDefault();
    exportButton.click();
  }
}

pagesInput.addEventListener("keydown", exportOnEnter);
folderNameInput.addEventListener("keydown", exportOnEnter);

exportButton.addEventListener("click", async () => {
  const exportingAll = allPagesInput.checked;
  const pages = pagesInput.value.trim();
  if (!exportingAll && !pages) {
    setStatus("请先输入页码范围，或勾选导出全部页面。", true);
    return;
  }
  const wholeDocumentRange = pages.replace(/\s/g, "") === "1-";
  if ((exportingAll || wholeDocumentRange) && !window.confirm("确认导出这个 PDF 的全部页面吗？页面较多时可能需要较久。")) {
    return;
  }
  if (busy || !ready) return;
  busy = true;
  updateExportAvailability();
  setStatus("正在导出，请稍候…");
  try {
    const response = await chrome.runtime.sendMessage({ type: "start-export", request: {
      sourcePath: currentFilePath,
      pages: exportingAll ? "" : pages,
      folderName: folderNameInput.value.trim(),
      format: document.getElementById("format").value,
      dpi: Number(document.getElementById("dpi").value)
    } });
    if (!response?.state) throw new Error(response?.error || "无法启动后台导出任务。");
    showExportState(response.state);
  } catch (error) {
    busy = false;
    setStatus(`导出失败：${error.message}`, true);
    updateExportAvailability();
  }
});

async function initialize() {
  await identifyCurrentTab().catch(() => {
    document.getElementById("source").textContent = "无法读取当前标签页；点击导出后请选择文件。";
  });
  try {
    const response = await chrome.runtime.sendMessage({ type: "export-state" });
    if (response?.error) throw new Error(response.error);
    // Completed results belong to the previous popup session, not this PDF.
    showExportState(response?.state?.status === "complete" ? null : response?.state);
    ready = true;
  } catch (error) {
    setStatus(`无法读取导出状态：${error.message}。请重新打开插件。`, true);
  }
  updateExportAvailability();
}

void initialize();

updateExportAvailability();
// Delay by one task so Edge has finished placing the popup before focusing it.
setTimeout(() => pagesInput.focus(), 0);
