"use strict";

const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const tls = require("node:tls");
const { createHash, randomBytes } = require("node:crypto");
const httpProxy = require("http-proxy");

const API_PREFIXES = ["/v1", "/api", "/auth", "/health", "/.well-known"];
const UPSTREAM_COOKIE_PREFIX = "omnigent_personal_upstream_";

/**
 * Return a stable cookie namespace for one configured Omnigent server.
 *
 * The private proxy binds to a new loopback port and uses a fresh access token
 * on every launch. Upstream login cookies must not follow that random token:
 * doing so leaves an otherwise-valid persistent cloud session stranded after
 * every normal app restart. Hashing the canonical server identity keeps the
 * cookie name stable across launches while still isolating two cloud servers
 * (including distinct path-mounted deployments on the same origin).
 */
function upstreamCookieNamespaceFor(serverUrl) {
  const target = serverUrl instanceof URL ? serverUrl : new URL(serverUrl);
  const basePath = target.pathname.replace(/\/+$/, "");
  const identity = `${target.origin}${basePath || "/"}`;
  const serverId = createHash("sha256").update(identity).digest("hex").slice(0, 16);
  return `${UPSTREAM_COOKIE_PREFIX}${serverId}_`;
}

function mergeCertificateAuthorities(...groups) {
  return [...new Set(groups.flat().filter((certificate) => typeof certificate === "string"))];
}

function systemAwareHttpsAgent() {
  let defaultCertificates = tls.rootCertificates;
  let systemCertificates = [];
  if (typeof tls.getCACertificates === "function") {
    try {
      defaultCertificates = tls.getCACertificates("default");
      systemCertificates = tls.getCACertificates("system");
    } catch {
      // Older Node/Electron releases fall back to the bundled root set.
    }
  }
  return new https.Agent({
    ca: mergeCertificateAuthorities(defaultCertificates, systemCertificates),
  });
}

function isApiPath(pathname) {
  return API_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function upstreamPath(basePath, requestUrl) {
  const suffix = requestUrl.startsWith("/") ? requestUrl : `/${requestUrl}`;
  return `${basePath}${suffix}` || "/";
}

function sanitizeSetCookies(cookies, upstreamCookieNamespace = "") {
  if (!Array.isArray(cookies)) return cookies;
  return cookies.map((cookie) =>
    cookie
      .replace(/^([^=;\s]+)=/, (_match, name) => `${upstreamCookieNamespace}${name}=`)
      .replace(/;\s*Domain=[^;]+/gi, "")
      .replace(/;\s*Secure/gi, "")
      .replace(/;\s*SameSite=None/gi, "; SameSite=Lax"),
  );
}

function stripProxyCookies(cookieHeader, upstreamCookieNamespace = "") {
  const kept = String(cookieHeader ?? "")
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part && !part.startsWith("omnigent_personal_proxy_"))
    .flatMap((part) => {
      const separator = part.indexOf("=");
      const name = separator === -1 ? part : part.slice(0, separator);
      if (!name.startsWith(UPSTREAM_COOKIE_PREFIX)) return [part];
      // Cookies ignore ports, so the browser sends cookies from every open
      // Personal proxy. Forward only this proxy's upstream cookies and restore
      // their original names (including secure-only __Host- names) for cloud.
      if (!upstreamCookieNamespace || !name.startsWith(upstreamCookieNamespace)) return [];
      return [`${name.slice(upstreamCookieNamespace.length)}${part.slice(separator)}`];
    });
  return kept.join("; ");
}

function contentType(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return (
    {
      ".css": "text/css; charset=utf-8",
      ".html": "text/html; charset=utf-8",
      ".ico": "image/x-icon",
      ".js": "text/javascript; charset=utf-8",
      ".json": "application/json; charset=utf-8",
      ".map": "application/json; charset=utf-8",
      ".png": "image/png",
      ".svg": "image/svg+xml",
      ".wasm": "application/wasm",
      ".woff": "font/woff",
      ".woff2": "font/woff2",
    }[extension] ?? "application/octet-stream"
  );
}

function staticFileFor(staticDir, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const relative = decoded.replace(/^\/+/, "");
  const candidate = path.resolve(staticDir, relative || "index.html");
  if (candidate !== staticDir && !candidate.startsWith(`${staticDir}${path.sep}`)) return null;
  try {
    if (fs.statSync(candidate).isFile()) return candidate;
  } catch {
    // React Router paths fall through to the local SPA entry below.
  }
  const index = path.join(staticDir, "index.html");
  return fs.existsSync(index) ? index : null;
}

async function startPersonalProxy({ staticDir, serverUrl }) {
  const target = new URL(serverUrl);
  const basePath = target.pathname.replace(/\/$/, "");
  const proxyOptions = {
    target: target.origin,
    changeOrigin: true,
    secure: true,
    ws: true,
  };
  // Electron's Node runtime does not use the macOS Keychain roots by default.
  // Keep TLS verification enabled while adding locally trusted enterprise CAs
  // (the same trust policy curl, browsers, and the official shell use).
  if (target.protocol === "https:") proxyOptions.agent = systemAwareHttpsAgent();
  const proxy = httpProxy.createProxyServer(proxyOptions);
  let localOrigin = null;
  const accessToken = randomBytes(24).toString("hex");
  // The access capability is deliberately launch-specific; the upstream
  // session namespace is deliberately server-specific and stable so a valid
  // cloud login survives app restarts without crossing server boundaries.
  const accessCookieName = `omnigent_personal_proxy_${accessToken.slice(0, 12)}`;
  const upstreamCookieNamespace = upstreamCookieNamespaceFor(target);
  const accessCookie = `${accessCookieName}=${accessToken}`;
  const hasAccess = (request) =>
    String(request.headers.cookie ?? "")
      .split(";")
      .map((part) => part.trim())
      .includes(accessCookie);

  proxy.on("proxyReq", (proxyRequest, request) => {
    proxyRequest.path = upstreamPath(basePath, request.url ?? "/");
    proxyRequest.setHeader("origin", target.origin);
    const cookies = stripProxyCookies(request.headers.cookie, upstreamCookieNamespace);
    if (cookies) proxyRequest.setHeader("cookie", cookies);
    else proxyRequest.removeHeader("cookie");
  });
  proxy.on("proxyReqWs", (proxyRequest, request) => {
    proxyRequest.path = upstreamPath(basePath, request.url ?? "/");
    proxyRequest.setHeader("origin", target.origin);
    const cookies = stripProxyCookies(request.headers.cookie, upstreamCookieNamespace);
    if (cookies) proxyRequest.setHeader("cookie", cookies);
    else proxyRequest.removeHeader("cookie");
  });
  proxy.on("proxyRes", (proxyResponse) => {
    if (proxyResponse.headers["set-cookie"]) {
      proxyResponse.headers["set-cookie"] = sanitizeSetCookies(
        proxyResponse.headers["set-cookie"],
        upstreamCookieNamespace,
      );
    }
    const location = proxyResponse.headers.location;
    if (localOrigin && typeof location === "string" && location.startsWith(target.origin)) {
      const upstreamBase = `${target.origin}${basePath}`;
      proxyResponse.headers.location = location.startsWith(upstreamBase)
        ? localOrigin + location.slice(upstreamBase.length)
        : localOrigin + location.slice(target.origin.length);
    }
  });
  proxy.on("error", (error, _request, response) => {
    console.error(
      "[omnigent-personal] proxy request failed:",
      error?.code ?? "unknown",
      error?.message ?? "unknown error",
    );
    if (!response || response.destroyed) return;
    if (typeof response.writeHead === "function") {
      response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
      response.end("Unable to reach the configured Omnigent server.");
    } else {
      response.destroy();
    }
  });

  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url ?? "/", "http://localhost").pathname;
    if (isApiPath(pathname)) {
      if (!hasAccess(request)) {
        response.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
        response.end("Forbidden");
        return;
      }
      proxy.web(request, response);
      return;
    }
    const filePath = staticFileFor(path.resolve(staticDir), pathname);
    if (!filePath) {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Omnigent Personal web bundle is missing. Rebuild the desktop app.");
      return;
    }
    response.writeHead(200, {
      "content-type": contentType(filePath),
      "set-cookie": `${accessCookie}; HttpOnly; SameSite=Strict; Path=/`,
      "cache-control": filePath.endsWith("index.html")
        ? "no-cache"
        : "public, max-age=31536000, immutable",
    });
    fs.createReadStream(filePath).pipe(response);
  });
  server.on("upgrade", (request, socket, head) => {
    if (!hasAccess(request)) {
      socket.destroy();
      return;
    }
    proxy.ws(request, socket, head);
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("failed to bind personal proxy");
  localOrigin = `http://127.0.0.1:${address.port}`;
  return {
    origin: localOrigin,
    close: () =>
      new Promise((resolve) => {
        proxy.close();
        server.close(() => resolve());
      }),
  };
}

module.exports = {
  isApiPath,
  mergeCertificateAuthorities,
  sanitizeSetCookies,
  stripProxyCookies,
  startPersonalProxy,
  staticFileFor,
  upstreamCookieNamespaceFor,
  upstreamPath,
};
