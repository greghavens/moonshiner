// Contract tests for the meeting-room booking API — protected file.
import test from "node:test";
import assert from "node:assert/strict";
import request from "supertest";

import { createApp } from "./app.mjs";

const ROOMS = ["kyoto", "oslo", "lima"];

function makeApp() {
  return createApp({ rooms: ROOMS });
}

function booking(overrides = {}) {
  return {
    room: "kyoto",
    title: "sprint planning",
    start: "2026-07-20T14:00:00Z",
    end: "2026-07-20T15:00:00Z",
    ...overrides,
  };
}

test("list starts empty", async () => {
  const r = await request(makeApp()).get("/api/bookings");
  assert.equal(r.status, 200);
  assert.deepEqual(r.body, { bookings: [] });
});

test("create returns 201 with the stored booking, ids start at 1", async () => {
  const r = await request(makeApp()).post("/api/bookings").send(booking());
  assert.equal(r.status, 201);
  assert.deepEqual(r.body, {
    id: 1,
    room: "kyoto",
    title: "sprint planning",
    start: "2026-07-20T14:00:00Z",
    end: "2026-07-20T15:00:00Z",
  });
});

test("ids count up and the list is ordered by start time", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(
    booking({ title: "late", start: "2026-07-20T16:00:00Z", end: "2026-07-20T17:00:00Z" }));
  await agent.post("/api/bookings").send(
    booking({ title: "early", start: "2026-07-20T09:00:00Z", end: "2026-07-20T10:00:00Z" }));
  const r = await agent.get("/api/bookings");
  assert.equal(r.status, 200);
  assert.deepEqual(r.body.bookings.map((b) => [b.id, b.title]),
    [[2, "early"], [1, "late"]]);
});

test("get by id returns the booking", async () => {
  const app = makeApp();
  await request(app).post("/api/bookings").send(booking());
  const r = await request(app).get("/api/bookings/1");
  assert.equal(r.status, 200);
  assert.equal(r.body.title, "sprint planning");
});

test("get unknown id is a JSON 404", async () => {
  const r = await request(makeApp()).get("/api/bookings/41");
  assert.equal(r.status, 404);
  assert.match(r.headers["content-type"], /application\/json/);
  assert.deepEqual(r.body, { error: "not found" });
});

test("get non-numeric id is a JSON 404", async () => {
  const r = await request(makeApp()).get("/api/bookings/tuesday");
  assert.equal(r.status, 404);
  assert.deepEqual(r.body, { error: "not found" });
});

test("room filter returns only that room, still ordered by start", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(booking({ room: "oslo", title: "o1" }));
  await agent.post("/api/bookings").send(
    booking({ room: "kyoto", title: "k1", start: "2026-07-21T10:00:00Z", end: "2026-07-21T11:00:00Z" }));
  await agent.post("/api/bookings").send(
    booking({ room: "oslo", title: "o2", start: "2026-07-20T08:00:00Z", end: "2026-07-20T09:00:00Z" }));
  const r = await agent.get("/api/bookings").query({ room: "oslo" });
  assert.equal(r.status, 200);
  assert.deepEqual(r.body.bookings.map((b) => b.title), ["o2", "o1"]);
});

test("filtering by a room that is not configured is a 400", async () => {
  const r = await request(makeApp()).get("/api/bookings").query({ room: "atlantis" });
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "unknown room" });
});

test("booking a room that is not configured is a 400", async () => {
  const r = await request(makeApp()).post("/api/bookings")
    .send(booking({ room: "atlantis" }));
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "unknown room" });
});

test("missing or blank title is a 400", async () => {
  const app = makeApp();
  const noTitle = { ...booking() };
  delete noTitle.title;
  let r = await request(app).post("/api/bookings").send(noTitle);
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "title is required" });
  r = await request(app).post("/api/bookings").send(booking({ title: "   " }));
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "title is required" });
});

test("end before or equal to start is a 400, as are unparseable stamps", async () => {
  const app = makeApp();
  let r = await request(app).post("/api/bookings").send(
    booking({ start: "2026-07-20T15:00:00Z", end: "2026-07-20T15:00:00Z" }));
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "invalid time range" });
  r = await request(app).post("/api/bookings").send(booking({ start: "half past nine" }));
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "invalid time range" });
});

test("overlapping booking in the same room is a 409 naming the conflict", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(booking());
  const r = await agent.post("/api/bookings").send(
    booking({ title: "standup", start: "2026-07-20T14:30:00Z", end: "2026-07-20T15:30:00Z" }));
  assert.equal(r.status, 409);
  assert.deepEqual(r.body, { error: "time conflict", with: 1 });
});

test("a booking fully inside an existing one is a 409", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(
    booking({ start: "2026-07-20T10:00:00Z", end: "2026-07-20T12:00:00Z" }));
  const r = await agent.post("/api/bookings").send(
    booking({ start: "2026-07-20T10:30:00Z", end: "2026-07-20T11:00:00Z" }));
  assert.equal(r.status, 409);
  assert.deepEqual(r.body, { error: "time conflict", with: 1 });
});

test("back-to-back bookings do not conflict (half-open intervals)", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(booking());
  const r = await agent.post("/api/bookings").send(
    booking({ title: "retro", start: "2026-07-20T15:00:00Z", end: "2026-07-20T16:00:00Z" }));
  assert.equal(r.status, 201);
});

test("same time in a different room does not conflict", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(booking());
  const r = await agent.post("/api/bookings").send(booking({ room: "oslo" }));
  assert.equal(r.status, 201);
});

test("non-JSON body is a 400 asking for JSON", async () => {
  const r = await request(makeApp()).post("/api/bookings")
    .set("content-type", "text/plain").send("room=kyoto");
  assert.equal(r.status, 400);
  assert.deepEqual(r.body, { error: "json body required" });
});

test("syntactically broken JSON is a 400, not an HTML error page", async () => {
  const r = await request(makeApp()).post("/api/bookings")
    .set("content-type", "application/json").send('{"room": "kyoto",');
  assert.equal(r.status, 400);
  assert.match(r.headers["content-type"], /application\/json/);
  assert.deepEqual(r.body, { error: "invalid json" });
});

test("unknown routes come back as JSON 404, never HTML", async () => {
  const r = await request(makeApp()).get("/definitely/not/here");
  assert.equal(r.status, 404);
  assert.match(r.headers["content-type"], /application\/json/);
  assert.deepEqual(r.body, { error: "not found" });
});

test("delete frees the slot and unknown deletes are JSON 404", async () => {
  const app = makeApp();
  const agent = request(app);
  await agent.post("/api/bookings").send(booking());
  let r = await agent.delete("/api/bookings/1");
  assert.equal(r.status, 204);
  r = await agent.get("/api/bookings/1");
  assert.equal(r.status, 404);
  r = await agent.post("/api/bookings").send(booking({ title: "reclaimed" }));
  assert.equal(r.status, 201);
  r = await agent.delete("/api/bookings/99");
  assert.equal(r.status, 404);
  assert.deepEqual(r.body, { error: "not found" });
});

test("two createApp instances keep separate stores", async () => {
  const a = makeApp();
  const b = makeApp();
  await request(a).post("/api/bookings").send(booking({ title: "only-in-a" }));
  const rb = await request(b).get("/api/bookings");
  assert.deepEqual(rb.body, { bookings: [] });
  const ra = await request(a).get("/api/bookings");
  assert.equal(ra.body.bookings[0].title, "only-in-a");
});
