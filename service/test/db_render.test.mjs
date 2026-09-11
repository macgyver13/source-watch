import assert from "node:assert/strict";
import test from "node:test";
import { CHUNK, writeRendered } from "../src/db.js";

const UNPAIRED = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

function recordingEnv() {
  const statements = [];
  return {
    statements,
    env: {
      DB: {
        prepare(sql) {
          return {
            bind(...args) {
              const stmt = { sql, args };
              statements.push(stmt);
              return stmt;
            },
          };
        },
        async batch() {},
      },
    },
  };
}

function chunkBodies(statements) {
  return statements
    .filter((row) => row.sql.includes("INSERT INTO rendered (name, seq, body)"))
    .map((row) => row.args[2]);
}

test("writeRendered never splits a surrogate pair", async () => {
  const { env, statements } = recordingEnv();
  const text = "😀".repeat(CHUNK);
  await writeRendered(env, "feed.json", text, "application/json");
  const bodies = chunkBodies(statements);
  assert.ok(bodies.length >= 2, "emoji body should span more than one chunk");
  for (const body of bodies) {
    assert.equal(UNPAIRED.test(body), false);
  }
  assert.equal(bodies.join(""), text);
});

test("writeRendered backs up when a chunk would end on a high surrogate", async () => {
  const { env, statements } = recordingEnv();
  const text = `x${"😀".repeat(CHUNK)}`;
  await writeRendered(env, "feed.json", text, "application/json");
  const bodies = chunkBodies(statements);
  assert.ok(bodies.length >= 2);
  for (const body of bodies) {
    assert.equal(UNPAIRED.test(body), false);
  }
  assert.equal(bodies.join(""), text);
  assert.notEqual(bodies[0].length, CHUNK, "chunk must not end on a high surrogate");
});
