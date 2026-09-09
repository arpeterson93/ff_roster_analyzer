// Paste this into Extensions > Apps Script on the settings Google Sheet,
// then Deploy > New deployment > type "Web app" > Execute as "Me" >
// Who has access "Anyone" > Deploy. Copy the resulting Web App URL into
// docs/js/settingsConfig.js as SETTINGS_WEBAPP_URL.
//
// Expects a "Settings" tab with header row: league_slug | key | value

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Settings');
  var body = JSON.parse(e.postData.contents);
  var updates = body.updates || []; // [{league_slug, key, value}, ...]

  var data = sheet.getDataRange().getValues();
  var header = data[0];
  var slugCol = header.indexOf('league_slug');
  var keyCol = header.indexOf('key');
  var valueCol = header.indexOf('value');
  if (slugCol === -1 || keyCol === -1 || valueCol === -1) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'Settings tab missing league_slug/key/value columns' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  updates.forEach(function (u) {
    var found = false;
    for (var i = 1; i < data.length; i++) {
      if (data[i][slugCol] === u.league_slug && data[i][keyCol] === u.key) {
        sheet.getRange(i + 1, valueCol + 1).setValue(u.value);
        found = true;
        break;
      }
    }
    if (!found) {
      var newRow = new Array(header.length).fill('');
      newRow[slugCol] = u.league_slug;
      newRow[keyCol] = u.key;
      newRow[valueCol] = u.value;
      sheet.appendRow(newRow);
    }
  });

  return ContentService.createTextOutput(JSON.stringify({ ok: true, updated: updates.length }))
    .setMimeType(ContentService.MimeType.JSON);
}
