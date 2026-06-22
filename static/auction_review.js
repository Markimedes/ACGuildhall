(function () {
  var form = document.getElementById('ah-list-form');
  if (!form) return;
  var PCT    = parseFloat(form.dataset.pct)     || 5;
  var RATE   = parseFloat(form.dataset.rate)    || 1;
  var MONEY  = parseInt(form.dataset.money, 10) || 0;
  var ONLINE = form.dataset.online === 'true';

  var hoursSel = document.getElementById('ah-hours');
  var rows     = document.querySelectorAll('.ah-list-row');
  var totalEl  = document.querySelector('.ah-total-deposit');
  var warn     = document.querySelector('.ah-deposit-warn');
  var submit   = document.getElementById('ah-list-submit');

  function fmt(c) {
    c = Math.max(0, Math.round(c));
    return Math.floor(c / 10000) + 'g ' + Math.floor((c % 10000) / 100) + 's ' + (c % 100) + 'c';
  }
  function clampInt(v, lo, hi) {
    v = parseInt(v, 10);
    if (isNaN(v)) v = lo;
    if (v < lo) v = lo;
    if (hi !== null && v > hi) v = hi;
    return v;
  }
  // Deposit for a single stack of `size`, mirroring the server's formula.
  function stackDeposit(sell, size, hours) {
    var minDep = Math.floor(100 * RATE);
    if (sell <= 0) return minDep;
    var dep = Math.floor((PCT * 3 / 100) * sell * size * (hours / 12) * RATE);
    return Math.max(dep, minDep);
  }
  // Read/write a row's g/s/c trio (class .ah-<prefix>-g/s/c) as a copper amount.
  function readPrice(row, prefix) {
    function v(suffix) {
      var el = row.querySelector('.ah-' + prefix + '-' + suffix);
      return el ? Math.max(0, parseInt(el.value, 10) || 0) : 0;
    }
    return v('g') * 10000 + v('s') * 100 + v('c');
  }
  function setPrice(row, prefix, copper) {
    copper = Math.max(0, Math.round(copper));
    var g = row.querySelector('.ah-' + prefix + '-g');
    var s = row.querySelector('.ah-' + prefix + '-s');
    var c = row.querySelector('.ah-' + prefix + '-c');
    if (g) g.value = Math.floor(copper / 10000);
    if (s) s.value = Math.floor((copper % 10000) / 100);
    if (c) c.value = copper % 100;
  }

  function recompute() {
    // Vendor-only submissions have no duration control / auction rows.
    var hours = (hoursSel && parseInt(hoursSel.value, 10)) || 12;
    var total = 0;
    rows.forEach(function (row) {
      var available = parseInt(row.dataset.available, 10) || 1;
      var maxStack  = parseInt(row.dataset.maxStack, 10)  || 1;
      var sizeInput = row.querySelector('.ah-stack-size');
      var numInput  = row.querySelector('.ah-num-stacks');

      var size = clampInt(sizeInput.value, 1, Math.min(maxStack, available));
      sizeInput.value = size;
      var maxNum = Math.max(1, Math.floor(available / size));
      numInput.max = maxNum;
      var num = clampInt(numInput.value, 1, maxNum);
      numInput.value = num;

      var depEach = stackDeposit(+row.dataset.sell, size, hours);
      var dep = depEach * num;
      total += dep;
      var depCell = row.querySelector('.ah-row-deposit');
      if (depCell) depCell.textContent = fmt(dep);

      var note = row.querySelector('.ah-stack-note');
      if (note) note.textContent = (size * num) + ' of ' + available;
    });
    if (totalEl) totalEl.textContent = fmt(total);
    var over = !ONLINE && total > MONEY;
    if (warn)   warn.hidden     = !over;
    if (submit) submit.disabled = over;
  }

  // Changing the stack size rescales the suggested per-stack price from the unit
  // value (like the in-game UI), then recomputes everything.
  rows.forEach(function (row) {
    var sizeInput = row.querySelector('.ah-stack-size');
    if (!sizeInput) return;
    sizeInput.addEventListener('input', function () {
      var unit = parseInt(row.dataset.unit, 10) || 0;
      var available = parseInt(row.dataset.available, 10) || 1;
      var maxStack  = parseInt(row.dataset.maxStack, 10)  || 1;
      var size = clampInt(sizeInput.value, 1, Math.min(maxStack, available));
      if (unit > 0) {
        setPrice(row, 'bid', unit * size);
        setPrice(row, 'buy', unit * size);
      }
    });
  });

  if (hoursSel) hoursSel.addEventListener('change', recompute);
  form.addEventListener('input', recompute);

  // On submit, serialize the form into one JSON object so auctioned and vendored
  // items stay logically separate. `hours` only matters when there are auctions.
  var payload = document.getElementById('ah-payload');
  form.addEventListener('submit', function () {
    var hours = (hoursSel && parseInt(hoursSel.value, 10)) || 0;
    var auctions = [];
    rows.forEach(function (row) {
      var available = parseInt(row.dataset.available, 10) || 1;
      var maxStack  = parseInt(row.dataset.maxStack, 10)  || 1;
      var size = clampInt(row.querySelector('.ah-stack-size').value,
                          1, Math.min(maxStack, available));
      var num  = clampInt(row.querySelector('.ah-num-stacks').value,
                          1, Math.max(1, Math.floor(available / size)));
      auctions.push({
        guids: row.dataset.guids || '',
        stack_size: size,
        num_stacks: num,
        bid: readPrice(row, 'bid'),
        buyout: readPrice(row, 'buy')
      });
    });
    var vendor = [];
    document.querySelectorAll('.ah-vendor-row').forEach(function (row) {
      if (row.dataset.guids) vendor.push(row.dataset.guids);
    });
    if (payload) {
      payload.value = JSON.stringify({ hours: hours, auctions: auctions,
                                       vendor: vendor });
    }
  });

  recompute();
})();
