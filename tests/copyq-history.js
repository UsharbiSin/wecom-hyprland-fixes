"use strict";
const assert = require("node:assert/strict");
const enrich = require("../modules/copyq/wecom-image-history.js");

class Bytes {
    constructor(value) {
        this.value = Buffer.isBuffer(value) ? value : Buffer.from(value);
    }
    size() { return this.value.length; }
    mid(start, count) {
        return new Bytes(this.value.subarray(start, start + count));
    }
}
function png(width = 320, height = 240) {
    const bytes = Buffer.alloc(45);
    Buffer.from("89504e470d0a1a0a0000000d49484452", "hex").copy(bytes);
    bytes.writeUInt32BE(width, 16);
    bytes.writeUInt32BE(height, 20);
    Buffer.from("0000000049454e44ae426082", "hex").copy(bytes, 33);
    return new Bytes(bytes);
}
function fixture() {
    const f = {
        event: true, stored: 0,
        html: new Bytes("<img src='file:///example-a.png'>"),
        currentHtml: null, uri: new Bytes("file:///example-a.png\r\n"),
        formats: ["text/html", "text/plain", "WeWork Message",
                  "image/png", "Ole Private Data"],
        image: png(), writes: [], sleeps: [], pngReads: 0,
        onRead: null, onSleep: null,
    };
    f.currentHtml = f.html;
    f.api = {
        isClipboard: () => f.event,
        historyPngSize: () => f.stored,
        eventHtml: () => f.html,
        clipboard: mime => {
            if (f.onRead) f.onRead(mime);
            if (mime === "?") return new Bytes(f.formats.join("\n") + "\n");
            if (mime === "text/html") return f.currentHtml;
            if (mime === "text/uri-list") return f.uri;
            if (mime === "image/png") { ++f.pngReads; return f.image; }
            throw new Error("Unexpected clipboard read: " + mime);
        },
        base64: b => b.value.toString("base64"),
        text: b => b.value.toString("utf8"),
        sleep: ms => { f.sleeps.push(ms); if (f.onSleep) f.onSleep(); },
        setHistoryPng: b => f.writes.push(b),
    };
    return f;
}
let passed = 0;
function test(name, run) {
    run(); ++passed;
    process.stdout.write("PASS " + name + "\n");
}
function unchanged(f, expected) {
    assert.equal(enrich(f.api), expected);
    assert.equal(f.writes.length, 0);
}
test("eligible WeCom event gains only history PNG", () => {
    const f = fixture();
    const originalFormats = f.formats.slice();
    assert.equal(enrich(f.api), "enriched");
    assert.deepEqual(f.writes, [f.image]);
    assert.deepEqual(f.formats, originalFormats);
    assert.equal(f.sleeps.length, 0);
});
test("primary selection is skipped", () => {
    const f = fixture(); f.event = false;
    unchanged(f, "skip-selection");
});
test("existing history PNG is preserved", () => {
    const f = fixture(); f.stored = 10;
    unchanged(f, "skip-existing-image");
});
test("other apps with text and PNG are not touched", () => {
    const f = fixture();
    f.formats = f.formats.filter(x => x !== "WeWork Message");
    unchanged(f, "skip-other-source"); assert.equal(f.pngReads, 0);
});
test("secret and hidden sources are not read", () => {
    for (const name of ["secret", "hidden"]) {
        const f = fixture(); f.formats.push("application/x-copyq-" + name);
        unchanged(f, "skip-other-source"); assert.equal(f.pngReads, 0);
    }
});
test("old event is not paired with current image", () => {
    const f = fixture();
    f.currentHtml = new Bytes("<img src='file:///different.png'>");
    unchanged(f, "skip-stale-event"); assert.equal(f.pngReads, 0);
});
test("multiple or remote URI is skipped", () => {
    for (const uri of ["file:///a.png\nfile:///b.png",
                       "https://example.org/image.png", ""]) {
        const f = fixture(); f.uri = new Bytes(uri);
        unchanged(f, "skip-uri");
    }
});
test("PNG appended after 300 ms is captured", () => {
    const f = fixture(); f.formats = f.formats.filter(x => x !== "image/png");
    f.onSleep = () => {
        if (f.sleeps.length === 2) f.formats.push("image/png");
    };
    assert.equal(enrich(f.api), "enriched");
    assert.equal(f.sleeps.reduce((a, b) => a + b, 0), 300);
    assert.equal(f.writes.length, 1);
});
test("PNG wait is bounded at 900 ms", () => {
    const f = fixture(); f.formats = f.formats.filter(x => x !== "image/png");
    unchanged(f, "skip-png-unavailable");
    assert.deepEqual(f.sleeps, [150, 150, 150, 150, 150, 150]);
});
test("selection changing during wait is skipped", () => {
    const f = fixture(); f.formats = f.formats.filter(x => x !== "image/png");
    f.onSleep = () => { f.uri = new Bytes("file:///different.png"); };
    unchanged(f, "skip-selection-changed"); assert.equal(f.pngReads, 0);
});
test("selection changing during image fetch is skipped", () => {
    const f = fixture();
    f.onRead = mime => {
        if (mime === "image/png") f.uri = new Bytes("file:///different.png");
    };
    unchanged(f, "skip-selection-changed"); assert.equal(f.pngReads, 1);
});
test("source marker disappearing during read is skipped", () => {
    const f = fixture();
    f.onRead = mime => {
        if (mime === "image/png") f.formats = ["image/png"];
    };
    unchanged(f, "skip-selection-changed");
});
test("truncated or invalid PNG is never saved", () => {
    for (const image of [new Bytes("not a png"), png(0, 1), png(4097, 1),
                         new Bytes(png().value.subarray(0, 44))]) {
        const f = fixture(); f.image = image;
        unchanged(f, "skip-png-unavailable");
    }
});
test("oversized PNG is rejected without copying payload", () => {
    const f = fixture(); f.image = {size: () => 64 * 1024 * 1024 + 1};
    unchanged(f, "skip-png-unavailable");
});
test("read exception leaves normal history intact", () => {
    const f = fixture();
    f.onRead = mime => {
        if (mime === "image/png") throw new Error("selection expired");
    };
    unchanged(f, "skip-read-error");
});
test("empty or oversized event HTML is skipped", () => {
    for (const size of [0, 1024 * 1024 + 1]) {
        const f = fixture(); f.html = {size: () => size};
        unchanged(f, "skip-event-html"); assert.equal(f.pngReads, 0);
    }
});
process.stdout.write(passed + " mocked rule tests passed.\n");
