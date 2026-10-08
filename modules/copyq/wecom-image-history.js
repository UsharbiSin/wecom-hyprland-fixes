// Add PNG to the CopyQ history event, never to the clipboard.
// The injected API also allows race/skip tests without a desktop session.
function enrichWecomImageHistory(api) {
    var maxBytes = 64 * 1024 * 1024;
    var htmlLimit = 1024 * 1024;
    var marker = "WeWork Message";
    if (!api.isClipboard()) return "skip-selection";
    if (api.historyPngSize() > 0) return "skip-existing-image";
    var eventHtml = api.eventHtml();
    if (!eventHtml || eventHtml.size() === 0 || eventHtml.size() > htmlLimit)
        return "skip-event-html";
    var htmlKey = api.base64(eventHtml);

    function formats() {
        return api.text(api.clipboard("?")).trim().split("\n");
    }
    function hasMarker(list) {
        return list.indexOf(marker) !== -1 &&
            list.indexOf("application/x-copyq-secret") === -1 &&
            list.indexOf("application/x-copyq-hidden") === -1;
    }
    function htmlMatches() {
        var value = api.clipboard("text/html");
        return value.size() <= htmlLimit && api.base64(value) === htmlKey;
    }
    function uint32(base64) {
        var alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
            "abcdefghijklmnopqrstuvwxyz0123456789+/";
        var acc = 0, bits = 0, bytes = [];
        for (var i = 0; i < base64.length; ++i) {
            var digit = alphabet.indexOf(base64.charAt(i));
            if (digit < 0) break;
            acc = (acc << 6) | digit;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                bytes.push((acc >>> bits) & 255);
            }
        }
        if (bytes.length !== 4) return 0;
        return bytes[0] * 16777216 + bytes[1] * 65536 +
            bytes[2] * 256 + bytes[3];
    }
    function validPng(png) {
        var size = png.size();
        if (size < 45 || size > maxBytes) return false;
        if (api.base64(png.mid(0, 8)) !== "iVBORw0KGgo=" ||
            api.base64(png.mid(8, 8)) !== "AAAADUlIRFI=" ||
            api.base64(png.mid(size - 12, 12)) !== "AAAAAElFTkSuQmCC")
            return false;
        var width = uint32(api.base64(png.mid(16, 4)));
        var height = uint32(api.base64(png.mid(20, 4)));
        return width > 0 && height > 0 && width <= 4096 && height <= 4096;
    }

    try {
        if (!hasMarker(formats())) return "skip-other-source";
        if (!htmlMatches()) return "skip-stale-event";
        var uri = api.clipboard("text/uri-list");
        if (uri.size() === 0 || uri.size() > 65536) return "skip-uri";
        var paths = api.text(uri).trim().split(/\r?\n/).filter(function(line) {
            return line && line.charAt(0) !== "#";
        });
        if (paths.length !== 1 || paths[0].indexOf("file:///") !== 0)
            return "skip-uri";
        var uriKey = api.base64(uri);

        function sameSelection() {
            return hasMarker(formats()) && htmlMatches() &&
                api.base64(api.clipboard("text/uri-list")) === uriKey;
        }
        for (var attempt = 0; attempt <= 6; ++attempt) {
            if (!sameSelection()) return "skip-selection-changed";
            if (formats().indexOf("image/png") !== -1) {
                // Request PNG directly, bypassing text-first history capture.
                // CopyQ decodes/re-encodes it; do not open the URI ourselves.
                var png = api.clipboard("image/png");
                if (!sameSelection()) return "skip-selection-changed";
                if (validPng(png)) {
                    api.setHistoryPng(png);
                    return "enriched";
                }
            }
            if (attempt < 6) api.sleep(150);
        }
        return "skip-png-unavailable";
    } catch (error) {
        // A failed read must leave the normal history event intact.
        return "skip-read-error";
    }
}

if (typeof module === "object" && module.exports) {
    module.exports = enrichWecomImageHistory;
} else {
    enrichWecomImageHistory({
        isClipboard: function() { return isClipboard(); },
        historyPngSize: function() { return data("image/png").size(); },
        eventHtml: function() { return data("text/html"); },
        clipboard: function(mime) { return clipboard(mime); },
        base64: function(bytes) { return toBase64(bytes); },
        text: function(bytes) { return str(bytes); },
        sleep: function(ms) { sleep(ms); },
        setHistoryPng: function(png) { setData("image/png", png); }
    });
}
