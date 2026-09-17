const test = require("node:test");
const assert = require("node:assert/strict");
const {
    repRangeLowerBound,
    numOrNull,
    toPayload,
    pruneEmptySets,
    topSetForExercise,
    resolveExerciseChoice,
    sessionExerciseFromPrescribed,
    loggableSessions,
    prescribedMeta,
    outstandingSessions,
} = require("./app.js");

test("resolveExerciseChoice turns a cached catalogue name into its stable id", () => {
    const choice = resolveExerciseChoice("barbell squat", [
        { id: "Barbell_Squat", name: "Barbell Squat" },
    ]);

    assert.deepEqual(choice, {
        exercise_id: "Barbell_Squat",
        title: "Barbell Squat",
    });
});

test("sessionExerciseFromPrescribed carries stable identity into offline state", () => {
    const exercise = sessionExerciseFromPrescribed({
        exercise_id: "Barbell_Squat",
        name: "Barbell Squat",
        notes: null,
        sets: 3,
        rep_range: "8-10",
        superset_group: null,
    });

    assert.equal(exercise.exercise_id, "Barbell_Squat");
    assert.equal(exercise.title, "Barbell Squat");
});

test("sessionExerciseFromPrescribed carries the coach's numbers onto the phone", () => {
    // The whole reason the picker moved to the week: a routine day had no
    // weights, a prescription does.
    const exercise = sessionExerciseFromPrescribed({
        exercise_id: "Barbell_Squat",
        name: "Barbell Squat",
        sets: 3,
        rep_range: "5",
        target_weight_kg: 82.5,
        target_rpe: 8,
        rest_seconds: 180,
        superset_group: 2,
    });

    assert.equal(exercise.target_weight_kg, 82.5);
    assert.equal(exercise.target_rpe, 8);
    assert.equal(exercise.rest_seconds, 180);
    assert.equal(exercise.target_sets, 3);
    assert.equal(exercise.rep_range, "5");
    assert.equal(exercise.superset_group, 2);
    assert.deepEqual(exercise.sets, []);
});

test("sessionExerciseFromPrescribed nulls the numbers a routine-built session lacks", () => {
    const exercise = sessionExerciseFromPrescribed({
        exercise_id: null,
        name: "Bulgarian. Split Squat",
        sets: 3,
        rep_range: "8-10",
    });

    assert.equal(exercise.exercise_id, null);
    assert.equal(exercise.target_weight_kg, null);
    assert.equal(exercise.target_rpe, null);
    assert.equal(exercise.rest_seconds, null);
    assert.equal(exercise.superset_group, null);
});

test("loggableSessions keeps gym and drops the sports the logger does not log", () => {
    const week = {
        week_start: "2024-06-10",
        sessions: [
            { id: 1, title: "Push", loggable: true },
            { id: 2, title: "Easy Run", loggable: false },
        ],
    };

    assert.deepEqual(loggableSessions(week), [week.sessions[0]]);
});

test("loggableSessions tolerates no cached week at all", () => {
    assert.deepEqual(loggableSessions(null), []);
    assert.deepEqual(loggableSessions({ week_start: "2024-06-10" }), []);
});

test("prescribedMeta says how many exercises carry the coach's weights", () => {
    const meta = prescribedMeta({
        exercises: [
            { name: "Barbell Squat", target_weight_kg: 82.5 },
            { name: "Leg Press", target_weight_kg: 140 },
            { name: "Plank", target_weight_kg: null },
        ],
    });

    assert.equal(meta, "3 exercises · 2 prescribed");
});

test("prescribedMeta marks a routine-built session as carrying no weights", () => {
    const meta = prescribedMeta({ exercises: [{ name: "Barbell Squat", target_weight_kg: null }] });

    assert.equal(meta, "1 exercise · no weights");
});

test("repRangeLowerBound reads the lower bound of a range like '8-10'", () => {
    assert.equal(repRangeLowerBound("8-10"), 8);
});

test("repRangeLowerBound reads a bare number as its own lower bound", () => {
    assert.equal(repRangeLowerBound("12"), 12);
});

test("repRangeLowerBound returns null for empty or unparseable input", () => {
    assert.equal(repRangeLowerBound(""), null);
    assert.equal(repRangeLowerBound(null), null);
    assert.equal(repRangeLowerBound("failure"), null);
});

test("numOrNull parses a numeric string with the given parser", () => {
    assert.equal(numOrNull("82.5", parseFloat), 82.5);
});

test("numOrNull treats an empty string as null, not NaN", () => {
    assert.equal(numOrNull("", parseFloat), null);
});

test("numOrNull treats unparseable input as null", () => {
    assert.equal(numOrNull("abc", parseFloat), null);
});

test("toPayload flattens a session's exercises into one flat sets array", () => {
    const session = {
        id: "s1",
        title: "Push day",
        start_time: "2026-09-01T10:00:00Z",
        end_time: "2026-09-01T11:00:00Z",
        notes: null,
        exercises: [
            {
                exercise_id: "Barbell_Bench_Press_-_Medium_Grip",
                title: "Bench Press",
                notes: null,
                superset_group: null,
                sets: [
                    { weight_kg: 80, reps: 5, rpe: 8, set_type: "normal", prescribed_weight_kg: 80, prescribed_reps: 5 },
                    { weight_kg: 82.5, reps: 4, rpe: null, set_type: "normal" },
                ],
            },
        ],
    };

    const payload = toPayload(session);

    assert.equal(payload.external_id, "s1");
    assert.equal(payload.sport, "gym");
    assert.equal(payload.sets.length, 2);
    assert.deepEqual(payload.sets[0], {
        exercise_id: "Barbell_Bench_Press_-_Medium_Grip",
        exercise_title: "Bench Press",
        exercise_notes: null,
        set_index: 1,
        set_type: "normal",
        superset_id: null,
        weight_kg: 80,
        reps: 5,
        rpe: 8,
        prescribed_weight_kg: 80,
        prescribed_reps: 5,
    });
    assert.equal(payload.sets[1].set_index, 2);
    assert.equal(payload.sets[1].prescribed_weight_kg, null);
});

test("pruneEmptySets drops sets with neither weight nor reps", () => {
    const exercises = [
        {
            title: "Squat",
            sets: [
                { weight_kg: 100, reps: 5 },
                { weight_kg: null, reps: null },
                { weight_kg: null, reps: 3 },
            ],
        },
    ];

    pruneEmptySets(exercises);

    assert.equal(exercises[0].sets.length, 2);
    assert.deepEqual(exercises[0].sets.map((s) => s.reps), [5, 3]);
});

test("topSetForExercise picks the heaviest working set", () => {
    const ex = {
        sets: [
            { weight_kg: 80, reps: 8, set_type: "normal" },
            { weight_kg: 100, reps: 3, set_type: "normal" },
            { weight_kg: 90, reps: 5, set_type: "normal" },
        ],
    };
    assert.equal(topSetForExercise(ex), ex.sets[1]);
});

test("topSetForExercise breaks ties in favour of the first set reached", () => {
    const ex = {
        sets: [
            { weight_kg: 100, reps: 5, set_type: "normal" },
            { weight_kg: 100, reps: 4, set_type: "normal" },
        ],
    };
    assert.equal(topSetForExercise(ex), ex.sets[0]);
});

test("topSetForExercise never picks a warmup set", () => {
    const ex = {
        sets: [
            { weight_kg: 120, reps: 5, set_type: "warmup" },
            { weight_kg: 100, reps: 5, set_type: "normal" },
        ],
    };
    assert.equal(topSetForExercise(ex), ex.sets[1]);
});

test("topSetForExercise returns null when every set is a warmup", () => {
    const ex = { sets: [{ weight_kg: 60, reps: 8, set_type: "warmup" }] };
    assert.equal(topSetForExercise(ex), null);
});

test("topSetForExercise returns null with no sets at all", () => {
    assert.equal(topSetForExercise({ sets: [] }), null);
});

test("topSetForExercise treats a bodyweight exercise's first working set as top", () => {
    const ex = {
        sets: [
            { weight_kg: null, reps: 12, set_type: "normal" },
            { weight_kg: null, reps: 10, set_type: "normal" },
        ],
    };
    assert.equal(topSetForExercise(ex), ex.sets[0]);
});

/* The union rule (#69): a prescription is outstanding unless the server says
   it is done or this phone already holds a session answering it. Cancelling a
   session deletes it locally, so the prescription correctly reappears. */
test("outstandingSessions keeps a prescription nothing has answered", () => {
    const week = [{ id: 1, title: "Push", done: false }];

    assert.deepEqual(outstandingSessions(week, []), week);
});

test("outstandingSessions drops a prescription the server has marked done", () => {
    const week = [{ id: 1, title: "Push", done: true }];

    assert.deepEqual(outstandingSessions(week, []), []);
});

test("outstandingSessions drops a prescription a local session already answers", () => {
    const week = [{ id: 1, title: "Push", done: false }];
    const local = [{ id: "uuid-a", planned_session_id: 1 }];

    assert.deepEqual(outstandingSessions(week, local), []);
});

test("outstandingSessions counts an unsynced local session as consumed", () => {
    // The stale `done` flag is the fallback, not the answer: a session lifted
    // in a basement is consumed long before the server hears about it.
    const week = [{ id: 1, title: "Push", done: false }];
    const local = [{ id: "uuid-a", planned_session_id: 1, synced: false }];

    assert.deepEqual(outstandingSessions(week, local), []);
});

test("outstandingSessions keeps a routine-built session, which has no id to consume", () => {
    const week = [{ id: null, title: "Pull", done: false }];
    const local = [{ id: "uuid-a", planned_session_id: null }];

    assert.deepEqual(outstandingSessions(week, local), week);
});

test("outstandingSessions leaves non-gym sessions in — filtering is the caller's job", () => {
    const week = [{ id: 2, title: "Easy Run", done: false, loggable: false }];

    assert.deepEqual(outstandingSessions(week, []), week);
});

test("toPayload forwards the prescription id the session answers", () => {
    const payload = toPayload({
        id: "uuid-a",
        title: "Push",
        start_time: "2024-06-10T09:00:00",
        end_time: "2024-06-10T10:00:00",
        planned_session_id: 7,
        exercises: [],
    });

    assert.equal(payload.planned_session_id, 7);
});

test("toPayload sends a null prescription id for an unprescribed session", () => {
    const payload = toPayload({
        id: "uuid-b",
        title: "Push",
        start_time: "2024-06-10T09:00:00",
        end_time: null,
        exercises: [],
    });

    assert.equal(payload.planned_session_id, null);
});
