const HOST_NAME = "com.xsy.pdf_page_exporter";
let activeJob = null;

async function saveState(state) {
  await chrome.storage.local.set({ exportState: state });
}

async function finish(job, result) {
  if (job.finished) return;
  job.finished = true;
  if (job.port) job.port.disconnect();
  const state = {
    id: job.state.id,
    startedAt: job.state.startedAt,
    finishedAt: Date.now(),
    status: result.ok ? "complete" : "error",
    message: result.ok
      ? `完成：${result.count} 张图片已保存到桌面。`
      : `导出失败：${result.error || "本地导出程序没有返回结果。"}`,
    outputPath: result.outputPath || null
  };
  job.state = state;
  try {
    await saveState(state);
  } finally {
    if (activeJob === job) activeJob = null;
  }
}

async function startExport(job, request) {
  try {
    await saveState(job.state);
    const port = chrome.runtime.connectNative(HOST_NAME);
    job.port = port;
    port.onMessage.addListener((response) => {
      void finish(job, response || { ok: false }).catch(console.error);
    });
    port.onDisconnect.addListener(() => {
      const error = chrome.runtime.lastError?.message;
      if (!job.finished) {
        void finish(job, {
          ok: false,
          error: error || "本地助手连接已中断，请重试。"
        }).catch(console.error);
      }
    });
    port.postMessage({ ...request, action: "export" });
  } catch (error) {
    await finish(job, { ok: false, error: error.message });
  }
}

async function getState() {
  if (activeJob) return activeJob.state;
  const { exportState } = await chrome.storage.local.get("exportState");
  if (activeJob) return activeJob.state;
  if (exportState?.status === "running") {
    const interrupted = {
      ...exportState,
      status: "error",
      message: "上次导出连接已中断，请先检查桌面是否已有图片，再决定是否重试。"
    };
    await saveState(interrupted);
    return interrupted;
  }
  return exportState || null;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "export-state") {
    getState().then((state) => sendResponse({ state })).catch((error) => {
      sendResponse({ error: error.message });
    });
    return true;
  }
  if (message.type !== "start-export") return;
  if (activeJob) {
    sendResponse({ accepted: false, state: activeJob.state });
    return;
  }
  const job = {
    state: {
      id: crypto.randomUUID(),
      startedAt: Date.now(),
      status: "running",
      message: "正在启动本地助手并导出，请稍候…关闭此窗口也会继续。"
    },
    finished: false,
    port: null
  };
  activeJob = job;
  // The background owns the native port; closing the popup cannot cancel it.
  void startExport(job, message.request).catch(console.error);
  sendResponse({ accepted: true, state: job.state });
});
