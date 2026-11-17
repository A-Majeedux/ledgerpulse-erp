/* ---- The lines of a purchase bill ---------------------------------------
   One editor for both bill screens (standard and import). The page hands it
   what it needs in `window.BILL_LINE_DATA`:

     accounts, products, taxRates, units, kinds, chargeTypes, draftLines

   The arithmetic here only helps the person typing: the server works every
   figure out again from quantity, rate, discount and tax, so a browser that
   lies - or has JavaScript switched off - cannot move a rupee in the books.

   quantity x rate = gross; the discount comes off that; the tax is charged on
   what is left; total is what the vendor's paper says, tax included.
   ------------------------------------------------------------------------ */
(function () {
  var DATA = window.BILL_LINE_DATA || {};
  var ACCOUNTS = DATA.accounts || [];
  var PRODUCTS = DATA.products || [];
  var TAX_RATES = DATA.taxRates || [];
  var UNITS = DATA.units || ['Units'];
  var KINDS = DATA.kinds || ['Goods'];
  var CHARGE_TYPES = DATA.chargeTypes || [];
  var DRAFT_LINES = DATA.draftLines || [];

  function money(value) {
    return (Math.round((parseFloat(value) || 0) * 100) / 100).toFixed(2);
  }

  function options(list, selected, blank) {
    var html = blank === undefined ? '' : '<option value="">' + blank + '</option>';
    list.forEach(function (item) {
      var value = item.value !== undefined ? item.value : item;
      var label = item.label !== undefined ? item.label : item;
      html += '<option value="' + value + '"' +
        (String(value) === String(selected) ? ' selected' : '') + '>' + label + '</option>';
    });
    return html;
  }

  function accountOptions(selected) {
    return options(ACCOUNTS.map(function (a) {
      return { value: a.id, label: a.label };
    }), selected, 'Select account…');
  }

  function productOptions(selected) {
    return options(PRODUCTS.map(function (p) {
      return { value: p.id, label: p.label };
    }), selected, '—');
  }

  function unitOptions(selected) {
    return options(UNITS, selected || 'Units');
  }

  function taxOptions(selected) {
    if (!TAX_RATES.length) {
      return '<option value="">no rates set up</option>';
    }
    return options(TAX_RATES.map(function (t) {
      return { value: t.id, label: t.label + ' (' + t.percent + '%)' };
    }), selected, 'No tax');
  }

  function kindOptions(selected) {
    return options(KINDS, selected || 'Goods');
  }

  function addLine(line) {
    line = line || {};
    var tbody = document.getElementById('lines-body');
    var row = document.createElement('tr');
    row.innerHTML =
      '<td><input type="text" name="label[]" class="form-control" placeholder="Description"></td>' +
      '<td><select name="product_id[]" class="form-select">' + productOptions(line.product_id) + '</select></td>' +
      '<td><select name="account_id[]" class="form-select" required>' + accountOptions(line.account_id) + '</select></td>' +
      '<td><select name="line_kind[]" class="form-select line-kind">' + kindOptions(line.line_kind) + '</select></td>' +
      '<td><select name="unit[]" class="form-select">' + unitOptions(line.unit) + '</select></td>' +
      '<td><input type="number" step="0.01" name="quantity[]" value="1" class="form-control num" oninput="LPBillLines.recalc(this)"></td>' +
      '<td><input type="number" step="0.01" name="rate[]" value="0" class="form-control num" oninput="LPBillLines.recalc(this)"></td>' +
      '<td><input type="number" step="0.01" min="0" max="100" name="discount_percent[]" value="0" class="form-control num" oninput="LPBillLines.recalc(this)"></td>' +
      '<td><select name="tax_rate_id[]" class="form-select" onchange="LPBillLines.recalc(this)">' + taxOptions(line.tax_rate_id) + '</select></td>' +
      '<td class="amount line-total" style="font-weight:700;">0.00</td>' +
      '<td><input type="number" step="0.5" min="0" name="asset_life[]" class="form-control num asset-life" title="Life of the asset in years"></td>' +
      '<td class="col-actions"><button type="button" class="lp-btn ghost sm" title="Remove line" aria-label="Remove line" onclick="LPBillLines.remove(this)">' +
        '<svg class="ico sm" aria-hidden="true"><use href="#i-trash"/></svg></button></td>';

    // Hidden totals are still posted: an older screen (or a person) may send a
    // line with no rate at all, and the server falls back to this figure.
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = 'total[]';
    row.appendChild(hidden);

    tbody.appendChild(row);

    if (line.label) row.querySelector('[name="label[]"]').value = line.label;
    if (line.quantity !== undefined && line.quantity !== null) row.querySelector('[name="quantity[]"]').value = line.quantity;
    if (line.rate !== undefined && line.rate !== null) row.querySelector('[name="rate[]"]').value = line.rate;
    if (line.discount_percent !== undefined && line.discount_percent !== null) {
      row.querySelector('[name="discount_percent[]"]').value = line.discount_percent;
    }
    if (line.asset_life) row.querySelector('[name="asset_life[]"]').value = line.asset_life;

    row.querySelector('[name="line_kind[]"]').addEventListener('change', function () {
      LPBillLines.markKind(row);
    });
    row.querySelector('[name="product_id[]"]').addEventListener('change', function () {
      LPBillLines.pickUnit(row);
    });

    markKind(row);
    recalc(row);
    return row;
  }

  // The kind changes what the row means: a landed-cost line has no product, and
  // an asset line is bought to be kept, so it wants its life in years.
  function markKind(row) {
    var kind = row.querySelector('[name="line_kind[]"]').value;
    var product = row.querySelector('[name="product_id[]"]');
    var life = row.querySelector('[name="asset_life[]"]');
    var isCharge = kind === 'Landed Cost';
    var isAsset = kind === 'Asset';

    product.style.opacity = isCharge ? '.45' : '';
    product.style.pointerEvents = isCharge ? 'none' : '';
    if (isCharge) product.value = '';
    row.style.background = isCharge ? '#FFFBF3' : (isAsset ? '#F3F8FF' : '');

    life.style.opacity = isAsset ? '' : '.35';
    life.style.pointerEvents = isAsset ? '' : 'none';
    if (!isAsset) life.value = '';

    var label = row.querySelector('[name="label[]"]');
    if (label && !label.value) {
      label.placeholder = isCharge
        ? 'e.g. Freight, customs duty, clearing'
        : (isAsset ? 'e.g. Delivery van, packing machine' : 'Description');
    }
  }

  // A product knows what it is measured in; picking one fills the unit in.
  function pickUnit(row) {
    var chosen = row.querySelector('[name="product_id[]"]').value;
    var unitBox = row.querySelector('[name="unit[]"]');
    var product = PRODUCTS.find(function (p) { return String(p.id) === String(chosen); });
    if (product && product.unit && unitBox) unitBox.value = product.unit;
  }

  function recalc(source) {
    var row = source.closest ? source.closest('tr') : source;
    var qty = parseFloat((row.querySelector('[name="quantity[]"]') || {}).value || 0);
    var rate = parseFloat((row.querySelector('[name="rate[]"]') || {}).value || 0);
    var discount = parseFloat((row.querySelector('[name="discount_percent[]"]') || {}).value || 0);
    var taxId = (row.querySelector('[name="tax_rate_id[]"]') || {}).value;

    var gross = qty * rate;
    var discountAmount = gross * Math.min(Math.max(discount, 0), 100) / 100;
    var net = gross - discountAmount;
    var taxRate = TAX_RATES.find(function (t) { return String(t.id) === String(taxId); });
    var taxAmount = taxRate ? net * (parseFloat(taxRate.percent) || 0) / 100 : 0;

    var cell = row.querySelector('.line-total');
    if (cell) cell.textContent = money(net + taxAmount);
    var hidden = row.querySelector('[name="total[]"]');
    if (hidden) hidden.value = money(net + taxAmount);

    recalcTotals();
  }

  function recalcTotals() {
    var net = 0, tax = 0;
    document.querySelectorAll('#lines-body tr').forEach(function (row) {
      var qty = parseFloat((row.querySelector('[name="quantity[]"]') || {}).value || 0);
      var rate = parseFloat((row.querySelector('[name="rate[]"]') || {}).value || 0);
      var discount = parseFloat((row.querySelector('[name="discount_percent[]"]') || {}).value || 0);
      var taxId = (row.querySelector('[name="tax_rate_id[]"]') || {}).value;
      var gross = qty * rate;
      var lineNet = gross - gross * Math.min(Math.max(discount, 0), 100) / 100;
      var taxRate = TAX_RATES.find(function (t) { return String(t.id) === String(taxId); });
      net += lineNet;
      tax += taxRate ? lineNet * (parseFloat(taxRate.percent) || 0) / 100 : 0;
    });

    var box = document.getElementById('grand-total');
    if (box) box.textContent = money(net + tax);
    var untaxed = document.getElementById('untaxed-total');
    if (untaxed) untaxed.textContent = money(net);
    var taxBox = document.getElementById('tax-total');
    if (taxBox) taxBox.textContent = money(tax);
  }

  function remove(button) {
    var row = button.closest('tr');
    row.parentNode.removeChild(row);
    recalcTotals();
  }

  window.LPBillLines = {
    add: addLine, recalc: recalc, remove: remove, markKind: markKind,
    pickUnit: pickUnit, totals: recalcTotals
  };

  if (DRAFT_LINES.length) {
    DRAFT_LINES.forEach(addLine);
  } else {
    addLine();
  }
  recalcTotals();
})();
