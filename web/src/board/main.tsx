// Bootstrap of the board. The entry is index.html.

import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HttpDataSource } from "../data/source";
import "./styles.css";
import { App } from "./view/app";

const root = document.getElementById("root");
if (root === null) throw new Error("#root is missing in index.html");
createRoot(root).render(
  <StrictMode>
    <App source={new HttpDataSource()} />
  </StrictMode>,
);
