/// The one place that talks to the backend.
///
/// One request per turn. Nothing streams: the narration arrives as finished audio
/// chunks and the voice window plays them in order.

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;

  @override
  String toString() => message;
}

/// Thin client over the PayGrow backend. Nothing is streamed: one request per
/// turn, and the narration comes back as ready-made audio chunks that the voice
/// window plays in order.
class PayGrowApi {
  PayGrowApi({String? baseUrl})
      : baseUrl = baseUrl ??
            const String.fromEnvironment(
              'API_BASE',
              // Your backend goes here. Nothing is baked in on purpose: a fork
              // should never end up calling somebody else's deployment, and
              // their PayPal keys with it.
              //
              // Either pass it at build time:
              //   flutter run --dart-define=API_BASE=https://your-backend.example.com
              // or change the default below and keep it to yourself.
              defaultValue: 'https://your-backend.example.com',
            );

  final String baseUrl;

  /// Shared by every screen: one token, one device id.
  static final PayGrowApi instance = PayGrowApi();

  String? token;
  String deviceId = 'device';

  Map<String, String> _headers() => {
        if (token != null) 'Authorization': 'Bearer $token',
        'X-Device-Id': deviceId,
      };

  /// The backend remembers the last cards per session, so "book card 2" works
  /// even if the app forgets to send them.
  String sessionId = 'app-${DateTime.now().millisecondsSinceEpoch}';

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Map<String, dynamic> _decode(http.Response res) {
    Map<String, dynamic> body = <String, dynamic>{};
    try {
      final decoded = jsonDecode(res.body);
      if (decoded is Map<String, dynamic>) body = decoded;
    } catch (_) {
      // fall through to the status check below
    }
    if (res.statusCode >= 400) {
      throw ApiException(
        '${body['detail'] ?? 'Request failed with HTTP ${res.statusCode}'}',
      );
    }
    return body;
  }

  Future<Map<String, dynamic>> health() async {
    final res = await http
        .get(_uri('/health'))
        .timeout(const Duration(seconds: 45));
    return _decode(res);
  }

  Future<AgentResult> understand(
    String text, {
    String language = 'auto',
    List<AgentCard> cards = const [],
  }) async {
    final payload = <String, dynamic>{
      'text': text,
      'language': language,
      'session_id': sessionId,
      if (cards.isNotEmpty)
        'cards': cards.map((card) => card.toJson()).toList(),
    };
    final res = await http
        .post(
          _uri('/api/agent/understand'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode(payload),
        )
        .timeout(const Duration(seconds: 90));
    return AgentResult.fromJson(_decode(res));
  }

  Future<AgentResult> voice(
    File file, {
    String language = 'auto',
    List<AgentCard> cards = const [],
  }) async {
    final request = http.MultipartRequest('POST', _uri('/api/agent/voice'))
      ..fields['language'] = language
      ..fields['session_id'] = sessionId;

    if (cards.isNotEmpty) {
      request.fields['cards'] =
          jsonEncode(cards.map((card) => card.toJson()).toList());
    }
    request.files.add(
      await http.MultipartFile.fromPath('file', file.path, filename: 'voice.wav'),
    );

    final streamed = await request.send().timeout(const Duration(seconds: 150));
    final res = await http.Response.fromStream(streamed);
    return AgentResult.fromJson(_decode(res));
  }

  Future<PayOrder> createOrder(List<AgentCard> cards) async {
    final lines = cards
        .map((card) => {
              'product_id': card.productId,
              'title': card.title,
              'image': card.image,
              'unit_price': card.price,
              'quantity': card.quantity,
            })
        .toList();
    final res = await http
        .post(
          _uri('/api/checkout/paypal/order'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'lines': lines}),
        )
        .timeout(const Duration(seconds: 60));
    return PayOrder.fromJson(_decode(res));
  }

  Future<PayOrder> capture(String orderId) async {
    final res = await http
        .post(
          _uri('/api/checkout/paypal/capture'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'order_id': orderId}),
        )
        .timeout(const Duration(seconds: 60));
    return PayOrder.fromJson(_decode(res));
  }

  // ── sessions ─────────────────────────────────────────────────────────────
  Future<List<Map<String, dynamic>>> listSessions() async {
    final res = await http
        .get(_uri('/api/sessions'), headers: _headers())
        .timeout(const Duration(seconds: 45));
    if (res.statusCode >= 400) throw ApiException('Could not load history.');
    final body = jsonDecode(res.body);
    return (body as List).cast<Map<String, dynamic>>();
  }

  Future<String> createSession() async {
    final res = await http
        .post(_uri('/api/sessions'), headers: _headers())
        .timeout(const Duration(seconds: 45));
    final body = _decode(res);
    sessionId = '${body['id']}';
    return sessionId;
  }

  Future<Map<String, dynamic>> loadSession(String id) async {
    final res = await http
        .get(_uri('/api/sessions/$id'), headers: _headers())
        .timeout(const Duration(seconds: 45));
    return _decode(res);
  }

  /// Transcribe one 25 second chunk. Text only - no search, no order.
  Future<String> transcribe(File chunk, {String language = 'auto'}) async {
    final request = http.MultipartRequest('POST', _uri('/api/stt'))
      ..fields['language'] = language;
    request.files.add(
      await http.MultipartFile.fromPath('file', chunk.path, filename: 'chunk.wav'),
    );
    final streamed = await request.send().timeout(const Duration(seconds: 60));
    final res = await http.Response.fromStream(streamed);
    final body = _decode(res);
    return '${body['transcript'] ?? ''}'.trim();
  }

  // ── login ────────────────────────────────────────────────────────────────
  /// Ask for a one-time code. When no mail provider is configured the backend
  /// hands the code back so the app can be tried before Brevo is wired up.
  Future<String?> requestCode(String email) async {
    final res = await http
        .post(
          _uri('/api/auth/request-code'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': email}),
        )
        .timeout(const Duration(seconds: 45));
    final body = _decode(res);
    return body['dev_code'] as String?;
  }

  Future<Map<String, dynamic>> verifyCode(String email, String code) async {
    final res = await http
        .post(
          _uri('/api/auth/verify'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': email, 'code': code}),
        )
        .timeout(const Duration(seconds: 45));
    final body = _decode(res);
    token = body['token'] as String?;
    return body;
  }

  Future<Map<String, dynamic>?> me() async {
    if (token == null) return null;
    try {
      final res = await http
          .get(_uri('/api/auth/me'), headers: _headers())
          .timeout(const Duration(seconds: 30));
      if (res.statusCode >= 400) return null;
      return jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }
}
