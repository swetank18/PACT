/**
 * Fonts are bundled rather than pulled from a CDN. A demo laptop on a
 * conference wifi that cannot reach Google Fonts falls back to a system stack
 * mid-pitch, and the monospace doing security work is most of the credibility.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
