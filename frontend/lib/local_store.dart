/// A small file for the login token and the device id.
///
/// Not a database. The history lives on the server; this just saves signing in
/// again after every launch.

import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// A tiny file so the login and the device id survive an app restart.
/// Deliberately not a database: the real history lives on the server.
class LocalStore {
  static Future<File> _file() async {
    final dir = await getApplicationDocumentsDirectory();
    return File('${dir.path}/paygrow_session.json');
  }

  static Future<Map<String, String>> read() async {
    try {
      final file = await _file();
      if (!file.existsSync()) return {};
      final body = jsonDecode(await file.readAsString());
      if (body is Map<String, dynamic>) {
        return body.map((key, value) => MapEntry(key, '$value'));
      }
    } catch (_) {}
    return {};
  }

  static Future<void> write(Map<String, String> values) async {
    try {
      final file = await _file();
      await file.writeAsString(jsonEncode(values));
    } catch (_) {}
  }

  static Future<void> clear() async {
    try {
      final file = await _file();
      if (file.existsSync()) await file.delete();
    } catch (_) {}
  }
}
