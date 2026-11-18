/* static/credit_notes.js
 *
 * The line editor on a customer credit note: what is being credited, at what
 * rate, and with which tax. The arithmetic is shown as it is typed, and the
 * server works the same figures out again when the form is saved - the browser
 * is a convenience here, never the authority.
 */
(function () {
  var data = window.CREDIT_NOTE_DATA || { products: [], accounts: [], taxRates: [], units: [] };
  var rows = document.getElementById("cn-rows");

  function options(list, valueIndex, labelIndex, chosen) {
    return list.map(function (item) {
      var value = item[valueIndex];
      var label = item[labelIndex];
      var selected = String(value) === String(chosen) ? " selected" : "";
      return '<option value="' + value + '"' + selected + ">" + label + "</option>";
    }).join("");
  }

  function add(draft) {
    draft = draft || {};
    var tr = document.createElement("tr");
    tr.innerHTML =
      '<td><select name="product_id[]">' +
        '<option value="">— none —</option>' +
        options(data.products, 0, 1, draft.product_id) + "</select></td>" +
      '<td><input type="text" name="label[]" placeholder="What is being credited" value="' +
        (draft.label || "") + '"></td>' +
      '<td><select name="account_id[]">' + options(data.accounts, 0, 1, draft.account_id) + "</select></td>" +
      '<td><input type="number" step="0.001" min="0" name="quantity[]" value="' +
        (draft.quantity === undefined ? 1 : draft.quantity) + '" oninput="LPCredit.recalc()"></td>' +
      '<td><input type="text" name="unit[]" value="' + (draft.unit || "Units") + '"></td>' +
      '<td><input type="number" step="0.01" min="0" name="rate[]" value="' +
        (draft.rate || 0) + '" oninput="LPCredit.recalc()"></td>' +
      '<td><select name="sales_tax_rate_id[]" onchange="LPCredit.recalc()">' +
        '<option value="">No tax</option>' + options(data.taxRates, 0, 1, draft.tax_rate_id) + "</select></td>" +
      '<td class="amount" data-line-total>0.00</td>' +
      '<td><button type="button" class="lp-btn sm" onclick="LPCredit.remove(this)" title="Remove this line">×</button></td>';
    rows.appendChild(tr);

    // choosing a product fills the account and the rate it usually sells at
    var productBox = tr.querySelector('select[name="product_id[]"]');
    productBox.addEventListener("change", function () {
      var found = data.products.filter(function (p) { return String(p[0]) === productBox.value; })[0];
      if (!found) return;
      tr.querySelector('select[name="account_id[]"]').value = found[4] || tr.querySelector('select[name="account_id[]"]').value;
      var rateBox = tr.querySelector('input[name="rate[]"]');
      if (!Number(rateBox.value)) rateBox.value = Number(found[3] || 0).toFixed(2);
      tr.querySelector('input[name="unit[]"]').value = found[2] || "Units";
      recalc();
    });
    recalc();
  }

  function remove(button) {
    var tr = button.closest("tr");
    if (rows.children.length > 1) tr.remove();
    recalc();
  }

  function recalc() {
    var net = 0, tax = 0;
    var rates = {};
    data.taxRates.forEach(function (r) { rates[String(r[0])] = Number(r.length > 2 ? r[2] : 0); });

    Array.prototype.forEach.call(rows.children, function (tr) {
      var quantity = Number((tr.querySelector('input[name="quantity[]"]') || {}).value || 0);
      var rate = Number((tr.querySelector('input[name="rate[]"]') || {}).value || 0);
      var taxId = (tr.querySelector('select[name="sales_tax_rate_id[]"]') || {}).value || "";
      var lineNet = quantity * rate;
      var lineTax = taxId ? lineNet * (rates[taxId] || 0) / 100 : 0;
      net += lineNet;
      tax += lineTax;
      var box = tr.querySelector("[data-line-total]");
      if (box) box.textContent = (lineNet + lineTax).toFixed(2);
    });

    document.getElementById("cn-net").textContent = net.toFixed(2);
    document.getElementById("cn-tax").textContent = tax.toFixed(2);
    document.getElementById("cn-total").textContent = (net + tax).toFixed(2);
  }

  window.LPCredit = { add: add, remove: remove, recalc: recalc };
  if (rows) add();
})();
