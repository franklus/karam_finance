const assert = require("node:assert/strict");
const {
  createHarness
} = require("./item_price_on_rate_mismatch.behaviour.support.cjs");

async function triggerManualPrompt(harness) {
  const { row } = harness;
  harness.dispatchPricingFieldInput("price_list_rate");
  row.rate = 8.1;
  row.price_list_rate = 8.1;
  harness.handlers.price_list_rate(harness.form(), harness.childDoctype, harness.cdn);
  await harness.flushTimers();
}

function assertPrompted(harness) {
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.calls[0].args.price_list_rate, 8.1);
  assert.equal(harness.confirms.length, 1);
}

async function testRegistrationAndScope() {
  const harness = createHarness();
  ["rate", "discount_percentage", "discount_amount", "margin_type"].forEach(
    (fieldname) => {
      assert.equal(harness.handlers[fieldname], undefined);
    }
  );
  await triggerManualPrompt(harness);
  assertPrompted(harness);
  assert.equal(harness.calls[0].args.batch_no, "BATCH-001");
  assert.equal(harness.calls[0].args.qty, 12);
  assert.equal("packing_unit" in harness.calls[0].args, false);
  assert.match(harness.confirms[0], /<strong>Supplier:<\/strong> SUP0085: Sodamco SAL/);
}

async function testBrowserAndModelEvents() {
  const browser = createHarness();
  browser.row.rate = 8.1;
  browser.row.price_list_rate = 8.1;
  browser.dispatchPriceListRateChange();
  await browser.flushTimers();
  assertPrompted(browser);

  const model = createHarness();
  model.dispatchPricingFieldInput("price_list_rate");
  model.row.rate = 8.1;
  model.row.price_list_rate = 8.1;
  model.dispatchPriceListRateModelChange();
  await model.flushTimers();
  assertPrompted(model);
}

async function testNewRowAndRejection() {
  const fresh = createHarness({ seedInitialPriceListRate: false });
  fresh.row.item_code = "ITM00189";
  fresh.row.rate = 1265;
  fresh.row.price_list_rate = 1265;
  fresh.dispatchPriceListRateModelChange();
  fresh.handlers.price_list_rate(fresh.form(), fresh.childDoctype, fresh.cdn);
  await fresh.flushTimers();
  assert.equal(fresh.calls.length, 0);

  const rejected = createHarness();
  rejected.form().doc.__islocal = 1;
  await triggerManualPrompt(rejected);
  await rejected.rejectPrompt();
  assert.equal(rejected.row.price_list_rate, 6.56);
  assert.equal(rejected.row.rate, 6.56);
  assert.equal(
    rejected.alerts.at(-1).message,
    "Rate reverted to the existing Item Price value."
  );
}

async function testDiscountSuppression() {
  await Promise.all(
    ["discount_percentage", "discount_amount"].map(async (fieldname) => {
      const harness = createHarness();
      harness.row[fieldname] = 10;
      harness.dispatchModelChange(fieldname);
      harness.row.rate = 7.29;
      harness.dispatchPriceListRateModelChange();
      await harness.flushTimers();
      assert.equal(harness.calls.length, 0);
      assert.equal(harness.confirms.length, 0);
    })
  );

  const input = createHarness();
  input.dispatchPricingFieldInput("discount_percentage");
  input.dispatchPricingFieldChange("discount_percentage");
  input.row.rate = 7.29;
  input.dispatchPriceListRateModelChange();
  await input.flushTimers();
  assert.equal(input.calls.length, 0);

  const explicit = createHarness();
  explicit.dispatchModelChange("discount_percentage");
  explicit.row.rate = 8.1;
  explicit.row.price_list_rate = 8.1;
  explicit.dispatchPriceListRateChange();
  await explicit.flushTimers();
  assertPrompted(explicit);
}

async function testPromptOnceAndErrors() {
  const both = createHarness();
  both.row.rate = 8.1;
  both.row.price_list_rate = 8.1;
  both.dispatchPriceListRateChange();
  both.handlers.price_list_rate(both.form(), both.childDoctype, both.cdn);
  await both.flushTimers();
  assertPrompted(both);

  const generic = createHarness();
  generic.setCallImplementation(() => rejectFrappeResponse("Network failed"));
  await triggerManualPrompt(generic);
  assert.equal(
    generic.alerts[0].message,
    "Unable to prepare the Item Price prompt right now."
  );

  const duplicate = createHarness();
  duplicate.setCallImplementation(() =>
    rejectFrappeResponse("with the same Valid From date")
  );
  await triggerManualPrompt(duplicate);
  assert.equal(duplicate.alerts.length, 0);
  assert.equal(duplicate.messages.length, 0);
}

async function testDuplicateReversionWaitsForDialog() {
  const harness = createHarness({
    installMessageDialogOnTimer: true,
    withMessageDialog: false
  });
  harness.setCallImplementation(() =>
    rejectFrappeResponse("with the same Valid From date")
  );
  await triggerManualPrompt(harness);
  await harness.flushTimers();
  assert.equal(harness.row.price_list_rate, 8.1);
  await harness.dismissMessageDialog();
  assert.equal(harness.row.price_list_rate, 6.56);
  assert.equal(
    harness.alerts.at(-1).message,
    "Rate reverted to the existing Item Price value."
  );
}

function rejectFrappeResponse(message) {
  // Frappe AJAX failures reject with response objects rather than Error instances.
  // eslint-disable-next-line prefer-promise-reject-errors
  return Promise.reject({ message });
}

async function run() {
  const tests = [
    testRegistrationAndScope,
    testBrowserAndModelEvents,
    testNewRowAndRejection,
    testDiscountSuppression,
    testPromptOnceAndErrors,
    testDuplicateReversionWaitsForDialog
  ];
  await tests.reduce((promise, test) => promise.then(test), Promise.resolve());
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
