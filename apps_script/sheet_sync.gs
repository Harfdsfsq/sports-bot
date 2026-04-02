/**
 * Minimal companion Apps Script for the Python bot.
 *
 * Script Properties required:
 * - SHEET_ID       -> target spreadsheet id
 * - SHEET_NAME     -> optional, defaults to ValueBets
 * - RAW_JSON_URL   -> raw GitHub URL to .data/sheet-export.json
 *
 * Example RAW_JSON_URL:
 * https://raw.githubusercontent.com/Harfdsfsq/sports-bot/main/.data/sheet-export.json
 */
function syncSheetExportFromGithub() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = props.getProperty('SHEET_ID') || '';
  var sheetName = props.getProperty('SHEET_NAME') || 'ValueBets';
  var rawUrl = props.getProperty('RAW_JSON_URL') || '';

  if (!sheetId) throw new Error('Missing SHEET_ID in Script Properties');
  if (!rawUrl) throw new Error('Missing RAW_JSON_URL in Script Properties');

  var response = UrlFetchApp.fetch(rawUrl, { muteHttpExceptions: true });
  if (response.getResponseCode() !== 200) {
    throw new Error('Failed to fetch sheet-export.json, HTTP ' + response.getResponseCode() + ': ' + response.getContentText().slice(0, 300));
  }

  var payload = JSON.parse(response.getContentText());
  var headers = payload.headers || [];
  var rows = payload.rows || [];

  var ss = SpreadsheetApp.openById(sheetId);
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) sheet = ss.insertSheet(sheetName);

  sheet.clearContents();
  if (!headers.length) return true;

  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (!rows.length) return true;

  var matrix = rows.map(function(row) {
    return headers.map(function(header) {
      var value = row[header];
      return value == null ? '' : value;
    });
  });

  sheet.getRange(2, 1, matrix.length, headers.length).setValues(matrix);
  return true;
}

function create15MinTrigger() {
  ScriptApp.newTrigger('syncSheetExportFromGithub').timeBased().everyMinutes(15).create();
}
