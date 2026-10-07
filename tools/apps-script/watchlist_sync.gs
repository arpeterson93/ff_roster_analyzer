// Paste this into Extensions > Apps Script on the watch list Google Sheet,
// then Deploy > New deployment > type "Web app" > Execute as "Me" >
// Who has access "Anyone" > Deploy. Copy the resulting Web App URL into
// docs/js/watchlistConfig.js as WATCHLIST_WEBAPP_URL. Already deployed?
// Use Manage deployments > edit (pencil) > new version > Deploy instead of
// a fresh deployment, so the URL in watchlistConfig.js doesn't change.
//
// Expects a "Watchlist" tab with header row:
//   league_slug | team_id | player_id | list
// One row per (player, list) per team - the same player can appear twice,
// once per list ("watch" and "target" today - see docs/js/watchlist.js).
// `list` is optional for backward compatibility: a sheet that hasn't added
// the column yet (or a legacy row with a blank cell there) is treated as
// "watch" throughout, so this script works whether or not you've added the
// column, and whether you add it before or after redeploying this script.
// Reading is unauthenticated (the site fetches this sheet's public CSV
// export directly) - this script only handles writes.

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Watchlist');
  var body = JSON.parse(e.postData.contents);
  var adds = body.adds || []; // [{league_slug, team_id, player_id, list}, ...]
  var removes = body.removes || []; // [{league_slug, team_id, player_id, list}, ...]

  var data = sheet.getDataRange().getValues();
  var header = data[0];
  var slugCol = header.indexOf('league_slug');
  var teamCol = header.indexOf('team_id');
  var playerCol = header.indexOf('player_id');
  var listCol = header.indexOf('list'); // -1 is fine - see this file's own header comment
  if (slugCol === -1 || teamCol === -1 || playerCol === -1) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'Watchlist tab missing league_slug/team_id/player_id columns' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  function rowList(row) {
    if (listCol === -1) return 'watch';
    return row[listCol] ? String(row[listCol]) : 'watch';
  }
  function entryList(w) {
    return w.list || 'watch';
  }
  function matches(row, w) {
    return String(row[slugCol]) === String(w.league_slug) && String(row[teamCol]) === String(w.team_id) && String(row[playerCol]) === String(w.player_id) && rowList(row) === entryList(w);
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
      if (matches(data[i], w)) return; // already on this list
    }
    var newRow = new Array(header.length).fill('');
    newRow[slugCol] = w.league_slug;
    newRow[teamCol] = w.team_id;
    newRow[playerCol] = w.player_id;
    if (listCol !== -1) newRow[listCol] = entryList(w);
    sheet.appendRow(newRow);
    data.push(newRow);
  });

  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
