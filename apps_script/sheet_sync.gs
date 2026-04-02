/**
 * Companion Apps Script for the Python bot.
 *
 * Script Properties:
 * - SHEET_ID              -> target spreadsheet id
 * - SHEET_NAME            -> optional, defaults to ValueBets
 * - SUMMARY_SHEET_NAME    -> optional, defaults to RunSummary
 * - MATCHES_SHEET_NAME    -> optional, defaults to Matches
 * - RAW_JSON_URL          -> optional raw GitHub URL to .data/sheet-export.json
 * - WEBHOOK_TOKEN         -> optional shared secret for doPost
 */

function doPost(e) {
  var props = PropertiesService.getScriptProperties();
  var expectedToken = props.getProperty('WEBHOOK_TOKEN') || '';
  var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  if (expectedToken && body.token !== expectedToken) {
    return _json({ ok: false, error: 'invalid token' }, 401);
  }
  _writePayload(body);
  return _json({ ok: true, rows: (body.rows || []).length });
}

function syncSheetExportFromGithub() {
  var props = PropertiesService.getScriptProperties();
  var rawUrl = props.getProperty('RAW_JSON_URL') || '';
  if (!rawUrl) throw new Error('Missing RAW_JSON_URL in Script Properties');
  var response = UrlFetchApp.fetch(rawUrl, { muteHttpExceptions: true });
  if (response.getResponseCode() !== 200) {
    throw new Error('Failed to fetch sheet-export.json, HTTP ' + response.getResponseCode() + ': ' + response.getContentText().slice(0, 300));
  }
  var payload = JSON.parse(response.getContentText());
  _writePayload(payload);
  return true;
}

function _writePayload(payload) {
  var props = PropertiesService.getScriptProperties();
  var sheetId = props.getProperty('SHEET_ID') || '';
  var sheetName = props.getProperty('SHEET_NAME') || 'ValueBets';
  var summarySheetName = props.getProperty('SUMMARY_SHEET_NAME') || 'RunSummary';
  var matchesSheetName = props.getProperty('MATCHES_SHEET_NAME') || 'Matches';
  if (!sheetId) throw new Error('Missing SHEET_ID in Script Properties');

  var ss = SpreadsheetApp.openById(sheetId);
  _writeTable(ss, sheetName, payload.headers || [], payload.rows || []);
  _writeSummary(ss, summarySheetName, payload.summary || {});
  _writeTable(ss, matchesSheetName, payload.match_headers || [], payload.matches || []);
}

function _writeTable(ss, sheetName, headers, rows) {
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) sheet = ss.insertSheet(sheetName);
  sheet.clearContents();
  if (!headers.length) return;
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (!rows.length) return;
  var matrix = rows.map(function(row) {
    return headers.map(function(header) {
      var value = row[header];
      return value == null ? '' : value;
    });
  });
  sheet.getRange(2, 1, matrix.length, headers.length).setValues(matrix);
}

function _writeSummary(ss, sheetName, summary) {
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) sheet = ss.insertSheet(sheetName);
  sheet.clearContents();
  var rows = [['metric', 'value']];
  Object.keys(summary || {}).forEach(function(key) {
    var value = summary[key];
    if (value && typeof value === 'object') {
      value = JSON.stringify(value);
    }
    rows.push([key, value == null ? '' : value]);
  });
  sheet.getRange(1, 1, rows.length, 2).setValues(rows);
}

function create15MinTrigger() {
  ScriptApp.newTrigger('syncSheetExportFromGithub').timeBased().everyMinutes(15).create();
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
