/// Chat sessions and the orders each one produced.
///
/// Kept in memory. The server is the real record; this is what the screen draws.

import 'package:flutter/foundation.dart';

import 'models.dart';

/// Holds every chat session and the orders each one produced. Lives in memory:
/// closing the app clears it, which is fine for a demo and honest about what it
/// is (the backend keeps no order history either).
class SessionStore extends ChangeNotifier {
  final List<ChatSession> _sessions = [];
  ChatSession? _active;

  List<ChatSession> get sessions => List.unmodifiable(_sessions);
  ChatSession? get active => _active;

  List<PayOrder> get allOrders {
    final orders = <PayOrder>[];
    for (final session in _sessions) {
      orders.addAll(session.orders);
    }
    return orders;
  }

  ChatSession startSession({String title = 'New chat'}) {
    final session = ChatSession(
      id: 'c-${DateTime.now().millisecondsSinceEpoch}',
      title: title,
    );
    _sessions.insert(0, session);
    _active = session;
    notifyListeners();
    return session;
  }

  void ensureSession() {
    if (_active == null) startSession();
  }

  void addMessage(ChatMessage message) {
    ensureSession();
    final session = _active!;
    final isFirstUserLine = message.fromUser &&
        session.messages.where((m) => m.fromUser).isEmpty;
    if (isFirstUserLine) {
      session.title = message.text.length > 34
          ? '${message.text.substring(0, 34)}…'
          : message.text;
    }
    session.messages.add(message);
    notifyListeners();
  }

  void addOrder(PayOrder order) {
    ensureSession();
    _active!.orders.add(order);
    notifyListeners();
  }

  void select(ChatSession session) {
    _active = session;
    notifyListeners();
  }

  /// Last cards the user was shown, so the backend can resolve "book card 2".
  List<AgentCard> get lastCards {
    for (final message in _active?.messages.reversed ?? const <ChatMessage>[]) {
      if (!message.fromUser && message.cards.isNotEmpty) return message.cards;
    }
    return const [];
  }

  /// Replace the local list with what the database has, so history survives an
  /// app restart. Every session keeps its own messages and orders.
  void hydrate(List<Map<String, dynamic>> rows) {
    _sessions
      ..clear()
      ..addAll(rows.map((row) => ChatSession(
            id: '${row['id']}',
            title: '${row['title'] ?? 'New order'}',
            startedAt:
                DateTime.tryParse('${row['created_at']}') ?? DateTime.now(),
          )));
    _active = _sessions.isEmpty ? null : _sessions.first;
    notifyListeners();
  }

  /// Fill one session with its stored messages and orders.
  void fill(
    String sessionId,
    List<dynamic> messages,
    List<dynamic> orders,
  ) {
    ChatSession? session;
    for (final item in _sessions) {
      if (item.id == sessionId) session = item;
    }
    session ??= ChatSession(id: sessionId, title: 'New order');
    session.messages
      ..clear()
      ..addAll(messages.map((raw) {
        final row = raw as Map<String, dynamic>;
        final cards = ((row['cards'] as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AgentCard.fromJson)
            .toList();
        return ChatMessage(
          text: '${row['text'] ?? ''}',
          fromUser: '${row['role']}' == 'user',
          cards: cards,
          meta: row['meta'] as String?,
        );
      }));
    session.orders
      ..clear()
      ..addAll(orders.map((raw) {
        final row = raw as Map<String, dynamic>;
        return PayOrder(
          orderId: '${row['order_id'] ?? ''}',
          status: '${row['status'] ?? 'CREATED'}',
          mode: 'stored',
          amount: (row['amount'] as num?)?.toDouble(),
          currency: row['currency'] as String?,
          approveUrl: row['approve_url'] as String?,
        );
      }));
    _active = session;
    notifyListeners();
  }
}
