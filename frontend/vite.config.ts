import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const LICENSE_FILE = "third-party-licenses.txt";

// Bundled packages that ship no LICENSE file. Their package.json declares MIT, whose notice must
// still travel with our copy; the text below is the standard MIT licence with the author from
// package.json and the year of the first npm release (registry `time.created`).
const LICENSE_OVERRIDES: Record<string, string> = {
  "react-remove-scroll-bar": `MIT License

Copyright (c) 2019 Anton Korzunov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`,
};

/** Fills licence sections Vite left empty and fails the build if any bundled package still has none. */
function completeLicenses(): Plugin {
  return {
    name: "moneylyzer-complete-licenses",
    apply: "build",
    // "post": runs after Vite's own build.license step has put the file into the bundle
    generateBundle: {
      order: "post",
      handler(_options, bundle) {
        const asset = bundle[LICENSE_FILE];
        if (!asset || asset.type !== "asset") throw new Error(`${LICENSE_FILE} was not emitted (build.license off?)`);
        const sections = String(asset.source).split(/^(?=## )/m);
        const missing: string[] = [];
        const out = sections.map((section) => {
          const m = /^## (\S+) - \S+/.exec(section); // "(MIT)" is missing when a package declares no licence
          if (!m) return section;
          const body = section.slice(section.indexOf("\n") + 1).trim();
          if (body) return section;
          const text = LICENSE_OVERRIDES[m[1] as string];
          if (!text) {
            missing.push(m[1] as string);
            return section;
          }
          return `${section.trimEnd()}\n\n${text}\n\n`;
        });
        if (missing.length) {
          throw new Error(`No licence text for bundled package(s): ${missing.join(", ")}. Add it to LICENSE_OVERRIDES in vite.config.ts.`);
        }
        asset.source = out.join("");
      },
    },
  };
}

// The backend (FastAPI) serves frontend/dist at "/" and the JSON/SSE API under /api.
// In development, Vite serves the app and proxies /api to the backend on 8080.
export default defineConfig({
  plugins: [react(), completeLicenses()],
  build: {
    outDir: "dist",
    assetsInlineLimit: 0, // keep every asset a real file: the CSP forbids nothing here, but data URIs are avoided on purpose
    sourcemap: false,
    // Licence texts of every bundled dependency, served at /third-party-licenses.txt
    // (Lucide's ISC and Feather's MIT notices must travel with the copies we ship).
    license: { fileName: LICENSE_FILE },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: false,
        ws: false, // Server-Sent Events are plain HTTP, not WebSocket
      },
    },
  },
});
