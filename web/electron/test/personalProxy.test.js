"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const {
  isApiPath,
  mergeCertificateAuthorities,
  sanitizeSetCookies,
  stripProxyCookies,
  upstreamPath,
} = require("../src/personalProxy");

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
