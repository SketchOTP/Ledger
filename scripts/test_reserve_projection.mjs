import assert from "node:assert/strict";
import test from "node:test";

import { projectReserveLines } from "../frontend/src/lib/reserveProjection.ts";

const days = (length, amountAt) => Array.from({ length }, (_, index) => ({
  date: new Date(Date.UTC(2026, 8, 24 + index)).toISOString().slice(0, 10),
  balance: amountAt(index),
}));

const snapshot = (overrides = {}) => ({
  cushionCurrent: 80,
  cushionGoal: 100,
  suggestedRefill30Days: 50,
  nearTermSurprises: 100,
  uncommittedNow: 200,
  extraSpendNow: 50,
  extraSpendShare: 0.25,
  usualSpending30Days: [],
  ...overrides,
});

test("suggested cushion steps never exceed the owner's goal", () => {
  const result = projectReserveLines(days(91, () => 1_000), snapshot());
  assert.equal(result[0].cushion, 80);
  assert.equal(result[30].cushion, 100);
  assert.ok(result.every((point) => point.cushion <= 100));
});

test("a cash dip draws down the projected cushion and paced steps rebuild it", () => {
  const result = projectReserveLines(days(151, (index) => index === 45 ? 40 : 1_000), snapshot());
  assert.equal(result[45].cushion, 40);
  assert.equal(result[45].surprises, 40);
  assert.ok(result[60].cushion > 40);
  assert.equal(result[120].cushion, 100);
});

test("uncommitted money grows and falls with dated cash, without being a second account", () => {
  const result = projectReserveLines(days(400, (index) => index < 183 ? 1_000 : index === 380 ? 850 : 1_200), snapshot());
  assert.equal(result[0].uncommitted, 200);
  assert.ok(result[183].uncommitted > result[0].uncommitted);
  assert.ok(result[230].uncommitted < result[183].uncommitted);
  assert.ok(result[381].uncommitted > result[230].uncommitted);
  assert.ok(result.every((point) => point.uncommitted <= 1_200));
});

test("usual spending reduces virtual uncommitted cash, and unknown cushion stays unknown", () => {
  const scheduled = days(31, () => 0).map((row) => ({ date: row.date, amount: 10 }));
  const result = projectReserveLines(days(61, () => 1_000), snapshot({
    cushionCurrent: null,
    suggestedRefill30Days: 0,
    usualSpending30Days: scheduled,
  }));
  assert.ok(result.every((point) => point.cushion === null));
  assert.equal(result[30].uncommitted, result[0].uncommitted); // Future essentials already constrain today.
  assert.ok(result[60].uncommitted <= result[30].uncommitted);
});

test("balance line deducts dated everyday spending and continues its pace beyond 30 days", () => {
  const scheduled = days(31, () => 0).map((row) => ({ date: row.date, amount: 10 }));
  const result = projectReserveLines(days(33, (index) => index < 2 ? 100 : 80), snapshot({
    usualSpending30Days: scheduled,
  }));
  assert.equal(result[0].balanceAfterSpending, 90);
  assert.equal(result[1].balanceAfterSpending, 80);
  assert.equal(result[2].balanceAfterSpending, 50);
  assert.equal(result[30].balanceAfterSpending, -230);
  assert.equal(result[31].balanceAfterSpending, -240);
  assert.equal(result[32].balanceAfterSpending, -250);
});

test("safe optional spending starts from Today's separate observed split", () => {
  const result = projectReserveLines(days(61, () => 1_000), snapshot({ suggestedRefill30Days: 0 }));
  assert.equal(result[0].uncommitted, 200);
  assert.equal(result[0].extraSpend, 50);
  assert.equal(result[0].uncommitted + result[0].extraSpend, 250);
});

test("a bill inside six months is already protected in today's spending amount", () => {
  const result = projectReserveLines(days(61, (index) => index === 30 ? 970 : 1_000),
    snapshot({ suggestedRefill30Days: 0 }));
  assert.equal(result[1].extraSpend, 50);
  assert.equal(result[1].uncommitted, 200);
  assert.equal(result[31].extraSpend, 57.5);
  assert.ok(result.every((point) => point.extraSpend >= 0));
});

test("a suggested cushion contribution reduces optional spending even under a larger surprise floor", () => {
  const result = projectReserveLines(days(61, () => 1_000), snapshot({ cushionGoal: 100 }));
  assert.equal(result[0].extraSpend, 50);
  assert.equal(result[30].cushion, 100);
  assert.equal(result[30].uncommitted, 200);
  assert.equal(result[30].extraSpend, 30);
});

test("new surplus splits between uncommitted and optional, while missing facts suppress optional", () => {
  const result = projectReserveLines(days(91, (index) => index < 31 ? 1_000 : 1_200),
    snapshot({ suggestedRefill30Days: 0 }));
  assert.equal(result[0].extraSpend, 50);
  assert.ok(result[31].uncommitted > 200);
  assert.ok(result[31].extraSpend > 50);
  const incomplete = projectReserveLines(days(31, () => 1_000), snapshot({ extraSpendNow: null }));
  assert.ok(incomplete.every((point) => point.extraSpend === null));
});

test("zero optional today can grow only after future protected headroom increases", () => {
  const result = projectReserveLines(days(91, (index) => index < 31 ? 1_000 : 1_200), snapshot({
    suggestedRefill30Days: 0,
    extraSpendNow: 0,
  }));
  assert.equal(result[0].extraSpend, 0);
  assert.equal(result[31].extraSpend, 50);
  assert.equal(result[31].uncommitted, 350);
});

 test("a month-five bill constrains free money from the start", () => {
 const result = projectReserveLines(days(200, index => index === 150 ? 150 : 1000), snapshot({cushionCurrent:100, cushionGoal:100, nearTermSurprises:0, suggestedRefill30Days:0, uncommittedNow:0, extraSpendNow:50, extraSpendShare:1}));
 assert.equal(result[0].extraSpend,50);
 assert.equal(result[100].extraSpend,50);
 assert.equal(result[151].extraSpend,900);
 });
