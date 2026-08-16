"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));

describe("personal desktop identity", () => {
  it("cannot replace or update the official desktop app", () => {
    assert.equal(packageJson.productName, "Omnigent Personal");
    assert.equal(packageJson.build.appId, "ai.omnigent.desktop.personal");
    assert.deepEqual(packageJson.build.protocols[0].schemes, ["omnigent-personal"]);
    assert.equal(JSON.stringify(packageJson.build).includes('"publish"'), false);
    assert.equal(JSON.stringify(packageJson.build).includes("/_desktop/updates/"), false);
  });
});
