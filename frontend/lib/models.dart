/// Everything the backend sends, typed.
class AgentCard {
  const AgentCard({
    required this.cardNo,
    required this.productId,
    required this.title,
    required this.price,
    required this.currency,
    required this.quantity,
    this.image,
    this.availability = 'in_stock',
    this.priceSource = 'simulated',
  });

  final int cardNo;
  final String productId;
  final String title;
  final String? image;
  final double price;
  final String currency;
  final double quantity;
  final String availability;
  final String priceSource;

  factory AgentCard.fromJson(Map<String, dynamic> json) => AgentCard(
        cardNo: (json['card_no'] as num?)?.toInt() ?? 0,
        productId: '${json['product_id'] ?? ''}',
        title: '${json['title'] ?? ''}',
        image: json['image'] as String?,
        price: (json['price'] as num?)?.toDouble() ?? 0,
        currency: '${json['currency'] ?? 'USD'}',
        quantity: (json['quantity'] as num?)?.toDouble() ?? 1,
        availability: '${json['availability'] ?? 'in_stock'}',
        priceSource: '${json['price_source'] ?? 'simulated'}',
      );

  Map<String, dynamic> toJson() => {
        'card_no': cardNo,
        'product_id': productId,
        'title': title,
        'image': image,
        'price': price,
        'currency': currency,
        'quantity': quantity,
        'availability': availability,
        'price_source': priceSource,
      };

  String get money =>
      (currency == 'INR' ? '₹' : '\$') + price.toStringAsFixed(2);
}

class AudioChunk {
  const AudioChunk({required this.index, required this.text, this.audio, this.error});

  final int index;
  final String text;
  final String? audio;
  final String? error;

  factory AudioChunk.fromJson(Map<String, dynamic> json) => AudioChunk(
        index: (json['index'] as num?)?.toInt() ?? 0,
        text: '${json['text'] ?? ''}',
        audio: json['audio'] as String?,
        error: json['error'] as String?,
      );
}

class PayOrder {
  const PayOrder({
    required this.orderId,
    required this.status,
    required this.mode,
    this.approveUrl,
    this.amount,
    this.currency,
  });

  final String orderId;
  final String status;
  final String mode;
  final String? approveUrl;
  final double? amount;
  final String? currency;

  factory PayOrder.fromJson(Map<String, dynamic> json) => PayOrder(
        orderId: '${json['order_id'] ?? ''}',
        status: '${json['status'] ?? ''}',
        mode: '${json['mode'] ?? ''}',
        approveUrl: json['approve_url'] as String?,
        amount: (json['amount'] as num?)?.toDouble(),
        currency: json['currency'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'order_id': orderId,
        'status': status,
        'mode': mode,
        'approve_url': approveUrl,
        'amount': amount,
        'currency': currency,
      };

  bool get isCaptured => status.toUpperCase() == 'COMPLETED';
}

class AgentResult {
  const AgentResult({
    required this.transcript,
    required this.intent,
    required this.narration,
    required this.cards,
    required this.audio,
    required this.language,
    this.order,
    this.llmEngine = 'rules',
    this.searchMode = 'mock',
    this.llmNote,
  });

  final String transcript;
  final String intent;
  final String narration;
  final List<AgentCard> cards;
  final List<AudioChunk> audio;
  final String language;
  final PayOrder? order;
  final String llmEngine;
  final String searchMode;
  final String? llmNote;

  factory AgentResult.fromJson(Map<String, dynamic> json) => AgentResult(
        transcript: '${json['transcript'] ?? ''}',
        intent: '${json['intent'] ?? 'search'}',
        narration: '${json['narration'] ?? ''}',
        cards: ((json['cards'] as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AgentCard.fromJson)
            .toList(),
        audio: ((json['audio'] as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AudioChunk.fromJson)
            .toList(),
        language: '${json['language'] ?? 'hi-IN'}',
        order: json['order'] is Map<String, dynamic>
            ? PayOrder.fromJson(json['order'] as Map<String, dynamic>)
            : null,
        llmEngine: '${json['llm_engine'] ?? 'rules'}',
        searchMode: '${json['search_mode'] ?? 'mock'}',
        llmNote: json['llm_note'] as String?,
      );
}

/// One line in the chat transcript.
class ChatMessage {
  ChatMessage({
    required this.text,
    required this.fromUser,
    this.cards = const [],
    this.audio = const [],
    this.order,
    this.meta,
    DateTime? at,
  }) : at = at ?? DateTime.now();

  final String text;
  final bool fromUser;
  final List<AgentCard> cards;
  final List<AudioChunk> audio;
  final PayOrder? order;
  final String? meta;
  final DateTime at;
}

/// A whole conversation. Orders hang off the session that produced them.
class ChatSession {
  ChatSession({required this.id, required this.title, DateTime? startedAt})
      : startedAt = startedAt ?? DateTime.now();

  final String id;
  String title;
  final DateTime startedAt;
  final List<ChatMessage> messages = [];
  final List<PayOrder> orders = [];

  String get timeLabel {
    final h = startedAt.hour.toString().padLeft(2, '0');
    final m = startedAt.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }
}
