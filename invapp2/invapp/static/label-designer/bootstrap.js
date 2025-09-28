import React, { createElement, createRoot } from "./lib/mini-react.js";
import LabelDesignerApp from "./components/LabelDesignerApp.js";

const h = createElement;

function start() {
  const container = document.getElementById("label-designer-root");
  if (!container) {
    console.error("Label designer root element not found.");
    return;
  }
  const config = window.__LABEL_DESIGNER_CONFIG__ || {};
  const root = createRoot(container);
  root.render(h(LabelDesignerApp, config));
}

document.addEventListener("DOMContentLoaded", start);
