(function () {
  var form = document.getElementById('ah-form');
  if (!form) return;
  var btn = document.getElementById('ah-sell-btn');
  var label = document.querySelector('.ah-sel-count');
  var junkBtn = document.getElementById('ah-junk-btn');
  // "Junk" = the low-value, tradeable items only worth their vendor price
  // (data-junk on the checkbox; soulbound items are excluded server-side).
  var junkBoxes = form.querySelectorAll('input[name="row"][data-junk="1"]');

  function allJunkChecked() {
    return junkBoxes.length > 0 &&
      Array.prototype.every.call(junkBoxes, function (b) { return b.checked; });
  }

  function update() {
    var n = form.querySelectorAll('input[name="row"]:checked').length;
    btn.disabled = n === 0;
    label.textContent = n === 0 ? 'No items selected'
      : n + ' item' + (n === 1 ? '' : 's') + ' selected';
    if (junkBtn && junkBoxes.length) {
      junkBtn.textContent = allJunkChecked() ? 'Deselect junk' : 'Select junk';
    }
  }

  // "Select junk" toggles every junk checkbox on (or off if all are already on).
  if (junkBtn && junkBoxes.length) {
    junkBtn.hidden = false;
    junkBtn.addEventListener('click', function () {
      var check = !allJunkChecked();
      Array.prototype.forEach.call(junkBoxes, function (b) { b.checked = check; });
      update();
    });
  }

  form.addEventListener('change', update);
  update();
})();
