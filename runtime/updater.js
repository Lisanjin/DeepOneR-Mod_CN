/* DeepOneR Mod 2: official resource updates + version-matched GitHub script overlay.
 * ES5 for Cocos Creator 2.2.2 JSB. Does not implement story redirection itself.
 */
(function (global) {
    "use strict";
    var fu = jsb.fileUtils;
    var config = JSON.parse(fu.getStringFromFile("deepone-mod/runtime-config.json"));
    var writable = fu.getWritablePath();
    var home = writable + "deepone-mod-v2/";
    var officialCache = writable + "deepone-assets/";
    var official = config.officialPackageUrl;
    var pages = config.githubPagesAssets;
    var raw = config.githubRawAssets;
    var manifestAsset = config.manifestAsset;
    var fontRelative = config.fontRelative;
    var state = { release: config.release, version: null, active: null, critical: null, checkedAt: 0 };
    var basePaths = [], fontRoot = "", cleanRoot = "", selectedRoot = "";
    var request = null, deadline = null, finished = false, syncStarted = false, launchStarted = false;
    var failureReported = false, quarantine = {}, bootFailures = 0, previousBoot = null;

    function log(message) { console.log("[DeepOneMod] " + message); }
    function exists(path) { return fu.isFileExist(path); }
    function read(path) { return fu.getStringFromFile(path); }
    function json(path) {
        try { return exists(path) ? JSON.parse(read(path)) : null; } catch (e) { return null; }
    }
    function digest(text) { return global.DeepOneModCrypto.MD5(text).toString(); }
    function byteLength(text) { return global.DeepOneModCrypto.enc.Utf8.parse(text).sigBytes; }
    function version(value) { return typeof value === "string" && /^\d+\.\d+\.\d+(?:\.\d+)?$/.test(value); }
    function entry(value) {
        return value && /^[a-f0-9]{32}$/.test(value.md5) && typeof value.size === "number" &&
            value.size > 1000000 && value.size < 16000000;
    }
    function write(path, text) {
        fu.createDirectory(path.substring(0, path.lastIndexOf("/") + 1));
        var temp = path + ".writing";
        if (!fu.writeStringToFile(text, temp)) { throw new Error("Cannot write " + path); }
        if (!fu.renameFile(temp, path)) { throw new Error("Cannot commit " + path); }
    }
    function saveStatus(message) {
        state.message = message;
        state.checkedAt = Date.now();
        try { write(home + "status.json", JSON.stringify(state, null, 2)); } catch (e) { log(String(e)); }
    }
    function isOurPath(path) {
        return path.indexOf(home) === 0 || path.indexOf("deepone-mod/font/") !== -1 ||
            path.indexOf("deepone-mod/patch-seed/") !== -1;
    }
    function applyPaths() {
        var result = [], all = [selectedRoot, fontRoot, cleanRoot].concat(basePaths);
        for (var i = 0; i < all.length; i++) {
            var path = all[i];
            if (path && result.indexOf(path) === -1) { result.push(path); }
        }
        fu.setSearchPaths(result);
        global.localStorage.setItem("HotUpdateSearchPaths", JSON.stringify(result));
    }
    function packagedRoot(marker, suffix) {
        var full = fu.fullPathForFilename(marker);
        return full && full.slice(-suffix.length) === suffix ? full.slice(0, -suffix.length) : "";
    }
    function validScript(path, info) {
        if (!exists(path) || !entry(info)) { return false; }
        var text = read(path);
        return text.indexOf("window.__require") !== -1 && text.indexOf("ntr.best") === -1 &&
            digest(text) === info.md5 && byteLength(text) === info.size;
    }
    function patchRoot(info) { return home + "patches/" + info.version + "/" + info.md5 + "/"; }
    function patchKey(info) { return info.version + ":" + info.md5; }
    function blockPatch(info, reason) {
        quarantine[patchKey(info)] = { at: Date.now(), reason: reason };
        write(home + "quarantine.json", JSON.stringify(quarantine, null, 2));
    }
    function markBootHealthy() {
        try { write(home + "boot.json", JSON.stringify({ pending: false, at: Date.now() })); } catch (e) { log(String(e)); }
    }
    function armBoot() {
        if (state.active) {
            write(home + "boot.json", JSON.stringify({ pending: true, phase: "loading-project",
                version: state.active.version, md5: state.active.md5, size: state.active.size,
                failures: previousBoot && previousBoot.md5 === state.active.md5 ? bootFailures : 0 }));
        } else { markBootHealthy(); }
    }
    function reportFailure(message) {
        log(message);
        if (failureReported) { return; }
        failureReported = true;
        try {
            jsb.reflection.callStaticMethod("org/cocos2dx/lib/Cocos2dxHelper", "showDialog",
                "(Ljava/lang/String;Ljava/lang/String;)V", "DeepOneR Mod", message);
        } catch (e) { log("Native error dialog unavailable: " + String(e)); }
    }
    function bootstrapFailed(error) {
        log("Script startup failed: " + String(error && error.stack || error));
        if (state.active) {
            try { blockPatch(state.active, "Script startup exception: " + String(error)); } catch (e) { log(String(e)); }
            markBootHealthy();
            saveStatus("Patch startup failed; disabled for the next launch. No automatic restart.");
            reportFailure("本次汉化脚本启动失败，已停用。请完全关闭游戏后重新打开；若仍异常，请反馈启动日志。已有资源已保留。");
        } else {
            saveStatus("Official script startup failed; no automatic restart.");
            reportFailure("游戏脚本未能启动。已有资源已保留，请向维护者反馈启动日志。");
        }
    }
    function selectPatch(info, root) {
        if (!info || info.version !== state.version || quarantine[patchKey(info)] || !validScript(root + "src/project.js", info)) { return false; }
        selectedRoot = root;
        state.active = { version: info.version, md5: info.md5, size: info.size, source: info.source || "cache" };
        return true;
    }
    function migrateManifest(path) {
        var value = json(path);
        if (!value || !version(value.version)) { return; }
        var changed = value.packageUrl !== official || value.remoteManifestUrl !== config.officialManifestUrl ||
            value.remoteVersionUrl !== config.officialVersionUrl;
        if (!changed) { return; }
        var original = read(path);
        var backup = home + "migration/" + digest(original) + ".manifest";
        if (!exists(backup)) { write(backup, original); }
        value.packageUrl = official;
        value.remoteManifestUrl = config.officialManifestUrl;
        value.remoteVersionUrl = config.officialVersionUrl;
        // Preserve the complete resource index, version, progress, and downloaded files.
        write(path, JSON.stringify(value));
        log("Migrated update endpoints: " + path);
    }
    function restoreLegacyScript() {
        var sourcePath = fu.fullPathForFilename("src/project.js");
        if (!sourcePath || !exists(sourcePath)) { throw new Error("Game script not found"); }
        var source = read(sourcePath);
        if (source.indexOf("ntr.best") === -1) { return; }
        var rules = json("deepone-mod/legacy-repair.json");
        if (!rules || !rules.remove) { throw new Error("Legacy migration data missing"); }
        var restored = source.replace(/\r\n/g, "\n");
        for (var i = 0; i < rules.remove.length; i++) { restored = restored.split(rules.remove[i]).join(""); }
        if (state.version === "1.288.0") { restored = restored.replace(rules.oldMaster, rules.originalMaster); }
        if (restored.indexOf("ntr.best") !== -1 || restored === source) {
            throw new Error("Unrecognized legacy script; existing resources were retained");
        }
        cleanRoot = home + "official-restored/" + state.version + "/" + digest(restored) + "/";
        if (!exists(cleanRoot + "src/project.js")) { write(cleanRoot + "src/project.js", restored); }
        log("Prepared official-compatible legacy script; original cache retained");
    }
    function prepare() {
        try {
            fu.createDirectory(home);
            var stored = null;
            try { stored = JSON.parse(global.localStorage.getItem("HotUpdateSearchPaths") || "null"); } catch (ignore) {}
            var paths = stored && Array.isArray(stored) ? stored : fu.getSearchPaths();
            for (var i = 0; i < paths.length; i++) {
                if (typeof paths[i] === "string" && !isOurPath(paths[i]) && basePaths.indexOf(paths[i]) === -1) {
                    basePaths.push(paths[i]);
                }
            }
            fu.setSearchPaths(basePaths);
            var cached = json(officialCache + "project.manifest");
            var bundled = json(manifestAsset);
            if (!bundled || !version(bundled.version)) { throw new Error("Bundled resource manifest invalid"); }
            var hasCache = cached && version(cached.version) && cached.assets && exists(officialCache + "src/project.js");
            state.version = hasCache ? cached.version : bundled.version;
            quarantine = json(home + "quarantine.json") || {};
            var schema = json(home + "recovery-schema.json");
            if (!schema || schema.version !== 2) {
                if (Object.keys(quarantine).length) {
                    write(home + "migration/quarantine-before-preboot.json", JSON.stringify(quarantine, null, 2));
                }
                quarantine = {};
                write(home + "quarantine.json", "{}");
                write(home + "recovery-schema.json", JSON.stringify({ version: 2, at: Date.now() }));
            }
            previousBoot = json(home + "boot.json");
            if (previousBoot && previousBoot.pending && previousBoot.phase === "loading-project" &&
                previousBoot.version === state.version && entry(previousBoot)) {
                bootFailures = (previousBoot.failures || 0) + 1;
                if (bootFailures >= 2) { blockPatch(previousBoot, "Two interrupted project loads; automatic recovery"); }
            }
            if (hasCache && basePaths.indexOf(officialCache) === -1) { basePaths.unshift(officialCache); }
            fu.setSearchPaths(basePaths);
            var candidates = [officialCache + "project.manifest", officialCache + "version.manifest",
                writable + "deepone-assets_temp/project.manifest", writable + "deepone-assets_temp/version.manifest",
                writable + "deepone-assets_temp/project.manifest.temp"];
            for (i = 0; i < candidates.length; i++) { if (exists(candidates[i])) { migrateManifest(candidates[i]); } }
            restoreLegacyScript();
            fontRoot = packagedRoot("deepone-mod/font/" + fontRelative, fontRelative);
            if (!fontRoot) { throw new Error("Chinese font overlay missing"); }
            var active = json(home + "active.json");
            if (active && version(active.version) && entry(active)) { selectPatch(active, patchRoot(active)); }
            var previous = json(home + "previous.json");
            if (!state.active && previous && version(previous.version) && entry(previous)) { selectPatch(previous, patchRoot(previous)); }
            var seed = json("deepone-mod/patch-seed/manifest.json");
            if (!state.active && seed && version(seed.version)) {
                var seedRoot = packagedRoot("deepone-mod/patch-seed/src/project.js", "src/project.js");
                if (seedRoot) { selectPatch(seed, seedRoot); }
            }
            applyPaths();
            saveStatus(state.active ? "Matching GitHub patch selected" : "Official script selected; checking GitHub at startup");
        } catch (e) {
            state.critical = String(e);
            log(state.critical);
            saveStatus("Migration requires attention; resources preserved");
        }
    }
    function show(title, detail) {
        // Preflight runs before cc.game.run. It must not create a Canvas, camera, Scene, or change resolution.
        log(title + " — " + detail);
    }
    function get(url, callback) {
        var xhr = new XMLHttpRequest(), called = false;
        request = xhr;
        function end(error, text) {
            if (called || finished) { return; }
            called = true;
            if (request === xhr) { request = null; }
            callback(error, text);
        }
        xhr.open("GET", url, true);
        xhr.timeout = 12000;
        xhr.onreadystatechange = function () {
            if (xhr.readyState === 4) {
                end(xhr.status >= 200 && xhr.status < 300 ? null : new Error("HTTP " + xhr.status), xhr.responseText);
            }
        };
        xhr.onerror = function () { end(new Error("Network error")); };
        xhr.ontimeout = function () { end(new Error("Request timed out")); };
        try { xhr.send(); } catch (e) { end(e); }
    }
    function fetchEither(relative, token, callback, validate) {
        var query = "?deepone_mod=" + encodeURIComponent(token);
        get(pages + relative + query, function (error, text) {
            if (!error && (!validate || validate(text))) { callback(null, text); return; }
            get(raw + relative + query, callback);
        });
    }
    function installDiagnostics() {
        if (!config.diagnostics) { return; }
        cc.game.once(cc.game.EVENT_GAME_INITED, function () {
            function summary(data) {
                var counts = {}, keys = Object.keys(data || {});
                for (var i = 0; i < keys.length; i++) {
                    if (Array.isArray(data[keys[i]])) { counts[keys[i]] = data[keys[i]].length; }
                }
                return { keys: keys, arrays: counts };
            }
            function singleton() { return global.__require("Singleton"); }
            function snapshot(phase) {
                try {
                    var shared = singleton(), dao = shared.dao || {}, room = [], owned = dao.userFurniture || [];
                    if (dao.userRoom && dao.userRoom.getRoomFurnitureList) { room = dao.userRoom.getRoomFurnitureList() || []; }
                    var missing = [], loose = 0, types = {};
                    for (var i = 0; i < room.length; i++) {
                        var code = room[i].getFurnitureCd(), found = false, looseFound = false;
                        types[typeof code] = (types[typeof code] || 0) + 1;
                        for (var j = 0; j < owned.length; j++) {
                            var other = owned[j].getFurnitureCd();
                            if (other === code) { found = true; }
                            if (String(other) === String(code)) { looseFound = true; }
                        }
                        if (!found) {
                            missing.push({ code: code, codeType: typeof code });
                            if (looseFound) { loose++; }
                        }
                    }
                    var info = { phase: phase, gameVersion: state.version, active: state.active,
                        scriptPath: fu.fullPathForFilename("src/project.js"), dao: summary(dao),
                        roomFurniture: room.length, ownedFurniture: owned.length, missing: missing.slice(0, 12),
                        missingCount: missing.length, looseMatches: loose, roomCodeTypes: types };
                    write(home + "diagnostics.json", JSON.stringify(info, null, 2));
                    log("DIAGNOSTICS " + JSON.stringify(info));
                } catch (e) { log("DIAGNOSTICS error " + String(e)); }
            }
            try {
                var roomClass = global.__require("RoomFurnitureModel");
                if (roomClass && roomClass.prototype && roomClass.prototype.getUserFurnitureModel) {
                    var originalGet = roomClass.prototype.getUserFurnitureModel;
                    roomClass.prototype.getUserFurnitureModel = function () {
                        var value = originalGet.apply(this, arguments);
                        if (!value && !this.__deeponeDiagnosed) {
                            this.__deeponeDiagnosed = true;
                            snapshot("missing-furniture");
                        }
                        return value;
                    };
                }
                var serviceClass = global.__require("ServiceManager");
                if (serviceClass && serviceClass.prototype && serviceClass.prototype._saveUserData) {
                    var originalSave = serviceClass.prototype._saveUserData;
                    serviceClass.prototype._saveUserData = function (types, data) {
                        log("DIAGNOSTICS user-data " + JSON.stringify(summary(data)));
                        return originalSave.apply(this, arguments);
                    };
                }
            } catch (e) { log("DIAGNOSTICS hooks " + String(e)); }
            snapshot("scripts-loaded");
            setTimeout(function () { snapshot("after-15s"); }, 15000);
            setTimeout(function () { snapshot("after-45s"); }, 45000);
        });
    }
    function installCompatibility() {
        cc.game.once(cc.game.EVENT_GAME_INITED, function () {
            try {
                require("deepone-mod/compatibility.js");
                global.DeepOneModCompatibility.install(state.version);
            } catch (e) { log("Furniture compatibility unavailable: " + String(e)); }
        });
    }
    function synchronize(next) {
        if (syncStarted) { log("Ignored duplicate preflight entry"); return; }
        syncStarted = true;
        if (state.critical) {
            reportFailure("汉化启动器需要处理。已有资源已保留，请向维护者反馈启动日志。");
            return;
        }
        show("正在检查汉化补丁", "游戏资源版本 " + state.version);
        finished = false;
        function done(message, notice) {
            if (finished) { return; }
            finished = true;
            if (deadline) { clearTimeout(deadline); deadline = null; }
            if (request) { try { request.abort(); } catch (ignore) {} request = null; }
            saveStatus(message);
            log(message);
            function launch() {
                if (launchStarted) { return; }
                launchStarted = true;
                try { armBoot(); installCompatibility(); installDiagnostics(); next(); } catch (e) { bootstrapFailed(e); }
            }
            if (notice) {
                show(notice, "已有游戏资源已保留");
            }
            launch();
        }
        deadline = setTimeout(function () { done("Patch check timed out; retained matching cache or official script"); }, 40000);
        fetchEither("project.manifest", Math.floor(Date.now() / 60000), function (error, text) {
            if (error) { done("GitHub unavailable; retained matching cache or official script"); return; }
            var manifest, script;
            try {
                manifest = JSON.parse(text);
                script = manifest.assets && manifest.assets["src/project.js"];
                if (!version(manifest.version) || !entry(script)) { throw new Error("Invalid patch manifest"); }
            } catch (e) { done("Invalid GitHub metadata; existing script retained"); return; }
            state.githubVersion = manifest.version;
            if (manifest.version !== state.version) {
                done("GitHub patch version differs from game resources; no mismatched script was applied",
                    "当前资源版本暂不匹配，先进入游戏同步更新");
                return;
            }
            if (quarantine[manifest.version + ":" + script.md5]) {
                done("This patch was quarantined after startup failure; waiting for a changed GitHub script",
                    "已停用启动异常的补丁，等待汉化更新");
                return;
            }
            if (state.active && state.active.md5 === script.md5 && state.active.size === script.size) {
                done("Matching GitHub patch is up to date");
                return;
            }
            show("正在更新汉化补丁", "仅下载脚本，保留已有游戏资源");
            fetchEither("src/project.js", script.md5, function (downloadError, content) {
                if (downloadError) { done("Patch download failed; existing script retained"); return; }
                try {
                    if (!content || byteLength(content) !== script.size || digest(content) !== script.md5 ||
                        content.indexOf("window.__require") === -1 || content.indexOf("ntr.best") !== -1) {
                        throw new Error("Patch content verification failed");
                    }
                    var info = { version: manifest.version, md5: script.md5, size: script.size, source: "github" };
                    var root = patchRoot(info);
                    write(root + "src/project.js", content);
                    if (!validScript(root + "src/project.js", info)) { throw new Error("Stored patch verification failed"); }
                    var oldActive = json(home + "active.json");
                    if (oldActive && version(oldActive.version) && entry(oldActive) && oldActive.md5 !== info.md5) {
                        write(home + "previous.json", JSON.stringify(oldActive, null, 2));
                    }
                    write(home + "active.json", JSON.stringify(info, null, 2));
                    if (finished) { return; }
                    state.active = info;
                    selectedRoot = root;
                    applyPaths();
                    // The game bundle has not been evaluated yet, so no VM restart is needed.
                    done("GitHub patch committed; loading it in the original game boot without restarting");
                } catch (e) { log(String(e)); done("Patch verification or storage failed; existing script retained"); }
            }, function (content) {
                try {
                    return !!content && byteLength(content) === script.size && digest(content) === script.md5 &&
                        content.indexOf("window.__require") !== -1 && content.indexOf("ntr.best") === -1;
                } catch (e) { return false; }
            });
        }, function (text) {
            try {
                var data = JSON.parse(text);
                return version(data.version) && entry(data.assets && data.assets["src/project.js"]);
            } catch (e) { return false; }
        });
    }
    global.DeepOneMod = { prepare: prepare, synchronize: synchronize, markBootHealthy: markBootHealthy,
        bootstrapFailed: bootstrapFailed };
})(window);
