// Paste this into Extensions > Apps Script on the watch list Google Sheet,
// then Deploy > New deployment > type "Web app" > Execute as "Me" >
// Who has access "Anyone" > Deploy. Copy the resulting Web App URL into
// docs/js/watchlistConfig.js as WATCHLIST_WEBAPP_URL.
//
// Expects a "Watchlist" tab with header row: league_slug | team_id | player_id
// One row per watched player per team. Reading is unauthenticated (the site
// fetches this sheet's public CSV export directly) - this script only
// handles writes.

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Watchlist');
  var body = JSON.parse(e.postData.contents);
  var adds = body.adds || []; // [{league_slug, team_id, player_id}, ...]
  var removes = body.removes || []; // [{league_slug, team_id, player_id}, ...]

  var data = sheet.getDataRange().getValues();
  var header = data[0];
  var slugCol = header.indexOf('league_slug');
  var teamCol = header.indexOf('team_id');
  var playerCol = header.indexOf('player_id');
  if (slugCol === -1 || teamCol === -1 || playerCol === -1) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'Watchlist tab missing league_slug/team_id/player_id columns' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  function matches(row, w) {
    return String(row[slugCol]) === String(w.league_slug) && String(row[teamCol]) === String(w.team_id) && String(row[playerCol]) === String(w.player_id);
  }

  // Delete bottom-up so earlier row indices stay valid as rows are removed.
  removes.forEach(function (w) {
    for (var i = data.length - 1; i >= 1; i--) {
      if (matches(data[i], w)) {
        sheet.deleteRow(i + 1);
        data.splice(i, 1);
      }
    }
  });

  adds.forEach(function (w) {
    for (var i = 1; i < data.length; i++) {
      if (matches(data[i], w)) return; // already on the list
    }
    var newRow = new Array(header.length).fill('');
    newRow[slugCol] = w.league_slug;
    newRow[teamCol] = w.team_id;
    newRow[playerCol] = w.player_id;
    sheet.appendRow(newRow);
    data.push(newRow);
  });

  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
