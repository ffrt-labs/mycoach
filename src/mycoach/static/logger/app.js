/* MyCoach Logger — offline-first gym logger.
 *
 * Sessions are logged fully offline and queued in IndexedDB. When MyCoach is
 * reachable on the LAN, unsynced sessions are pushed to the universal import
 * endpoint. A session is editable until it syncs, then read-only.
 */
(function () {
    "use strict";

    // ── Config ──────────────────────────────────────────────────────
    var API_IMPORT = "/api/sources/import/workouts";
    var API_EXERCISES = "/api/logger/exercises";
    var API_ROUTINES = "/api/logger/routines";
    var KEY_APIKEY = "mycoach_logger_api_key";
    var SET_TYPES = ["normal", "warmup", "dropset", "failure"];

    // ── Tiny DOM helper ─────────────────────────────────────────────
    function el(tag, props, children) {
        var node = document.createElement(tag);
        if (props) {
            Object.keys(props).forEach(function (k) {
                if (k === "class") node.className = props[k];
                else if (k === "html") node.innerHTML = props[k];
                else if (k === "text") node.textContent = props[k];
                else if (k.indexOf("on") === 0 && typeof props[k] === "function")
                    node.addEventListener(k.slice(2), props[k]);
                else if (props[k] === true) node.setAttribute(k, "");
                else if (props[k] !== false && props[k] != null) node.setAttribute(k, props[k]);
            });
        }
        (children || []).forEach(function (c) {
            if (c == null) return;
            node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
        });
        return node;
    }
    function $(id) { return document.getElementById(id); }
    function uuid() {
        if (crypto.randomUUID) return crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
            var r = (Math.random() * 16) | 0;
            return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        });
    }

    // ── IndexedDB ───────────────────────────────────────────────────
    var DB;
    function idb() {
        if (DB) return Promise.resolve(DB);
        return new Promise(function (resolve, reject) {
            var req = indexedDB.open("mycoach-logger", 1);
            req.onupgradeneeded = function () {
                var db = req.result;
                if (!db.objectStoreNames.contains("sessions"))
                    db.createObjectStore("sessions", { keyPath: "id" });
                if (!db.objectStoreNames.contains("meta"))
                    db.createObjectStore("meta", { keyPath: "key" });
            };
            req.onsuccess = function () { DB = req.result; resolve(DB); };
            req.onerror = function () { reject(req.error); };
        });
    }
    function tx(store, mode) {
        return idb().then(function (db) { return db.transaction(store, mode).objectStore(store); });
    }
    function reqP(r) {
        return new Promise(function (res, rej) {
            r.onsuccess = function () { res(r.result); };
            r.onerror = function () { rej(r.error); };
        });
    }
    function getAllSessions() { return tx("sessions", "readonly").then(function (s) { return reqP(s.getAll()); }); }
    function getSession(id) { return tx("sessions", "readonly").then(function (s) { return reqP(s.get(id)); }); }
    function putSession(v) { return tx("sessions", "readwrite").then(function (s) { return reqP(s.put(v)); }); }
    function delSession(id) { return tx("sessions", "readwrite").then(function (s) { return reqP(s.delete(id)); }); }
    function getMeta(k) { return tx("meta", "readonly").then(function (s) { return reqP(s.get(k)); }).then(function (r) { return r ? r.value : null; }); }
    function setMeta(k, v) { return tx("meta", "readwrite").then(function (s) { return reqP(s.put({ key: k, value: v })); }); }

    // ── State ───────────────────────────────────────────────────────
    var state = { activeId: null, exerciseCache: [], routine: null };

    // ── Sync-status chip ────────────────────────────────────────────
    function setChip(kind, text) {
        var chip = $("sync-chip");
        chip.className = "chip" + (kind ? " chip--" + kind : "");
        $("sync-chip-text").textContent = text;
    }
    function refreshChip() {
        return getAllSessions().then(function (all) {
            var pending = all.filter(function (s) { return !s.synced; }).length;
            if (!navigator.onLine) { setChip("offline", pending ? pending + " to sync · offline" : "Offline"); return; }
            if (pending) setChip("pending", pending + " to sync");
            else setChip("synced", "All synced");
        });
    }

    // ── Toast ───────────────────────────────────────────────────────
    var toastTimer;
    function toast(msg, kind) {
        var existing = document.querySelector(".toast");
        if (existing) existing.remove();
        var t = el("div", { class: "toast" + (kind ? " toast--" + kind : ""), text: msg });
        document.body.appendChild(t);
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { t.remove(); }, 3200);
    }

    // ── Helpers ─────────────────────────────────────────────────────
    function apiKey() { return localStorage.getItem(KEY_APIKEY) || ""; }
    function fmtTime(iso) {
        var d = new Date(iso);
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " +
            d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    }
    function totalSets(s) { return s.exercises.reduce(function (n, e) { return n + e.sets.length; }, 0); }
    /* "8-10" -> 8; a bare number or anything unparseable falls through to null. */
    function repRangeLowerBound(repRange) {
        if (!repRange) return null;
        var m = /^\s*(\d+)/.exec(repRange);
        return m ? parseInt(m[1], 10) : null;
    }

    /* Flatten a stored session to the canonical WorkoutImport payload. */
    function toPayload(s) {
        var sets = [];
        s.exercises.forEach(function (ex) {
            ex.sets.forEach(function (set, i) {
                sets.push({
                    exercise_title: ex.title,
                    exercise_notes: ex.notes || null,
                    set_index: i + 1,
                    set_type: set.set_type || "normal",
                    superset_id: ex.superset_group != null ? ex.superset_group : null,
                    weight_kg: set.weight_kg,
                    reps: set.reps,
                    rpe: set.rpe,
                });
            });
        });
        return {
            external_id: s.id,
            title: s.title,
            sport: "gym",
            start_time: s.start_time,
            end_time: s.end_time || null,
            notes: s.notes || null,
            sets: sets,
        };
    }

    // ── Sync ────────────────────────────────────────────────────────
    var syncing = false;
    function syncNow(manual) {
        if (syncing) return Promise.resolve();
        if (!apiKey()) {
            if (manual) toast("Set your API key in Settings first", "err");
            return Promise.resolve();
        }
        if (!navigator.onLine) { if (manual) toast("Offline — will sync when reachable", "err"); return refreshChip(); }
        syncing = true;
        return getAllSessions().then(function (all) {
            var pending = all.filter(function (s) { return !s.synced && s.end_time; });
            if (!pending.length) { syncing = false; if (manual) toast("Nothing to sync"); return refreshChip(); }
            setChip("pending", "Syncing…");
            var body = { source: "logger", workouts: pending.map(toPayload) };
            return fetch(API_IMPORT, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-API-Key": apiKey() },
                body: JSON.stringify(body),
            }).then(function (resp) {
                if (resp.status === 401) { toast("API key rejected — check Settings", "err"); throw new Error("401"); }
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                return resp.json();
            }).then(function () {
                return Promise.all(pending.map(function (s) { s.synced = true; return putSession(s); }));
            }).then(function () {
                if (manual) toast(pending.length + " session" + (pending.length > 1 ? "s" : "") + " synced", "ok");
                pullExercises();
            }).catch(function (e) {
                if (e.message !== "401" && manual) toast("Sync failed — MyCoach not reachable", "err");
            });
        }).then(function () {
            syncing = false;
            return refreshChip();
        }).then(function () {
            if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render();
        });
    }

    function pullExercises() {
        if (!apiKey() || !navigator.onLine) return;
        fetch(API_EXERCISES, { headers: { "X-API-Key": apiKey() } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.exercises) { state.exerciseCache = d.exercises; setMeta("exercises", d.exercises); } })
            .catch(function () {});
    }

    function pullRoutine() {
        if (!apiKey() || !navigator.onLine) return;
        fetch(API_ROUTINES, { headers: { "X-API-Key": apiKey() } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { state.routine = d; setMeta("routine", d); if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render(); })
            .catch(function () {});
    }

    // ── Rendering: Home ─────────────────────────────────────────────
    function render() {
        state.activeId = null;
        setActionbar(null);
        var view = $("view");
        view.innerHTML = "";
        view.appendChild(el("h1", { class: "screen-title", text: "Train" }));
        view.appendChild(el("p", { class: "sub", text: "Log your session. It saves offline and syncs when you're home." }));

        view.appendChild(
            el("button", { class: "btn btn--primary btn--block", style: "margin-top:22px", onclick: startSession }, ["＋ Start session"])
        );
        if (state.routine && state.routine.days && state.routine.days.length) {
            view.appendChild(
                el("button", { class: "btn btn--ghost btn--block", style: "margin-top:10px", onclick: openRoutinePicker }, ["Start from routine"])
            );
        }
        view.appendChild(
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:10px", onclick: openSettings }, ["Settings"])
        );

        getAllSessions().then(function (all) {
            all.sort(function (a, b) { return (b.start_time || "").localeCompare(a.start_time || ""); });
            view.appendChild(el("div", { class: "eyebrow", text: "Sessions" }));
            if (!all.length) {
                view.appendChild(el("div", { class: "empty", text: "No sessions yet. Start one above." }));
                return;
            }
            all.forEach(function (s) {
                var meta = fmtTime(s.start_time) + " · " + s.exercises.length + " ex · " + totalSets(s) + " sets";
                view.appendChild(
                    el("button", { class: "session-row", onclick: function () { openSession(s.id); } }, [
                        el("span", {}, [
                            el("div", { class: "session-row__title", text: s.title || "Session" }),
                            el("div", { class: "session-row__meta", text: meta }),
                        ]),
                        el("span", { class: "tag " + (s.synced ? "tag--synced" : "tag--pending"), text: s.synced ? "Synced" : "Pending" }),
                    ])
                );
            });
        });
        refreshChip();
    }

    // ── Session lifecycle ───────────────────────────────────────────
    function startSession() {
        var now = new Date();
        var s = {
            id: uuid(),
            title: defaultTitle(now),
            start_time: now.toISOString(),
            end_time: null,
            notes: null,
            exercises: [],
            synced: false,
            created_at: now.toISOString(),
        };
        putSession(s).then(function () { openSession(s.id); refreshChip(); });
    }
    function defaultTitle(d) {
        var h = d.getHours();
        var part = h < 12 ? "Morning" : h < 17 ? "Afternoon" : "Evening";
        return part + " Session";
    }

    function openRoutinePicker() {
        var days = (state.routine && state.routine.days) || [];
        var sorted = days.slice().sort(function (a, b) { return a.order_index - b.order_index; });
        openSheet("Start from routine", sorted.map(function (day) {
            var meta = day.exercises.length + (day.exercises.length === 1 ? " exercise" : " exercises");
            return el("button", { class: "session-row", onclick: function () { closeSheet(); startFromRoutineDay(day); } }, [
                el("span", {}, [
                    el("div", { class: "session-row__title", text: day.name }),
                    el("div", { class: "session-row__meta", text: meta }),
                ]),
            ]);
        }));
    }

    function startFromRoutineDay(day) {
        var now = new Date();
        var s = {
            id: uuid(),
            title: day.name,
            start_time: now.toISOString(),
            end_time: null,
            notes: null,
            exercises: day.exercises.slice().sort(function (a, b) { return a.order_index - b.order_index; }).map(function (e) {
                return {
                    title: e.exercise_name,
                    notes: e.notes || null,
                    sets: [],
                    target_sets: e.sets,
                    rep_range: e.rep_range,
                    superset_group: e.superset_group,
                };
            }),
            synced: false,
            created_at: now.toISOString(),
        };
        putSession(s).then(function () { openSession(s.id); refreshChip(); });
    }

    function openSession(id) {
        getSession(id).then(function (s) {
            if (!s) { render(); return; }
            state.activeId = id;
            renderSession(s);
        });
    }

    function renderSession(s) {
        var ro = !!s.synced; // read-only after sync
        var view = $("view");
        view.innerHTML = "";

        view.appendChild(
            el("button", { class: "iconbtn", style: "margin:18px 0 4px;margin-left:-8px", onclick: function () { render(); } }, ["‹ Back"])
        );

        if (ro) {
            view.appendChild(el("h1", { class: "screen-title", text: s.title }));
            view.appendChild(el("p", { class: "sub", text: fmtTime(s.start_time) + " · Synced (read-only)" }));
        } else {
            var titleInput = el("input", {
                class: "input", value: s.title, "aria-label": "Session title",
                onchange: function (e) { s.title = e.target.value.trim() || "Session"; putSession(s); },
            });
            view.appendChild(el("div", { class: "field", style: "margin-top:8px" }, [titleInput]));
            view.appendChild(el("p", { class: "sub faint", style: "font-size:13px", text: fmtTime(s.start_time) }));
        }

        // Exercise cards
        if (!s.exercises.length) {
            view.appendChild(el("div", { class: "empty", text: ro ? "No exercises logged." : "Add your first exercise below." }));
        }
        s.exercises.forEach(function (ex, exIdx) {
            view.appendChild(exerciseCard(s, ex, exIdx, ro));
        });

        // Action bar
        if (ro) {
            setActionbar([
                el("button", { class: "btn btn--danger", onclick: function () { confirmDelete(s); } }, ["Delete"]),
                el("button", { class: "btn btn--ghost", style: "flex:2", onclick: function () { render(); } }, ["Done"]),
            ]);
        } else {
            setActionbar([
                el("button", { class: "btn btn--ghost", onclick: function () { openAddExercise(s); } }, ["＋ Exercise"]),
                el("button", { class: "btn btn--primary", style: "flex:2", onclick: function () { finishSession(s); } }, ["Finish"]),
            ]);
        }
    }

    function exerciseCard(s, ex, exIdx, ro) {
        var meta = ex.sets.length + (ex.sets.length === 1 ? " set" : " sets");
        if (ex.target_sets) meta += "  ·  Target " + ex.target_sets + " × " + ex.rep_range;
        var head = el("div", { class: "card__head" }, [
            el("div", {}, [
                el("p", { class: "exercise-title", text: ex.title }),
                el("div", { class: "exercise-meta", text: meta }),
            ]),
            ro ? null : el("button", { class: "iconbtn", onclick: function () { ex.remove = true; s.exercises.splice(exIdx, 1); putSession(s).then(function () { renderSession(s); }); } }, ["Remove"]),
        ]);
        var card = el("div", { class: "card" }, [head]);

        ex.sets.forEach(function (set, i) {
            var w = set.weight_kg != null ? set.weight_kg : "—";
            var reps = set.reps != null ? set.reps : "—";
            var row = el("div", { class: "setrow" }, [
                el("span", { class: "setrow__idx", text: String(i + 1) }),
                el("span", { class: "setrow__val", html: w + '<small>&nbsp;kg</small>' }),
                el("span", { class: "setrow__val", html: reps + '<small>&nbsp;reps</small>' }),
                el("span", { class: "setrow__type setrow__type--" + (set.set_type || "normal"), text: set.rpe != null ? "RPE " + set.rpe : (set.set_type !== "normal" ? set.set_type : "") }),
                ro ? el("span", {}) : el("button", { class: "iconbtn", "aria-label": "Delete set", onclick: function () { ex.sets.splice(i, 1); putSession(s).then(function () { renderSession(s); }); } }, ["✕"]),
            ]);
            card.appendChild(row);
        });

        if (!ro) {
            card.appendChild(
                el("button", { class: "btn btn--sm btn--ghost", style: "margin-top:12px", onclick: function () { openAddSet(s, ex); } }, ["＋ Add set"])
            );
        }
        return card;
    }

    function finishSession(s) {
        s.end_time = new Date().toISOString();
        putSession(s).then(function () {
            render();
            toast("Session saved");
            syncNow(false);
        });
    }

    function confirmDelete(s) {
        openSheet("Delete this session?", [
            el("p", { class: "sub", style: "margin-bottom:16px", text: "This removes it from the logger. Already-synced data stays in MyCoach." }),
            el("button", { class: "btn btn--danger btn--block", onclick: function () { delSession(s.id).then(function () { closeSheet(); render(); toast("Session deleted"); }); } }, ["Delete session"]),
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:8px", onclick: closeSheet }, ["Cancel"]),
        ]);
    }

    // ── Sheets: add exercise / add set / settings ───────────────────
    function openSheet(title, children) {
        closeSheet();
        var sheet = el("div", { class: "sheet" }, [el("h2", { class: "sheet__title", text: title })].concat(children));
        var backdrop = el("div", { class: "sheet-backdrop", onclick: function (e) { if (e.target === backdrop) closeSheet(); } }, [sheet]);
        document.body.appendChild(backdrop);
    }
    function closeSheet() {
        var b = document.querySelector(".sheet-backdrop");
        if (b) b.remove();
    }

    function openAddExercise(s) {
        var listId = "ex-list";
        var datalist = el("datalist", { id: listId },
            (state.exerciseCache || []).map(function (t) { return el("option", { value: t }); }));
        var input = el("input", { class: "input", list: listId, placeholder: "e.g. Bench Press", autocomplete: "off", autocapitalize: "words" });
        function add() {
            var title = input.value.trim();
            if (!title) return;
            s.exercises.push({ title: title, notes: null, sets: [] });
            putSession(s).then(function () { closeSheet(); renderSession(s); openAddSet(s, s.exercises[s.exercises.length - 1]); });
        }
        openSheet("Add exercise", [
            el("div", { class: "field" }, [datalist, input]),
            el("button", { class: "btn btn--primary btn--block", onclick: add }, ["Add exercise"]),
        ]);
        setTimeout(function () { input.focus(); }, 50);
    }

    function openAddSet(s, ex) {
        var prev = ex.sets.length ? ex.sets[ex.sets.length - 1] : null;
        var repsDefault = prev && prev.reps != null ? prev.reps : (!prev ? repRangeLowerBound(ex.rep_range) : null);
        var weight = el("input", { class: "input mono", type: "number", inputmode: "decimal", step: "0.5", min: "0", placeholder: "kg", value: prev && prev.weight_kg != null ? prev.weight_kg : "" });
        var reps = el("input", { class: "input mono", type: "number", inputmode: "numeric", min: "0", placeholder: "reps", value: repsDefault != null ? repsDefault : "" });
        var rpe = el("input", { class: "input mono", type: "number", inputmode: "decimal", step: "0.5", min: "1", max: "10", placeholder: "RPE (optional)" });
        var chosenType = "normal";
        var segButtons = SET_TYPES.map(function (t) {
            return el("button", { type: "button", "aria-pressed": t === "normal" ? "true" : "false", onclick: function () {
                chosenType = t;
                seg.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
                this.setAttribute("aria-pressed", "true");
            }, text: t });
        });
        var seg = el("div", { class: "seg" }, segButtons);

        function add(keepOpen) {
            var wv = weight.value !== "" ? parseFloat(weight.value) : null;
            var rv = reps.value !== "" ? parseInt(reps.value, 10) : null;
            var pv = rpe.value !== "" ? parseFloat(rpe.value) : null;
            if (rv == null && wv == null) { toast("Enter a weight or reps", "err"); return; }
            ex.sets.push({ weight_kg: wv, reps: rv, rpe: pv, set_type: chosenType });
            putSession(s).then(function () {
                if (keepOpen) { renderSession(s); openAddSet(s, ex); }
                else { closeSheet(); renderSession(s); }
            });
        }
        openSheet("Add set · " + ex.title, [
            el("div", { class: "row" }, [
                el("div", { class: "field", style: "margin:0" }, [el("label", { text: "Weight" }), weight]),
                el("div", { class: "field", style: "margin:0" }, [el("label", { text: "Reps" }), reps]),
            ]),
            el("div", { class: "field", style: "margin-top:14px" }, [el("label", { text: "RPE" }), rpe]),
            el("div", { class: "field" }, [el("label", { text: "Set type" }), seg]),
            el("div", { class: "row" }, [
                el("button", { class: "btn btn--ghost", onclick: function () { add(true); } }, ["Save + add"]),
                el("button", { class: "btn btn--primary", onclick: function () { add(false); } }, ["Save set"]),
            ]),
        ]);
        setTimeout(function () { weight.focus(); }, 50);
    }

    function openSettings() {
        var key = el("input", { class: "input", type: "password", placeholder: "Paste MYCOACH_API_TOKEN", value: apiKey(), autocomplete: "off" });
        openSheet("Settings", [
            el("div", { class: "field" }, [
                el("label", { text: "API key" }),
                key,
                el("p", { class: "sub faint", style: "font-size:12px;margin-top:8px", text: "Must match MYCOACH_API_TOKEN on your MyCoach server. Stored on this device only." }),
            ]),
            el("button", { class: "btn btn--primary btn--block", onclick: function () {
                localStorage.setItem(KEY_APIKEY, key.value.trim());
                closeSheet();
                toast("Saved");
                pullExercises();
                pullRoutine();
                syncNow(true);
            } }, ["Save"]),
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:8px", onclick: function () { closeSheet(); syncNow(true); } }, ["Sync now"]),
        ]);
        setTimeout(function () { key.focus(); }, 50);
    }

    // ── Action bar ──────────────────────────────────────────────────
    function setActionbar(children) {
        var bar = $("actionbar");
        var inner = $("actionbar-inner");
        inner.innerHTML = "";
        if (!children) { bar.hidden = true; return; }
        children.forEach(function (c) { inner.appendChild(c); });
        bar.hidden = false;
    }

    // ── Boot ────────────────────────────────────────────────────────
    $("sync-chip").addEventListener("click", function () { syncNow(true); });
    window.addEventListener("online", function () { refreshChip(); syncNow(false); pullRoutine(); });
    window.addEventListener("offline", refreshChip);

    getMeta("exercises").then(function (list) { if (list) state.exerciseCache = list; });
    getMeta("routine").then(function (r) {
        state.routine = r;
        if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render();
    });

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/logger/sw.js", { scope: "/logger" }).catch(function (e) {
            console.warn("[logger] SW registration failed:", e);
        });
    }

    render();
    pullExercises();
    pullRoutine();
    syncNow(false);
})();
