/* MyCoach Logger — offline-first gym logger.
 *
 * Sessions are logged fully offline and queued in IndexedDB. When MyCoach is
 * reachable over Tailscale, unsynced sessions are pushed to the universal import
 * endpoint. A session is editable until it syncs, then read-only.
 */
(function () {
    "use strict";

    // ── Config ──────────────────────────────────────────────────────
    // window.MYCOACH_CONFIG only exists on the standalone deploy (its own
    // origin, injected into index.html at container start). Absent there —
    // as under the legacy same-origin /logger mount — API paths stay
    // relative and the service worker keeps its /logger scope.
    var STANDALONE = typeof window !== "undefined" && !!window.MYCOACH_CONFIG;
    var API_ORIGIN = (STANDALONE && window.MYCOACH_CONFIG.apiOrigin) || "";
    var API_IMPORT = API_ORIGIN + "/api/sources/import/workouts";
    var API_EXERCISES = API_ORIGIN + "/api/logger/exercises";
    var API_WEEK = API_ORIGIN + "/api/logger/week";
    var KEY_APIKEY = "mycoach_logger_api_key";
    var SET_TYPES = ["normal", "warmup", "dropset", "failure"];
    var API_TIMEOUT_MS = 30000;

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

    // ── Deferred writes ─────────────────────────────────────────────
    /* Inline set entry has no Save button: the row *is* the record, so every
       keystroke is a potential write. Typing debounces — a typed set survives
       the app being killed 250ms after the last keystroke — while anything
       structural (a set added, deleted, retyped) writes at once. The queue
       holds the live session object, never a copy, so a late flush can never
       write a stale value. */
    var PERSIST_DEBOUNCE_MS = 250;
    var pendingSession = null;
    var persistTimer = null;

    function schedulePersist(s) {
        pendingSession = s;
        if (persistTimer) return;
        persistTimer = setTimeout(function () { persistTimer = null; flushPersist(); }, PERSIST_DEBOUNCE_MS);
    }
    function flushPersist() {
        if (persistTimer) { clearTimeout(persistTimer); persistTimer = null; }
        var s = pendingSession;
        pendingSession = null;
        return s ? putSession(s) : Promise.resolve();
    }
    function persistNow(s) { pendingSession = s; return flushPersist(); }
    /* Drop queued writes for a session about to be deleted, so a stray flush
       cannot resurrect it after delSession. */
    function dropPersist() {
        if (persistTimer) { clearTimeout(persistTimer); persistTimer = null; }
        pendingSession = null;
    }

    // ── State ───────────────────────────────────────────────────────
    var state = { activeId: null, exerciseCache: [], lastPerformedCache: [], week: null };

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
    /* Shared fetch for all /api/* calls. Tailscale makes MyCoach reachable
       away from home, but a request can still hang in a dead zone. Abort it
       after 30 seconds; callers keep the session queued and retry later. */
    function apiFetch(url, options) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, API_TIMEOUT_MS);
        var opts = Object.assign({}, options, { signal: controller.signal });
        return fetch(url, opts).then(
            function (resp) { clearTimeout(timer); return resp; },
            function (err) { clearTimeout(timer); throw err; }
        );
    }
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

    function resolveExerciseChoice(raw, catalogue) {
        var title = raw.trim();
        var match = (catalogue || []).find(function (exercise) {
            var name = typeof exercise === "string" ? exercise : exercise.name;
            return name && name.toLocaleLowerCase() === title.toLocaleLowerCase();
        });
        if (!match || typeof match === "string") {
            return { exercise_id: null, title: title };
        }
        return { exercise_id: match.id, title: match.name };
    }

    /* The grey reference row under a card (#54): the last time this exercise
       was actually trained, from the `last_performed` sibling list #70 added
       to /api/logger/exercises. Matched the same way the server built it — by
       exercise_id, falling back to title only for a custom (null-id)
       exercise, which is the only handle it has. */
    function lastPerformedFor(cache, ex) {
        var match = (cache || []).find(function (entry) {
            if (ex.exercise_id != null) return entry.exercise_id === ex.exercise_id;
            return entry.exercise_id == null && entry.title === ex.title;
        });
        return match || null;
    }

    /* One exercise of a prescribed session, as the offline session stores it.

       The coach's numbers ride along unchanged. `target_weight_kg` is the
       point of the whole exercise: it is what #54 prefills the first set's
       input with, and storing it on the session means the prescription
       survives a week with no signal. */
    function sessionExerciseFromPrescribed(exercise) {
        return {
            exercise_id: exercise.exercise_id || null,
            title: exercise.name,
            notes: exercise.notes || null,
            sets: [],
            target_sets: exercise.sets,
            rep_range: exercise.rep_range,
            target_weight_kg: exercise.target_weight_kg != null ? exercise.target_weight_kg : null,
            target_rpe: exercise.target_rpe != null ? exercise.target_rpe : null,
            rest_seconds: exercise.rest_seconds != null ? exercise.rest_seconds : null,
            superset_group: exercise.superset_group != null ? exercise.superset_group : null,
        };
    }

    /* The week's *loggable* sessions. Swim and run ride the same payload (#48)
       so the wire never has to change to render them, but this screen does not
       render them yet — see the map's Out of scope. Dropping them here keeps
       that decision in one place. */
    function loggableSessions(week) {
        return ((week && week.sessions) || []).filter(function (ps) { return !!ps.loggable; });
    }

    /* The line under a prescribed session's title. Whether the coach has put
       weights on it is the thing worth knowing before you tap: a session the
       server rebuilt from the routine (no plan this week) carries none. */
    function prescribedMeta(ps) {
        var exercises = ps.exercises || [];
        var weighted = exercises.filter(function (ex) { return ex.target_weight_kg != null; }).length;
        return exercises.length + (exercises.length === 1 ? " exercise" : " exercises") +
            " · " + (weighted ? weighted + " prescribed" : "no weights");
    }

    /* The heaviest working (non-warmup) set in an exercise, ties going to the
       first set reached at that weight. A bodyweight exercise with no logged
       weight still has a top set — the first working set — since every entry
       there is tied at "no weight". Exercises with no working sets have none. */
    function topSetForExercise(ex) {
        var working = ex.sets.filter(function (set) { return (set.set_type || "normal") !== "warmup"; });
        if (!working.length) return null;
        var top = working[0];
        var topWeight = top.weight_kg != null ? top.weight_kg : -Infinity;
        for (var i = 1; i < working.length; i++) {
            var weight = working[i].weight_kg != null ? working[i].weight_kg : -Infinity;
            if (weight > topWeight) { top = working[i]; topWeight = weight; }
        }
        return top;
    }

    /* rir "3+" has no exact RPE — writing null is honest, a naive 7 would be
       false precision GymWorkoutDetail.rpe doesn't need (it already has an
       established null-handling path for Hevy history / manual entry). */
    var RIR_TO_RPE = { "0": 10, "1": 9, "2": 8, "3+": null };

    /* Which of the week's prescriptions are still owed.

       The server's `done` flag is a fallback, not the answer: it is whatever
       MyCoach knew at the last successful pull, and a session lifted in a
       basement is consumed long before the server hears about it. So the
       local sessions are unioned in — any session carrying a prescription's
       id consumes it.

       Consumption is *derived* from the stored sessions rather than kept as a
       separate list, and that is what makes cancelling work: cancelling
       deletes the session outright (it is never synced, because a cancelled
       session is not data), so the prescription correctly reappears as owed.
       A separate list would need a second code path to rewind, and would
       quietly rot when it didn't.

       Sessions rebuilt from the routine have `id: null` — there is no
       prescription to consume, so they always stay. Non-gym sessions stay too:
       what to *render* is the caller's decision. */
    function outstandingSessions(weekSessions, localSessions) {
        var consumed = {};
        (localSessions || []).forEach(function (s) {
            if (s.planned_session_id != null) consumed[s.planned_session_id] = true;
        });
        return (weekSessions || []).filter(function (ps) {
            if (ps.id == null) return true;
            return !ps.done && !consumed[ps.id];
        });
    }

    /* Flatten a stored session to the canonical WorkoutImport payload. */
    function toPayload(s) {
        var sets = [];
        s.exercises.forEach(function (ex) {
            ex.sets.forEach(function (set, i) {
                sets.push({
                    exercise_id: ex.exercise_id || null,
                    exercise_title: ex.title,
                    exercise_notes: ex.notes || null,
                    set_index: i + 1,
                    set_type: set.set_type || "normal",
                    superset_id: ex.superset_group != null ? ex.superset_group : null,
                    weight_kg: set.weight_kg,
                    reps: set.reps,
                    rpe: set.rpe,
                    prescribed_weight_kg: set.prescribed_weight_kg != null ? set.prescribed_weight_kg : null,
                    prescribed_reps: set.prescribed_reps != null ? set.prescribed_reps : null,
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
            planned_session_id: s.planned_session_id != null ? s.planned_session_id : null,
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
            if (!pending.length) { if (manual) toast("Nothing to sync"); return; }
            setChip("pending", "Syncing…");
            var body = { source: "logger", workouts: pending.map(toPayload) };
            return apiFetch(API_IMPORT, {
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
        }).finally(function () {
            syncing = false;
            return refreshChip();
        }).then(function () {
            if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render();
        }).catch(function () {
            // Safety net for the whole chain (e.g. getAllSessions() rejecting)
            // so a failure here never surfaces as an unhandled rejection. The
            // guard itself is already cleared by the `finally` above.
        });
    }

    function pullExercises() {
        if (!apiKey() || !navigator.onLine) return;
        apiFetch(API_EXERCISES, { headers: { "X-API-Key": apiKey() } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                if (d.exercises) { state.exerciseCache = d.exercises; setMeta("exercises", d.exercises); }
                if (d.last_performed) { state.lastPerformedCache = d.last_performed; setMeta("lastPerformed", d.last_performed); }
            })
            .catch(function () {});
    }

    /* The training week, pulled on open and cached.

       Nothing on the gym floor waits on this: home renders from the cached
       copy the moment the app opens, and a successful pull re-renders
       underneath it. A failed or non-ok response leaves the cache alone —
       last week's plan beats an empty screen in a basement. */
    function pullWeek() {
        if (!apiKey() || !navigator.onLine) return;
        apiFetch(API_WEEK, { headers: { "X-API-Key": apiKey() } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                state.week = d;
                setMeta("week", d);
                if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render();
            })
            .catch(function () {});
    }

    // ── Screen wake lock ────────────────────────────────────────────
    /* The screen dimming mid-set is the loudest way this feels worse than a
       paper notebook. The lock is held for exactly as long as an editable
       session is on screen — acquired when one is opened, released the moment
       we return home.

       iOS drops the lock whenever the tab backgrounds and never hands it
       back on its own, so intent (`wakeWanted`) is tracked separately from
       the sentinel and re-asserted on `visibilitychange`. Every failure path
       is silent: no wake lock on an unsupported phone is a worse session,
       not a broken one, and a toast mid-set is noise. */
    var wakeSentinel = null;
    var wakeWanted = false;
    var wakeRequesting = false;

    function acquireWakeLock() {
        wakeWanted = true;
        if (!navigator.wakeLock || wakeSentinel || wakeRequesting) return;
        // A request while hidden is rejected by the browser; the
        // visibilitychange handler re-asserts it on the way back.
        if (document.visibilityState !== "visible") return;
        wakeRequesting = true;
        navigator.wakeLock.request("screen").then(function (sentinel) {
            wakeRequesting = false;
            // Released while the request was still in flight.
            if (!wakeWanted) { sentinel.release().catch(function () {}); return; }
            wakeSentinel = sentinel;
            sentinel.addEventListener("release", function () {
                if (wakeSentinel === sentinel) wakeSentinel = null;
            });
        }).catch(function () {
            wakeRequesting = false;
        });
    }

    function releaseWakeLock() {
        wakeWanted = false;
        if (!wakeSentinel) return;
        var sentinel = wakeSentinel;
        wakeSentinel = null;
        sentinel.release().catch(function () {});
    }

    // ── Rendering: Home ─────────────────────────────────────────────
    function render() {
        state.activeId = null;
        releaseWakeLock();
        setActionbar(null);
        var view = $("view");
        view.innerHTML = "";
        view.appendChild(el("h1", { class: "screen-title", text: "Train" }));
        view.appendChild(el("p", { class: "sub", text: "Log your session. It saves offline and syncs when you're home." }));

        view.appendChild(
            el("button", { class: "btn btn--primary btn--block", style: "margin-top:22px", onclick: startSession }, ["＋ Start session"])
        );
        view.appendChild(
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:10px", onclick: openSettings }, ["Settings"])
        );

        /* Both lists need the stored sessions — the week to know what has
           already been answered, the history to list itself — so their
           containers go in now, in order, and fill from the one read. */
        var weekBox = el("div", {});
        var historyBox = el("div", {});
        view.appendChild(weekBox);
        view.appendChild(historyBox);

        getAllSessions().then(function (all) {
            renderWeek(weekBox, all);
            renderHistory(historyBox, all);
        });
        refreshChip();
    }

    /* This week's gym sessions, in place of the old routine picker (#71).

       Selection is the athlete's, not the calendar's: the week is a list of
       work owed, shown in no day order, and you start whichever one you are
       actually doing. Answered sessions stay on screen but inert — watching
       the week fill up is half the point — and "answered" is the server's
       stale `done` flag unioned with the local record (outstandingSessions),
       so a session lifted in a basement leaves the list immediately and a
       cancelled one comes back. */
    function renderWeek(box, localSessions) {
        var gym = loggableSessions(state.week);
        if (!gym.length) return;
        var owed = outstandingSessions(gym, localSessions);
        box.appendChild(el("div", { class: "eyebrow", text: "This week" }));
        gym.forEach(function (ps) {
            var label = el("span", {}, [
                el("div", { class: "session-row__title", text: ps.title }),
                el("div", { class: "session-row__meta", text: prescribedMeta(ps) }),
            ]);
            if (owed.indexOf(ps) === -1) {
                box.appendChild(el("div", { class: "session-row session-row--done" }, [
                    label,
                    el("span", { class: "tag tag--synced", text: "Done" }),
                ]));
                return;
            }
            box.appendChild(
                el("button", { class: "session-row", onclick: function () { startFromPrescribed(ps); } }, [label])
            );
        });
    }

    function renderHistory(box, all) {
        all = all.slice().sort(function (a, b) { return (b.start_time || "").localeCompare(a.start_time || ""); });
        box.appendChild(el("div", { class: "eyebrow", text: "Sessions" }));
        if (!all.length) {
            box.appendChild(el("div", { class: "empty", text: "No sessions yet. Start one above." }));
            return;
        }
        all.forEach(function (s) {
            var meta = fmtTime(s.start_time) + " · " + s.exercises.length + " ex · " + totalSets(s) + " sets";
            box.appendChild(
                el("button", { class: "session-row", onclick: function () { openSession(s.id); } }, [
                    el("span", {}, [
                        el("div", { class: "session-row__title", text: s.title || "Session" }),
                        el("div", { class: "session-row__meta", text: meta }),
                    ]),
                    el("span", { class: "tag " + (s.synced ? "tag--synced" : "tag--pending"), text: s.synced ? "Synced" : "Pending" }),
                ])
            );
        });
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
            planned_session_id: null,  // ad-hoc: answers no prescription
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

    /* Start one of the week's sessions. No picker sheet: the week is already
       on the home screen, so the row *is* the button. */
    function startFromPrescribed(ps) {
        var now = new Date();
        var s = {
            id: uuid(),
            title: ps.title,
            start_time: now.toISOString(),
            end_time: null,
            notes: null,
            // The prescription this session answers (#69). Null when the
            // server rebuilt the session from the routine because no plan
            // exists this week — there is nothing to claim.
            planned_session_id: ps.id != null ? ps.id : null,
            exercises: (ps.exercises || []).map(sessionExerciseFromPrescribed),
            synced: false,
            created_at: now.toISOString(),
        };
        putSession(s).then(function () { openSession(s.id); refreshChip(); });
    }

    function openSession(id) {
        getSession(id).then(function (s) {
            if (!s) { render(); return; }
            state.activeId = id;
            if (s.synced) releaseWakeLock(); // read-only: nothing to keep awake for
            else acquireWakeLock();
            renderSession(s);
        });
    }

    function renderSession(s) {
        var ro = !!s.synced; // read-only after sync
        cancelPendingReorder(); // about to detach every card; don't outlive them
        var view = $("view");
        view.innerHTML = "";

        view.appendChild(
            el("button", { class: "iconbtn", style: "margin:18px 0 4px;margin-left:-8px", onclick: function () { flushPersist().then(function () { render(); }); } }, ["‹ Back"])
        );

        if (ro) {
            view.appendChild(el("h1", { class: "screen-title", text: s.title }));
            view.appendChild(el("p", { class: "sub", text: fmtTime(s.start_time) + " · Synced (read-only)" }));
        } else {
            var titleInput = el("input", {
                class: "input", value: s.title, "aria-label": "Session title",
                onchange: function (e) { s.title = e.target.value.trim() || "Session"; persistNow(s); },
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
            if (!ro && exIdx < s.exercises.length - 1) view.appendChild(linkToggle(s, exIdx));
        });

        // Action bar
        if (ro) {
            setActionbar([
                el("button", { class: "btn btn--danger", onclick: function () { confirmDelete(s); } }, ["Delete"]),
                el("button", { class: "btn btn--ghost", style: "flex:2", onclick: function () { render(); } }, ["Done"]),
            ]);
        } else {
            setActionbar([
                el("button", { class: "iconbtn", "aria-label": "Cancel session", onclick: function () { confirmCancel(s); } }, ["✕"]),
                el("button", { class: "btn btn--ghost", onclick: function () { openAddExercise(s); } }, ["＋ Exercise"]),
                el("button", { class: "btn btn--primary", style: "flex:2", onclick: function () { promptFinish(s); } }, ["Finish"]),
            ]);
        }
    }

    function exerciseMeta(ex) {
        var meta = ex.sets.length + (ex.sets.length === 1 ? " set" : " sets");
        if (ex.target_sets) meta += "  ·  Target " + ex.target_sets + " × " + ex.rep_range;
        return meta;
    }

    function cardFor(exIdx) { return $("view").querySelector('.card[data-ex="' + exIdx + '"]'); }

    /* The grey line under the coach's prescription (#54): context for
       overriding the prefilled default, not the default itself — the input
       is already on the target weight, this just shows what it took last
       time. Nothing renders when the exercise has never been trained. */
    function lastPerformedRow(ex) {
        var entry = lastPerformedFor(state.lastPerformedCache, ex);
        if (!entry || !entry.sets.length) return null;
        var text = "Last: " + entry.sets.map(function (set) {
            var w = set.weight_kg != null ? set.weight_kg + "kg" : "BW";
            return w + "×" + (set.reps != null ? set.reps : "—");
        }).join(", ");
        return el("div", { class: "exercise-lastperformed", text: text });
    }

    // ── Ad-hoc supersets ────────────────────────────────────────────
    /* superset_group is a session-local integer. The one invariant every
       mutation here has to preserve: members of a group are always a
       contiguous run in s.exercises. That's what lets "linked with next"
       stand in for "same superset" without re-deriving anything, and it's
       why a reorder that interleaves a run has to break the group outright
       (see repairGroupsAfterReorder) rather than guess which half survives. */
    function linkedWithNext(s, idx) {
        var a = s.exercises[idx], b = s.exercises[idx + 1];
        return !!b && a.superset_group != null && a.superset_group === b.superset_group;
    }
    function nextGroupId(s) {
        var max = 0;
        s.exercises.forEach(function (ex) { if (ex.superset_group != null && ex.superset_group > max) max = ex.superset_group; });
        return max + 1;
    }
    function groupRun(s, idx) {
        var g = s.exercises[idx].superset_group;
        if (g == null) return [idx, idx];
        var start = idx, end = idx;
        while (start > 0 && s.exercises[start - 1].superset_group === g) start--;
        while (end < s.exercises.length - 1 && s.exercises[end + 1].superset_group === g) end++;
        return [start, end];
    }
    /* A run that's shrunk to one exercise isn't a superset any more. */
    function normalizeGroups(s) {
        var i = 0;
        while (i < s.exercises.length) {
            var run = groupRun(s, i);
            if (s.exercises[i].superset_group != null && run[1] === run[0]) s.exercises[i].superset_group = null;
            i = run[1] + 1;
        }
    }
    function toggleLink(s, exIdx) {
        if (linkedWithNext(s, exIdx)) {
            // Unlink: the back half of the run peels off under a fresh id.
            var run = groupRun(s, exIdx);
            var freshGroup = nextGroupId(s);
            for (var i = exIdx + 1; i <= run[1]; i++) s.exercises[i].superset_group = freshGroup;
        } else {
            var runA = groupRun(s, exIdx);
            var runB = groupRun(s, exIdx + 1);
            var group = s.exercises[exIdx].superset_group != null ? s.exercises[exIdx].superset_group
                : (s.exercises[exIdx + 1].superset_group != null ? s.exercises[exIdx + 1].superset_group : nextGroupId(s));
            for (var j = runA[0]; j <= runB[1]; j++) s.exercises[j].superset_group = group;
        }
        normalizeGroups(s);
        persistNow(s).then(function () { renderSession(s); });
    }
    /* A reorder can pull a third exercise into the middle of a run, or tear
       a run apart. Either way the run stops being contiguous, which a
       superset_group is never allowed to be — so the group breaks outright
       instead of silently keeping whichever half looks contiguous. */
    function repairGroupsAfterReorder(s) {
        var byGroup = {};
        s.exercises.forEach(function (ex, i) {
            if (ex.superset_group == null) return;
            (byGroup[ex.superset_group] = byGroup[ex.superset_group] || []).push(i);
        });
        var broken = false;
        Object.keys(byGroup).forEach(function (g) {
            var idxs = byGroup[g];
            var contiguous = idxs.every(function (idx, k) { return k === 0 || idx === idxs[k - 1] + 1; });
            if (!contiguous) {
                broken = true;
                idxs.forEach(function (idx) { s.exercises[idx].superset_group = null; });
            }
        });
        return broken;
    }
    function linkToggle(s, idx) {
        var linked = linkedWithNext(s, idx);
        return el("button", {
            class: "link-toggle" + (linked ? " link-toggle--linked" : ""),
            onclick: function () { toggleLink(s, idx); },
        }, [linked ? "🔗 Unlink superset" : "🔗 Link as superset"]);
    }

    function exerciseCard(s, ex, exIdx, ro) {
        var joinedAbove = exIdx > 0 && linkedWithNext(s, exIdx - 1);
        var joinedBelow = linkedWithNext(s, exIdx);
        var cardClass = "card" + (joinedAbove ? " card--joined-above" : "") + (joinedBelow ? " card--joined-below" : "");

        var handle = ro ? null : el("button", { class: "card__drag", type: "button", "aria-label": "Reorder exercise", tabindex: "-1" }, ["⠿"]);
        var head = el("div", { class: "card__head" }, [
            handle,
            el("div", { class: "card__headmain" }, [
                el("p", { class: "exercise-title" }, [
                    ex.title,
                    ex.superset_group != null ? el("span", { class: "superset-badge", text: "Superset" }) : null,
                ]),
                el("div", { class: "exercise-meta", text: exerciseMeta(ex) }),
                ro ? null : lastPerformedRow(ex),
            ]),
            ro ? null : el("button", { class: "iconbtn", onclick: function () {
                var i = s.exercises.indexOf(ex);
                if (i >= 0) s.exercises.splice(i, 1);
                normalizeGroups(s);
                persistNow(s).then(function () { renderSession(s); });
            } }, ["Remove"]),
        ]);
        var sets = el("div", { class: "card__sets" });
        var card = el("div", { class: cardClass, "data-ex": exIdx }, [head, sets]);
        if (handle) enableReorder(handle, card, s);

        // Hidden by CSS until a row follows it, so an empty card stays quiet.
        if (!ro) sets.appendChild(setHeadRow());
        ex.sets.forEach(function (set, i) {
            sets.appendChild(ro ? readSetRow(set, i) : editSetRow(s, ex, card, set));
        });

        if (!ro) {
            refreshBadges(card, ex);
            card.appendChild(
                el("button", { class: "btn btn--sm btn--ghost", style: "margin-top:12px", onclick: function () { addSet(s, ex, card); } }, ["＋ Add set"])
            );
        }
        return card;
    }

    function setHeadRow() {
        return el("div", { class: "setrow setrow--edit setrow--head" }, [
            el("span", {}),
            el("span", { text: "kg" }),
            el("span", { text: "reps" }),
            el("span", {}),
        ]);
    }

    /* A synced session is read-only: values render as text, as they always did. */
    function readSetRow(set, i) {
        var w = set.weight_kg != null ? set.weight_kg : "—";
        var reps = set.reps != null ? set.reps : "—";
        return el("div", { class: "setrow" }, [
            el("span", { class: "setrow__idx", text: String(i + 1) }),
            el("span", { class: "setrow__val", html: w + '<small>&nbsp;kg</small>' }),
            el("span", { class: "setrow__val", html: reps + '<small>&nbsp;reps</small>' }),
            el("span", { class: "setrow__type setrow__type--" + (set.set_type || "normal"), text: set.rpe != null ? "RPE " + set.rpe : (set.set_type !== "normal" ? set.set_type : "") }),
            el("span", {}),
        ]);
    }

    function numOrNull(raw, parse) {
        if (raw === "") return null;
        var n = parse(raw);
        return isNaN(n) ? null : n;
    }

    /* One editable row. Values go straight onto the set object as they are
       typed, so there is nothing to save and nothing to lose. Nothing in here
       re-renders: a re-render mid-keystroke would destroy the focus, the caret
       and the scroll position, which is the whole difficulty of this screen.
       Structural changes patch the DOM in place instead (see refreshBadges). */
    function editSetRow(s, ex, card, set) {
        var badge = el("button", { class: "setrow__badge", "aria-label": "Set type", onclick: function () { openSetType(s, ex, set, card); } });

        function cell(props, apply) {
            var input = el("input", props);
            input.addEventListener("input", function () { apply(input.value); schedulePersist(s); });
            input.addEventListener("change", function () { apply(input.value); flushPersist(); });
            input.addEventListener("blur", function () { flushPersist(); });
            input.addEventListener("focus", function () { keepRowVisible(row); });
            return input;
        }

        var weight = cell(
            { class: "input input--cell mono", type: "number", inputmode: "decimal", step: "0.5", min: "0", placeholder: "—", "aria-label": "Weight in kg", value: set.weight_kg != null ? set.weight_kg : "" },
            function (v) { set.weight_kg = numOrNull(v, parseFloat); }
        );
        var reps = cell(
            { class: "input input--cell mono", type: "number", inputmode: "numeric", min: "0", placeholder: "—", "aria-label": "Reps", value: set.reps != null ? set.reps : "" },
            function (v) { set.reps = numOrNull(v, function (x) { return parseInt(x, 10); }); }
        );
        var row = el("div", { class: "setrow setrow--edit" }, [
            badge, weight, reps,
            el("button", { class: "iconbtn setrow__del", "aria-label": "Delete set", onclick: function () { removeSet(s, ex, card, set, row); } }, ["✕"]),
        ]);
        return row;
    }

    /* Set type is rare enough to hide behind the set number: the badge shows
       the index for a normal set and an initial for anything else, and is the
       control that changes it. Badges carry the index, so every structural
       change restamps them — along with the card's set count. */
    var SET_TYPE_INITIAL = { warmup: "W", dropset: "D", failure: "F" };

    function refreshBadges(card, ex) {
        var badges = card.querySelectorAll(".setrow:not(.setrow--head) .setrow__badge");
        ex.sets.forEach(function (set, i) {
            if (!badges[i]) return;
            var type = set.set_type || "normal";
            badges[i].textContent = SET_TYPE_INITIAL[type] || String(i + 1);
            badges[i].className = "setrow__badge setrow__badge--" + type;
        });
        card.querySelector(".exercise-meta").textContent = exerciseMeta(ex);
    }

    /* Prefill carried over from the old add-set sheet: the previous set's
       weight and reps, else — for the first set of a prescribed exercise —
       the coach's target weight and the bottom of the prescribed rep range
       (#54: the default *value* of the input, not a placeholder to accept).
       The difference from the old sheet is that the prefill is now stored the
       moment the row appears — an untouched row is a logged set, not a
       discarded draft. */
    function defaultSetValues(ex, prev) {
        if (prev) {
            return {
                weight_kg: prev.weight_kg != null ? prev.weight_kg : null,
                reps: prev.reps != null ? prev.reps : null,
            };
        }
        return {
            weight_kg: ex.target_weight_kg != null ? ex.target_weight_kg : null,
            reps: repRangeLowerBound(ex.rep_range),
        };
    }

    /* Captured at write time, once, from the prescription that was live when
       the set was created — #58 reads this off GymWorkoutDetail to compare
       plan against actual without reconstructing it later. An ad-hoc
       exercise has no prescription, so both come back null: unprescribed,
       not falsely missed. */
    function prescribedForSet(ex) {
        return {
            prescribed_weight_kg: ex.target_weight_kg != null ? ex.target_weight_kg : null,
            prescribed_reps: repRangeLowerBound(ex.rep_range),
        };
    }

    function addSet(s, ex, card) {
        var prev = ex.sets.length ? ex.sets[ex.sets.length - 1] : null;
        var defaults = defaultSetValues(ex, prev);
        var prescribed = prescribedForSet(ex);
        var set = {
            weight_kg: defaults.weight_kg,
            reps: defaults.reps,
            rpe: null,
            set_type: "normal",
            prescribed_weight_kg: prescribed.prescribed_weight_kg,
            prescribed_reps: prescribed.prescribed_reps,
        };
        ex.sets.push(set);
        var row = editSetRow(s, ex, card, set);
        card.querySelector(".card__sets").appendChild(row);
        refreshBadges(card, ex);
        persistNow(s);
        var weight = row.querySelector("input");
        weight.focus();
        keepRowVisible(row);
    }

    function removeSet(s, ex, card, set, row) {
        var i = ex.sets.indexOf(set);
        if (i >= 0) ex.sets.splice(i, 1);
        row.remove();
        refreshBadges(card, ex);
        persistNow(s);
    }

    function openSetType(s, ex, set, card) {
        var current = set.set_type || "normal";
        openSheet("Set type", SET_TYPES.map(function (t) {
            return el("button", {
                class: "btn btn--block " + (t === current ? "btn--primary" : "btn--ghost"),
                style: "margin-top:8px;text-transform:capitalize",
                onclick: function () { set.set_type = t; closeSheet(); refreshBadges(card, ex); persistNow(s); },
            }, [t]);
        }));
    }

    /* ── Drag to reorder exercises ──────────────────────────────────
       HTML5 drag-and-drop never fires on touch, so this is built from
       Pointer Events by hand. A drag begins only from the grip handle —
       never the card body, which is now full of editable inputs (#49) —
       and only once a short long-press (or a deliberate move) has armed
       it, so a stray tap can't reorder. `touch-action: none` on the
       handle keeps the browser from claiming the gesture as a scroll,
       and the pointer is captured to the handle so events keep flowing
       as the finger travels over other cards.

       The lifted card is glued under the finger with a transform while
       its siblings stay put; a fixed drop-line marks where it will land.
       On release we splice `s.exercises` and persist — session-local,
       never written back to the routine (#51) — then re-render, which is
       safe here because a drop has no focus to lose. */
    var LONG_PRESS_MS = 160;
    var PRE_DRAG_SLOP = 8;   // a move past this on the handle arms the drag early
    var EDGE_ZONE = 72;      // autoscroll when the finger nears a viewport edge
    var EDGE_SPEED = 16;     // px per frame at the very edge
    var drag = null;
    var pendingReorder = null; // { cleanup } for a pointerdown not yet armed or released

    /* renderSession wipes the card list wholesale (`view.innerHTML = ""`),
       which on iOS Safari does not reliably fire pointercancel for a touch
       whose target got detached. Left alone, a pending long-press timer from
       a handle pressed moments ago fires anyway and arms a drag on a card
       that's no longer in the document — surfacing as a drag that hijacks
       whatever gesture (e.g. a plain scroll) happens to be live when the
       stale timer finally fires. Call this before any re-render wipes the
       cards, so an in-flight touch on a handle never outlives its element. */
    function cancelPendingReorder() {
        if (pendingReorder) { pendingReorder.cleanup(); pendingReorder = null; }
        if (drag) endDrag(false);
    }

    function enableReorder(handle, card, s) {
        handle.addEventListener("pointerdown", function (e) {
            if (drag || (e.button != null && e.button > 0)) return;
            e.preventDefault();
            var pid = e.pointerId;
            var startX = e.clientX, startY = e.clientY;
            var pressTimer = setTimeout(function () { arm(startY); }, LONG_PRESS_MS);

            function cleanup() {
                if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
                handle.removeEventListener("pointermove", preMove);
                handle.removeEventListener("pointerup", preEnd);
                handle.removeEventListener("pointercancel", preEnd);
                pendingReorder = null;
            }
            function arm(pointerY) {
                cleanup();
                if (!card.isConnected) return; // detached by a re-render since pointerdown
                beginDrag(s, card, handle, pid, pointerY);
            }
            function preMove(ev) {
                if (ev.pointerId !== pid) return;
                if (Math.abs(ev.clientY - startY) > PRE_DRAG_SLOP ||
                    Math.abs(ev.clientX - startX) > PRE_DRAG_SLOP) arm(ev.clientY);
            }
            function preEnd(ev) {
                if (ev.pointerId !== pid) return;
                cleanup(); // released or cancelled before arming — it was a tap
            }
            handle.addEventListener("pointermove", preMove);
            handle.addEventListener("pointerup", preEnd);
            handle.addEventListener("pointercancel", preEnd);
            pendingReorder = { cleanup: cleanup };
        });
    }

    function beginDrag(s, card, handle, pid, pointerY) {
        flushPersist(); // a set typed a moment ago must be saved before we re-render
        if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
        try { handle.setPointerCapture(pid); } catch (_) {}
        var rect = card.getBoundingClientRect();
        drag = {
            s: s, card: card, handle: handle, pid: pid,
            grabOffset: pointerY - rect.top,
            pointerY: pointerY,
            left: rect.left, width: rect.width,
            translateY: 0, targetIdx: null,
            line: el("div", { class: "drop-line" }),
            rafId: null,
        };
        card.classList.add("card--dragging");
        document.body.classList.add("reordering");
        document.body.appendChild(drag.line);
        handle.addEventListener("pointermove", onDragMove);
        handle.addEventListener("pointerup", onDragUp);
        handle.addEventListener("pointercancel", onDragCancel);
        positionDrag(pointerY);
        drag.rafId = requestAnimationFrame(autoscrollTick);
    }

    function onDragMove(e) {
        if (!drag || e.pointerId !== drag.pid) return;
        e.preventDefault();
        drag.pointerY = e.clientY;
        positionDrag(e.clientY);
    }
    function onDragUp(e) { if (drag && e.pointerId === drag.pid) { e.preventDefault(); endDrag(true); } }
    function onDragCancel(e) { if (drag && e.pointerId === drag.pid) endDrag(false); }

    /* Keep the lifted card's grabbed point under the finger (in viewport
       coordinates, so it holds still while the list autoscrolls beneath it),
       then recompute where it would drop. */
    function positionDrag(pointerY) {
        var card = drag.card;
        var natTop = card.getBoundingClientRect().top - drag.translateY;
        drag.translateY = (pointerY - drag.grabOffset) - natTop;
        card.style.transform = "translateY(" + drag.translateY + "px)";
        drag.targetIdx = targetIndex(pointerY);
        positionLine(drag.targetIdx);
    }

    function otherCards() {
        return Array.prototype.slice.call($("view").querySelectorAll(".card"))
            .filter(function (c) { return c !== drag.card; });
    }

    /* Insertion index into s.exercises *with the dragged item removed*:
       the count of other cards whose midpoint the finger has passed. */
    function targetIndex(pointerY) {
        var idx = 0;
        otherCards().forEach(function (c) {
            var r = c.getBoundingClientRect();
            if (pointerY > r.top + r.height / 2) idx++;
        });
        return idx;
    }

    function positionLine(idx) {
        var cards = otherCards();
        if (!cards.length) { drag.line.style.display = "none"; return; }
        var y;
        if (idx >= cards.length) y = cards[cards.length - 1].getBoundingClientRect().bottom + 5;
        else y = cards[idx].getBoundingClientRect().top - 7;
        drag.line.style.display = "block";
        drag.line.style.top = y + "px";
        drag.line.style.left = drag.left + "px";
        drag.line.style.width = drag.width + "px";
    }

    function autoscrollTick() {
        if (!drag) return;
        var y = drag.pointerY, vh = window.innerHeight, dy = 0;
        if (y < EDGE_ZONE) dy = -EDGE_SPEED * (1 - y / EDGE_ZONE);
        else if (y > vh - EDGE_ZONE) dy = EDGE_SPEED * (1 - (vh - y) / EDGE_ZONE);
        if (dy) { window.scrollBy(0, dy); positionDrag(drag.pointerY); }
        drag.rafId = requestAnimationFrame(autoscrollTick);
    }

    function endDrag(commit) {
        var d = drag;
        drag = null;
        if (!d) return;
        if (d.rafId) cancelAnimationFrame(d.rafId);
        d.handle.removeEventListener("pointermove", onDragMove);
        d.handle.removeEventListener("pointerup", onDragUp);
        d.handle.removeEventListener("pointercancel", onDragCancel);
        try { d.handle.releasePointerCapture(d.pid); } catch (_) {}
        d.line.remove();
        d.card.classList.remove("card--dragging");
        d.card.style.transform = "";
        document.body.classList.remove("reordering");

        var s = d.s;
        if (commit) {
            var from = parseInt(d.card.getAttribute("data-ex"), 10);
            var to = d.targetIdx != null ? d.targetIdx : from;
            if (to !== from) {
                var moved = s.exercises.splice(from, 1)[0];
                if (moved) s.exercises.splice(to, 0, moved);
                if (repairGroupsAfterReorder(s)) toast("Superset broken by reorder");
                persistNow(s).then(function () { renderSession(s); });
                return;
            }
        }
        renderSession(s); // nothing moved (or cancelled) — rebuild to a clean state
    }

    /* iOS raises the keyboard over the bottom of the screen only after focus
       lands, so centring the row has to wait for it; `scroll-margin` on the
       row keeps the action bar off it. */
    function keepRowVisible(row) {
        setTimeout(function () {
            if (row.isConnected && row.scrollIntoView) row.scrollIntoView({ block: "center", behavior: "smooth" });
        }, 220);
    }

    /* Rows appear prefilled and are stored immediately, so a tapped-then-
       abandoned row is a set with nothing in it. It is not data — and a
       null weight *and* null reps would poison the 1RM estimates the
       coaching prompts read — so it is dropped rather than synced. */
    function pruneEmptySets(exercises) {
        exercises.forEach(function (ex) {
            ex.sets = ex.sets.filter(function (set) { return set.weight_kg != null || set.reps != null; });
        });
    }

    /* RIR is asked once per exercise, on the top set only, at Finish — not
       live as sets are entered, so there's nothing to re-target mid-session.
       The overlay never blocks Finish: proceeding past it (or dismissing it)
       leaves any unanswered exercise's rpe untouched (null, same as every
       other set). */
    function promptFinish(s) {
        var tops = [];
        s.exercises.forEach(function (ex) {
            var set = topSetForExercise(ex);
            if (set) tops.push({ ex: ex, set: set });
        });
        if (!tops.length) { finishSession(s); return; }
        openRirSheet(s, tops);
    }

    function openRirSheet(s, tops) {
        var rows = tops.map(function (t) { return rirRow(t.set, t.ex.title); });
        openSheet("How many more reps could you have done?", rows.concat([
            el("button", {
                class: "btn btn--primary btn--block", style: "margin-top:16px",
                onclick: function () { closeSheet(); finishSession(s); },
            }, ["Save session"]),
        ]));
    }

    /* Selection state lives only in this closure, not on the set: the set
       has nowhere to put "3+ was tapped" that's distinct from "never asked"
       (both write rpe = null), and it doesn't need one — the sheet is thrown
       away the moment Finish completes. */
    function rirRow(set, title) {
        var buttons = {};
        function select(label) {
            set.rpe = RIR_TO_RPE[label];
            Object.keys(buttons).forEach(function (l) {
                buttons[l].className = "btn btn--sm " + (l === label ? "btn--primary" : "btn--ghost");
            });
        }
        Object.keys(RIR_TO_RPE).forEach(function (label) {
            buttons[label] = el("button", {
                class: "btn btn--sm btn--ghost", style: "flex:1",
                onclick: function () { select(label); },
            }, [label]);
        });
        return el("div", { class: "rir-row" }, [
            el("div", { class: "rir-row__title", text: title }),
            el("div", { class: "rir-row__buttons" }, Object.keys(RIR_TO_RPE).map(function (l) { return buttons[l]; })),
        ]);
    }

    function finishSession(s) {
        pruneEmptySets(s.exercises);
        s.end_time = new Date().toISOString();
        persistNow(s).then(function () {
            render();
            toast("Session saved");
            syncNow(false);
        });
    }

    function confirmCancel(s) {
        openSheet("Discard this session?", [
            el("p", { class: "sub", style: "margin-bottom:16px", text: "This is unrecoverable — nothing is kept, and nothing syncs to MyCoach." }),
            el("button", { class: "btn btn--danger btn--block", onclick: function () { releaseWakeLock(); dropPersist(); delSession(s.id).then(function () { closeSheet(); render(); toast("Session discarded"); }); } }, ["Discard session"]),
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:8px", onclick: closeSheet }, ["Keep going"]),
        ]);
    }

    function confirmDelete(s) {
        openSheet("Delete this session?", [
            el("p", { class: "sub", style: "margin-bottom:16px", text: "This removes it from the logger. Already-synced data stays in MyCoach." }),
            el("button", { class: "btn btn--danger btn--block", onclick: function () { delSession(s.id).then(function () { closeSheet(); render(); toast("Session deleted"); }); } }, ["Delete session"]),
            el("button", { class: "btn btn--ghost btn--block", style: "margin-top:8px", onclick: closeSheet }, ["Cancel"]),
        ]);
    }

    // ── Sheets: add exercise / set type / settings ──────────────────
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
            (state.exerciseCache || []).map(function (exercise) {
                return el("option", { value: typeof exercise === "string" ? exercise : exercise.name });
            }));
        var input = el("input", { class: "input", list: listId, placeholder: "e.g. Bench Press", autocomplete: "off", autocapitalize: "words" });
        function add() {
            var choice = resolveExerciseChoice(input.value, state.exerciseCache);
            if (!choice.title) return;
            var ex = {
                exercise_id: choice.exercise_id,
                title: choice.title,
                notes: null,
                sets: [],
                superset_group: null,
            };
            s.exercises.push(ex);
            persistNow(s).then(function () {
                closeSheet();
                renderSession(s);
                addSet(s, ex, cardFor(s.exercises.length - 1));
            });
        }
        openSheet("Add exercise", [
            el("div", { class: "field" }, [datalist, input]),
            el("button", { class: "btn btn--primary btn--block", onclick: add }, ["Add exercise"]),
        ]);
        setTimeout(function () { input.focus(); }, 50);
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
                pullWeek();
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
    // Guarded so this file can also be `require()`d by node:test for its
    // pure functions (see the export guard below), which has no DOM.
    if (typeof document !== "undefined") {
        $("sync-chip").addEventListener("click", function () { syncNow(true); });
        window.addEventListener("online", function () { refreshChip(); syncNow(false); pullWeek(); });
        window.addEventListener("offline", refreshChip);
        // A swipe-away kill does not always fire visibilitychange first.
        window.addEventListener("pagehide", function () { flushPersist(); });
        document.addEventListener("visibilitychange", function () {
            // Restoring connectivity and reopening the tab should retry
            // without waiting for the next manual sync press.
            if (document.visibilityState !== "visible") { flushPersist(); return; }
            syncNow(false);
            // iOS silently drops the lock when the tab backgrounds.
            if (wakeWanted) acquireWakeLock();
        });

        getMeta("exercises").then(function (list) { if (list) state.exerciseCache = list; });
        getMeta("lastPerformed").then(function (list) { if (list) state.lastPerformedCache = list; });
        getMeta("week").then(function (w) {
            if (!w) return;
            state.week = w;
            if (state.activeId === null && !document.querySelector(".sheet-backdrop")) render();
        });

        if ("serviceWorker" in navigator) {
            var swUrl = STANDALONE ? "/sw.js" : "/logger/sw.js";
            var swScope = STANDALONE ? "/" : "/logger";
            navigator.serviceWorker.register(swUrl, { scope: swScope }).catch(function (e) {
                console.warn("[logger] SW registration failed:", e);
            });
        }

        render();
        pullExercises();
        pullWeek();
        syncNow(false);
    }

    /* Dev-only: exposes pure functions to node:test. `module` is undefined in
       the browser, so this branch never runs there. */
    if (typeof module !== "undefined" && module.exports) {
        module.exports = { toPayload: toPayload, repRangeLowerBound: repRangeLowerBound, numOrNull: numOrNull, pruneEmptySets: pruneEmptySets, topSetForExercise: topSetForExercise, resolveExerciseChoice: resolveExerciseChoice, sessionExerciseFromPrescribed: sessionExerciseFromPrescribed, loggableSessions: loggableSessions, prescribedMeta: prescribedMeta, outstandingSessions: outstandingSessions, defaultSetValues: defaultSetValues, prescribedForSet: prescribedForSet, lastPerformedFor: lastPerformedFor };
    }
})();
