"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {
  isApiPath,
  mergeCertificateAuthorities,
  normalizePersonalProxyPort,
  personalProxyPortFromSettings,
  rememberPersonalProxyPort,
  sanitizeSetCookies,
  startPersonalProxy,
  stripProxyCookies,
  upstreamCookieNamespaceFor,
  upstreamPath,
} = require("../src/personalProxy");

function listen(server, port = 0) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
}

function close(server) {
  return new Promise((resolve) => {
    server.close(resolve);
  });
}

function staticFixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "omnigent-personal-proxy-"));
  fs.writeFileSync(path.join(directory, "index.html"), "<!doctype html><title>Personal</title>");
  return directory;
}

describe("personal frontend proxy", () => {
  it("combines bundled and system certificate authorities without weakening TLS", () => {
    assert.deepEqual(mergeCertificateAuthorities(["bundled-a", "shared"], ["system-a", "shared"]), [
      "bundled-a",
      "shared",
      "system-a",
    ]);
  });

  it("proxies only backend namespaces and leaves SPA routes local", () => {
    assert.equal(isApiPath("/v1/sessions"), true);
    assert.equal(isApiPath("/auth/login"), true);
    assert.equal(isApiPath("/c/conv_123"), false);
    assert.equal(isApiPath("/assets/app.js"), false);
  });

  it("preserves the configured cloud mount when forwarding", () => {
    assert.equal(
      upstreamPath("/api/2.0/omnigent", "/v1/sessions?limit=3"),
      "/api/2.0/omnigent/v1/sessions?limit=3",
    );
  });

  it("keeps the upstream login namespace stable across app restarts", () => {
    assert.equal(
      upstreamCookieNamespaceFor("https://omni.example.com/api/2.0/omnigent/"),
      upstreamCookieNamespaceFor("https://omni.example.com/api/2.0/omnigent"),
    );
  });

  it("stores one validated proxy port per configured server", () => {
    const settings = {};
    const serverUrl = "https://omni.example.com/team-a";
    assert.equal(normalizePersonalProxyPort("not-a-port"), 0);
    assert.equal(personalProxyPortFromSettings(settings, serverUrl), 0);
    assert.equal(rememberPersonalProxyPort(settings, serverUrl, 43123), true);
    assert.equal(personalProxyPortFromSettings(settings, serverUrl), 43123);
    assert.equal(rememberPersonalProxyPort(settings, serverUrl, 43123), false);
    assert.equal(rememberPersonalProxyPort(settings, serverUrl, 80), false);
  });

  it("reuses a preferred loopback port so browser storage survives restarts", async () => {
    const reservation = http.createServer();
    await listen(reservation);
    const reservedAddress = reservation.address();
    assert.ok(reservedAddress && typeof reservedAddress !== "string");
    const preferredPort = reservedAddress.port;
    await close(reservation);

    const staticDir = staticFixture();
    const personalProxy = await startPersonalProxy({
      staticDir,
      serverUrl: "https://omni.example.com/",
      preferredPort,
    });
    try {
      assert.equal(personalProxy.port, preferredPort);
      assert.equal(new URL(personalProxy.origin).port, String(preferredPort));
    } finally {
      await personalProxy.close();
      fs.rmSync(staticDir, { recursive: true, force: true });
    }
  });

  it("falls back safely when the saved loopback port is occupied", async () => {
    const occupant = http.createServer();
    await listen(occupant);
    const occupiedAddress = occupant.address();
    assert.ok(occupiedAddress && typeof occupiedAddress !== "string");

    const staticDir = staticFixture();
    const personalProxy = await startPersonalProxy({
      staticDir,
      serverUrl: "https://omni.example.com/",
      preferredPort: occupiedAddress.port,
    });
    try {
      assert.notEqual(personalProxy.port, occupiedAddress.port);
    } finally {
      await personalProxy.close();
      await close(occupant);
      fs.rmSync(staticDir, { recursive: true, force: true });
    }
  });

  it("isolates saved logins for different servers and path-mounted deployments", () => {
    const primary = upstreamCookieNamespaceFor("https://omni.example.com/");
    assert.notEqual(primary, upstreamCookieNamespaceFor("https://other.example.com/"));
    assert.notEqual(primary, upstreamCookieNamespaceFor("https://omni.example.com/team-a/"));
  });

  it("rewrites upstream cookies for the private loopback origin", () => {
    assert.deepEqual(
      sanitizeSetCookies(
        ["session=abc; Path=/; Domain=omni.example.com; Secure; HttpOnly; SameSite=None"],
        "omnigent_personal_upstream_server_a_",
      ),
      ["omnigent_personal_upstream_server_a_session=abc; Path=/; HttpOnly; SameSite=Lax"],
    );
  });

  it("restores cloud cookie names without leaking other proxy capabilities or sessions", () => {
    assert.equal(
      stripProxyCookies(
        "omnigent_personal_proxy_deadbeef=secret; " +
          "omnigent_personal_upstream_server_a___Host-ap_session=abc; " +
          "omnigent_personal_upstream_server_b___Host-ap_session=other; theme=dark",
        "omnigent_personal_upstream_server_a_",
      ),
      "__Host-ap_session=abc; theme=dark",
    );
  });
});
