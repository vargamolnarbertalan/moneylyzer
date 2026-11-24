import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initMotion } from "./motion";
import { initTheme } from "./theme";
import "./styles.css";

initTheme();
initMotion();

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root");
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
