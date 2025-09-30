(function () {
  const widgetStates = new WeakMap();
  const trackedWidgets = new Set();

  function getState(widget) {
    return widgetStates.get(widget);
  }

  function setButtonState(state, running) {
    if (!state) {
      return;
    }
    if (state.startBtn) {
      state.startBtn.disabled = running;
    }
    if (state.stopBtn) {
      state.stopBtn.disabled = !running;
    }
  }

  function updateResult(widget, message, level = "status") {
    const state = getState(widget);
    if (!state || !state.resultBox) {
      return;
    }
    const resultBox = state.resultBox;
    resultBox.textContent = message || "";
    resultBox.classList.remove("scan-status", "scan-success", "scan-error");
    if (level === "success") {
      resultBox.classList.add("scan-success");
    } else if (level === "error") {
      resultBox.classList.add("scan-error");
    } else if (message) {
      resultBox.classList.add("scan-status");
    }
  }

  function stopScan(widget) {
    const state = getState(widget);
    if (!state) {
      return;
    }

    if (state.controls && typeof state.controls.stop === "function") {
      state.controls.stop();
    }
    state.controls = null;

    if (state.video && state.video.srcObject) {
      try {
        state.video.srcObject.getTracks().forEach((track) => track.stop());
      } catch (err) {
        console.warn("Unable to stop camera tracks", err);
      }
      state.video.srcObject = null;
    }

    if (state.reader && typeof state.reader.reset === "function") {
      state.reader.reset();
    }

    state.running = false;
    setButtonState(state, false);
  }

  function announceAndMaybeStop(widget, message, level) {
    updateResult(widget, message, level);
    if (level === "error") {
      stopScan(widget);
    }
  }

  function findWidgetForInput(input) {
    for (const widget of trackedWidgets) {
      const state = getState(widget);
      if (state && state.targetInput === input) {
        return widget;
      }
    }
    return null;
  }

  async function handleLookup(widget, code) {
    const state = getState(widget);
    if (!state || !state.lookupTemplate) {
      return;
    }

    const placeholder = "__SKU__";
    const encoded = encodeURIComponent(code);
    let url = state.lookupTemplate;
    if (url.includes(placeholder)) {
      url = url.replace(placeholder, encoded);
    } else {
      const joiner = url.includes("?") ? "&" : "?";
      url = `${url}${joiner}sku=${encoded}`;
    }

    try {
      updateResult(widget, "Looking up item…", "status");
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error(`Lookup failed (${response.status})`);
      }
      const data = await response.json();
      if (data && data.name) {
        const details = [data.name];
        if (data.description) {
          details.push(data.description);
        }
        if (data.unit) {
          details.push(`Unit: ${data.unit}`);
        }
        updateResult(widget, details.join(" · "), "success");
      } else {
        updateResult(widget, "Item found.", "success");
      }
    } catch (error) {
      updateResult(widget, error.message || "Lookup failed", "error");
    }
  }

  function handleDetection(widget, codeText) {
    const state = getState(widget);
    if (!state) {
      return;
    }
    const value = (codeText || "").toString().trim();
    if (!value) {
      return;
    }
    if (state.lastResult === value) {
      return;
    }
    state.lastResult = value;

    const input = state.targetInput;
    if (input) {
      input.value = value;
      const event = new Event("input", { bubbles: true });
      input.dispatchEvent(event);
    }

    updateResult(widget, `Scanned: ${value}`, "success");
    handleLookup(widget, value);

    if (state.autoSubmit && input && input.form) {
      stopScan(widget);
      if (typeof input.form.requestSubmit === "function") {
        input.form.requestSubmit();
      } else {
        input.form.submit();
      }
    }
  }

  async function startScan(widget) {
    const state = getState(widget);
    if (!state || state.running) {
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      announceAndMaybeStop(widget, "Camera access is not supported on this device.", "error");
      return;
    }

    state.lastResult = "";
    setButtonState(state, true);
    updateResult(widget, "Starting camera…", "status");

    try {
      state.controls = await state.reader.decodeFromConstraints(
        {
          video: {
            facingMode: { ideal: "environment" },
          },
        },
        state.video,
        (result, err) => {
          if (result && (result.text || typeof result.getText === "function")) {
            const text = result.text || result.getText();
            handleDetection(widget, text);
          } else if (err && !(err instanceof window.ZXing.NotFoundException)) {
            console.warn("Barcode scan error", err);
            updateResult(widget, "Scanning error. Adjust lighting or distance.", "status");
          }
        }
      );
      state.running = true;
      updateResult(widget, "Scanner is active. Align the code within the frame.", "status");
    } catch (error) {
      setButtonState(state, false);
      updateResult(widget, error.message || "Unable to start camera.", "error");
    }
  }

  function initScannerWidgets() {
    const ZX = window.ZXing;
    const widgets = document.querySelectorAll("[data-scanner]");
    if (!widgets.length) {
      return;
    }

    if (!ZX || !ZX.BrowserMultiFormatReader) {
      console.warn("ZXing library was not loaded.");
      return;
    }

    widgets.forEach((widget) => {
      const video = widget.querySelector("video");
      const startBtn = widget.querySelector(".start-scan");
      const stopBtn = widget.querySelector(".stop-scan");
      const targetId = widget.dataset.targetInput || "barcodeOutput";
      const targetInput = document.getElementById(targetId);
      const autoSubmit = widget.dataset.autoSubmit === "true";
      const lookupTemplate = widget.dataset.lookupTemplate || "";
      const resultBox = widget.querySelector("[data-scan-result]");

      if (!video) {
        return;
      }

      const reader = new ZX.BrowserMultiFormatReader();
      const state = {
        reader,
        video,
        startBtn,
        stopBtn,
        targetInput,
        autoSubmit,
        lookupTemplate,
        resultBox,
        controls: null,
        running: false,
        lastResult: "",
      };

      if (!targetInput) {
        if (resultBox) {
          resultBox.textContent = "Scanner input field was not found.";
          resultBox.classList.add("scan-error");
        }
        if (startBtn) {
          startBtn.disabled = true;
        }
        if (stopBtn) {
          stopBtn.disabled = true;
        }
        return;
      }

      widgetStates.set(widget, state);
      trackedWidgets.add(widget);

      if (startBtn) {
        startBtn.addEventListener("click", () => startScan(widget));
      }
      if (stopBtn) {
        stopBtn.addEventListener("click", () => {
          stopScan(widget);
          updateResult(widget, "Scanner stopped.", "status");
        });
      }
    });
  }

  function attachRedirectHandlers() {
    const buttons = document.querySelectorAll("[data-scan-redirect]");
    if (!buttons.length) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const targetInputId = button.dataset.targetInput || "barcodeOutput";
        const input = document.getElementById(targetInputId);
        const url = button.dataset.scanRedirect;
        const param = button.dataset.param || "sku";
        if (!input || !url) {
          return;
        }
        const value = input.value.trim();
        if (!value) {
          const widget = findWidgetForInput(input);
          if (widget) {
            updateResult(widget, "Scan or enter a code before continuing.", "error");
          } else {
            window.alert("Scan or enter a code before continuing.");
          }
          return;
        }
        const separator = url.includes("?") ? "&" : "?";
        const destination = `${url}${separator}${encodeURIComponent(param)}=${encodeURIComponent(value)}`;
        window.location.href = destination;
      });
    });
  }

  function cleanupAll() {
    trackedWidgets.forEach((widget) => stopScan(widget));
  }

  document.addEventListener("DOMContentLoaded", () => {
    attachRedirectHandlers();
    initScannerWidgets();
  });

  window.addEventListener("beforeunload", cleanupAll);
  window.addEventListener("pagehide", cleanupAll);
})();
