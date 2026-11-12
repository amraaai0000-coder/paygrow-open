/// The chat screen: header, conversation, composer and the orders drawer.

import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api.dart';
import '../local_store.dart';
import '../models.dart';
import '../narration.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets/card_tile.dart';
import '../widgets/menu_button.dart';
import '../widgets/recording_bar.dart';
import '../widgets/shimmer_stage.dart';
import 'login_screen.dart';
import 'voice_window.dart';

enum ComposerMode { idle, recording, voice }

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const List<String> _stages = <String>[
    'Understanding your order',
    'Picking the products',
    'Sending the list to the catalogue',
    'Pricing your basket',
    'Writing the answer',
  ];

  final GlobalKey<ScaffoldState> _scaffold = GlobalKey<ScaffoldState>();
  final PayGrowApi _api = PayGrowApi.instance;
  final SessionStore _store = SessionStore();
  final NarrationPlayer _narration = NarrationPlayer();
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();
  final AudioRecorder _recorder = AudioRecorder();

  ComposerMode _mode = ComposerMode.idle;
  bool _busy = false;
  String? _stage;
  Duration _elapsed = Duration.zero;
  Timer? _tick;
  Timer? _stageTimer;
  Timer? _chunkTimer;
  final StringBuffer _dictation = StringBuffer();
  int _stageIndex = 0;

  String? _accountEmail;
  String _historyState = 'checking';
  String? _liveCaption;
  int? _revealingIndex;

  @override
  void initState() {
    super.initState();
    _loadHistory();
    _loadAccount();
  }

  /// Every session keeps its own history; this brings the old ones back.
  Future<void> _loadHistory() async {
    _readDatabaseMode();
    try {
      final rows = await _api.listSessions();
      _store.hydrate(rows);
      if (_store.sessions.isEmpty) {
        await _api.createSession();
        await _loadHistory();
        return;
      }
      await _openSession(_store.sessions.first.id);
    } catch (_) {
      _store.ensureSession();
    }
  }

  Future<void> _loadAccount() async {
    final saved = await LocalStore.read();
    if (!mounted) return;
    setState(() => _accountEmail = saved['email']);
  }

  /// /health says whether the backend has a real database or is only keeping
  /// sessions in memory - worth showing, because in memory mode old chats are
  /// gone after every restart.
  Future<void> _readDatabaseMode() async {
    try {
      final body = await _api.health();
      if (!mounted) return;
      setState(() {
        _historyState = body['database'] == 'postgres' ? 'saved' : 'temporary';
      });
    } catch (_) {
      if (mounted) setState(() => _historyState = 'unknown');
    }
  }

  Future<void> _openSession(String id) async {
    // The backend owns session ids (they start with "pg-"). A local one means
    // the list is stale, so asking the server for it would only 404.
    if (!id.startsWith('pg-')) return;
    try {
      final detail = await _api.loadSession(id);
      _store.fill(
        id,
        (detail['messages'] as List?) ?? const [],
        (detail['orders'] as List?) ?? const [],
      );
      _api.sessionId = id;
      _scrollToEnd();
    } catch (e) {
      _snack('Could not load that chat: $e');
    }
  }

  Future<void> _signOut() async {
    await LocalStore.clear();
    PayGrowApi.instance.token = null;
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (_) => false,
    );
  }

  Future<void> _newOrder() async {
    try {
      await _api.createSession();
      await _loadHistory();
    } catch (_) {
      _snack('Could not start a new order.');
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    _stageTimer?.cancel();
    _narration.dispose();
    _recorder.dispose();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }


  // ── one turn, whichever way it started ─────────────────────────────────────
  /// A turn must belong to a session the backend knows about.
  Future<void> _ensureSession() async {
    if (_api.sessionId.startsWith('pg-')) return;
    try {
      await _api.createSession();
      await _loadHistory();
    } catch (_) {
      // the turn will still run; the backend keeps it in memory
    }
  }

  Future<void> _runTurn(
    Future<AgentResult> Function() call, {
    bool speak = false,
  }) async {
    if (_busy) return;
    await _ensureSession();
    setState(() {
      _busy = true;
      _stageIndex = 0;
      _stage = _stages.first;
    });
    _stageTimer?.cancel();
    _stageTimer = Timer.periodic(const Duration(milliseconds: 1500), (_) {
      if (!mounted || !_busy) return;
      setState(() {
        _stageIndex = (_stageIndex + 1) % _stages.length;
        _stage = _stages[_stageIndex];
      });
    });

    try {
      final result = await call();
      if (!mounted) return;

      _store.addMessage(ChatMessage(
        text: result.narration,
        fromUser: false,
        cards: result.cards,
        audio: result.audio,
        order: result.order,
        meta: '${result.intent} · ${result.llmEngine}',
      ));
      if (result.order != null) {
        _store.addOrder(result.order!);
        await _openPayPal(result.order!);
      }

      _scrollToEnd();

      // Text to speech belongs to the automated voice window only. A typed or
      // dictation turn is silent - the answer is read, not played.
      if (speak) {
        final index = (_store.active?.messages.length ?? 1) - 1;
        setState(() {
          _revealingIndex = index;
          _liveCaption = '';
        });
        await _narration.play(
          result.audio,
          onText: (text) {
            if (mounted) setState(() => _liveCaption = text);
          },
        );
      }
    } on ApiException catch (e) {
      _store.addMessage(ChatMessage(
        text: e.message,
        fromUser: false,
        meta: 'error',
      ));
    } catch (e) {
      _store.addMessage(ChatMessage(
        text: 'Network problem: $e',
        fromUser: false,
        meta: 'error',
      ));
    } finally {
      _stageTimer?.cancel();
      if (mounted) {
        setState(() {
          _busy = false;
          _stage = null;
          _revealingIndex = null;
          _liveCaption = null;
        });
        _scrollToEnd();
      }
    }
  }

  Future<void> _sendTyped() async {
    final text = _input.text.trim();
    if (text.isEmpty || _busy) return;
    _input.clear();
    _store.addMessage(ChatMessage(text: text, fromUser: true));
    _scrollToEnd();
    await _runTurn(() => _api.understand(text, cards: _store.lastCards));
  }

  // ── inline recorder (mic button) ───────────────────────────────────────────
  Future<void> _startInlineRecording() async {
    if (_busy) return;
    try {
      if (!await _recorder.hasPermission()) {
        _snack('Microphone permission was denied.');
        return;
      }
      _dictation.clear();
      await _beginChunk();
      _elapsed = Duration.zero;
      _tick?.cancel();
      _tick = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() => _elapsed += const Duration(seconds: 1));
      });
      // Sarvam takes at most 30 s per request, so the recording is cut into
      // 25 s chunks and each one is transcribed as it closes.
      _chunkTimer?.cancel();
      _chunkTimer = Timer.periodic(
        const Duration(seconds: 25),
        (_) => _flushChunk(restart: true),
      );
      setState(() => _mode = ComposerMode.recording);
    } catch (e) {
      _snack('Could not start the microphone: $e');
    }
  }

  Future<void> _beginChunk() async {
    final dir = await getTemporaryDirectory();
    final path =
        '${dir.path}/pg_chunk_${DateTime.now().millisecondsSinceEpoch}.wav';
    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.wav,
        sampleRate: 16000,
        numChannels: 1,
      ),
      path: path,
    );
  }

  /// Close the current chunk, transcribe it, and optionally start the next.
  Future<void> _flushChunk({required bool restart}) async {
    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {}
    if (path != null) {
      try {
        final text = await _api.transcribe(File(path));
        if (text.isNotEmpty && mounted) {
          setState(() {
            _dictation.write(_dictation.isEmpty ? text : ' $text');
          });
        }
      } catch (_) {
        // a bad chunk should not kill the recording
      }
    }
    if (restart && mounted && _mode == ComposerMode.recording) {
      await _beginChunk();
    }
  }

  Future<void> _cancelInlineRecording() async {
    _tick?.cancel();
    _chunkTimer?.cancel();
    try {
      await _recorder.stop();
    } catch (_) {}
    _dictation.clear();
    if (mounted) {
      setState(() {
        _mode = ComposerMode.idle;
        _elapsed = Duration.zero;
      });
    }
  }

  Future<void> _sendInlineRecording() async {
    _tick?.cancel();
    _chunkTimer?.cancel();
    await _flushChunk(restart: false);
    if (mounted) {
      setState(() {
        _mode = ComposerMode.idle;
        _elapsed = Duration.zero;
      });
    }
    final text = _dictation.toString().trim();
    if (text.isEmpty) {
      _snack('Nothing was heard — try again.');
      return;
    }
    _input.text = text;
    await _sendTyped();
  }

  // ── booking ────────────────────────────────────────────────────────────────
  Future<void> _book(AgentCard card) async {
    if (_busy) return;
    await _ensureSession();
    setState(() {
      _busy = true;
      _stage = 'Opening PayPal';
    });
    try {
      final order = await _api.createOrder([card]);
      _store.addOrder(order);
      _store.addMessage(ChatMessage(
        text: 'Card ${card.cardNo} · ${card.title}',
        fromUser: true,
      ));
      _store.addMessage(ChatMessage(
        text: order.mode == 'mock'
            ? 'Order ${order.orderId} created (sandbox).'
            : 'Order ${order.orderId} created. Approve it in PayPal, then capture.',
        fromUser: false,
        order: order,
      ));
      await _openPayPal(order);
      _scrollToEnd();
    } on ApiException catch (e) {
      _snack(e.message);
    } catch (e) {
      _snack('Could not create the order: $e');
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _stage = null;
        });
      }
    }
  }

  Future<void> _capture(PayOrder order) async {
    setState(() => _busy = true);
    try {
      final captured = await _api.capture(order.orderId);
      _store.addOrder(captured);
      _store.addMessage(ChatMessage(
        text: captured.isCaptured
            ? 'Payment captured. Order ${captured.orderId} is complete.'
            : 'Order is still ${captured.status}. Approve it in PayPal first.',
        fromUser: false,
        order: captured,
      ));
      _scrollToEnd();
    } on ApiException catch (e) {
      _store.addMessage(ChatMessage(
        text: e.message,
        fromUser: false,
        meta: 'error',
      ));
      _scrollToEnd();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openPayPal(PayOrder order) async {
    final url = order.approveUrl;
    if (url == null || url.isEmpty) return;
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened) _snack('Could not open PayPal. Open this link manually: $url');
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(message),
        backgroundColor: PayGrow.surfaceAlt,
      ));
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent + 220,
        duration: const Duration(milliseconds: 280),
        curve: Curves.easeOut,
      );
    });
  }

  void _onVoiceResult(AgentResult result) {
    _store.addMessage(ChatMessage(
      text: result.transcript,
      fromUser: true,
    ));
    _store.addMessage(ChatMessage(
      text: result.narration,
      fromUser: false,
      cards: result.cards,
      audio: result.audio,
      order: result.order,
      meta: '${result.intent} · ${result.llmEngine}',
    ));
    if (result.order != null) {
      _store.addOrder(result.order!);
      _openPayPal(result.order!);
    }
    _scrollToEnd();
  }

  // ── build ──────────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffold,
      backgroundColor: PayGrow.black,
      drawer: _drawer(),
      body: SafeArea(
        child: Column(
          children: [
            _header(),
            Expanded(child: _messages()),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                PayGrow.pageMargin,
                0,
                PayGrow.pageMargin,
                14,
              ),
              child: _composer(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _header() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        PayGrow.pageMargin,
        12,
        PayGrow.pageMargin,
        8,
      ),
      child: Row(
        children: [
          MenuButton(onTap: () => _scaffold.currentState?.openDrawer()),
          // The title is centred against the whole bar, so the spacer mirrors
          // the menu button's width.
          Expanded(
            child: Center(
              child: Text(
                'PayGrow',
                style: TextStyle(
                  color: PayGrow.green,
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.3,
                ),
              ),
            ),
          ),
          const SizedBox(width: 46),
        ],
      ),
    );
  }

  Widget _stageRow() {
    return Padding(
      padding: const EdgeInsets.only(top: 12, bottom: 4),
      child: Row(
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: PayGrow.orange,
            ),
          ),
          const SizedBox(width: 8),
          ShimmerStage(label: _stage!),
        ],
      ),
    );
  }

  Widget _messages() {
    final session = _store.active;
    final messages = session?.messages ?? const <ChatMessage>[];

    if (messages.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 44),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: const [
              Text(
                'Speak. Pick. Pay.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: PayGrow.white,
                  fontSize: 21,
                  fontWeight: FontWeight.w700,
                  height: 1.3,
                ),
              ),
              SizedBox(height: 22),
              _Step(number: '1', text: 'Speak or type your shopping list'),
              SizedBox(height: 12),
              _Step(number: '2', text: 'Tap a card number to book it'),
              SizedBox(height: 12),
              _Step(number: '3', text: 'Approve the payment in PayPal'),
            ],
          ),
        ),
      );
    }

    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(
        PayGrow.pageMargin,
        4,
        PayGrow.pageMargin,
        16,
      ),
      itemCount: messages.length + (_busy && _stage != null ? 1 : 0),
      itemBuilder: (context, index) {
        // The processing label sits in the conversation, right under the
        // message that started the turn - the way every chat app does it.
        if (index >= messages.length) return _stageRow();
        final message = messages[index];
        final isRevealing = _revealingIndex == index;
        return _bubble(message, isRevealing);
      },
    );
  }

  Widget _bubble(ChatMessage message, bool isRevealing) {
    if (message.fromUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.only(top: 10, left: 40),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: PayGrow.orange.withValues(alpha: 0.16),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: PayGrow.orange.withValues(alpha: 0.35)),
          ),
          child: Text(
            message.text,
            style: const TextStyle(color: PayGrow.white, fontSize: 13.5),
          ),
        ),
      );
    }

    final text = isRevealing && (_liveCaption ?? '').isNotEmpty
        ? _liveCaption!
        : message.text;

    if (message.meta == 'error') {
      return Container(
        margin: const EdgeInsets.only(top: 10, right: 24),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: PayGrow.orange.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: PayGrow.orange.withValues(alpha: 0.35)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.error_outline_rounded,
                size: 15, color: PayGrow.orange),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                text,
                style: const TextStyle(
                  color: PayGrow.white,
                  fontSize: 12.5,
                  height: 1.45,
                ),
              ),
            ),
          ],
        ),
      );
    }

    return Container(
      margin: const EdgeInsets.only(top: 10, right: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (message.cards.isNotEmpty) ...[
            SizedBox(
              height: 258,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: message.cards.length,
                separatorBuilder: (_, __) => const SizedBox(width: 10),
                itemBuilder: (context, i) => CardTile(
                  card: message.cards[i],
                  onBook: () => _book(message.cards[i]),
                ),
              ),
            ),
            const SizedBox(height: 10),
          ],
          Text(
            text,
            style: const TextStyle(
              color: PayGrow.white,
              fontSize: 13.5,
              height: 1.5,
            ),
          ),
          if (message.order != null) ...[
            const SizedBox(height: 8),
            _orderChip(message.order!),
          ],
          if (message.meta != null) ...[
            const SizedBox(height: 6),
            Text(
              message.meta!,
              style: const TextStyle(color: PayGrow.grey, fontSize: 10),
            ),
          ],
        ],
      ),
    );
  }

  Widget _orderChip(PayOrder order) {
    final captured = order.isCaptured;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: PayGrow.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: PayGrow.line),
      ),
      child: Row(
        children: [
          Icon(
            captured ? Icons.check_circle_rounded : Icons.schedule_rounded,
            size: 16,
            color: captured ? PayGrow.orange : PayGrow.grey,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '${order.orderId} · ${order.status}'
              '${order.amount != null ? ' · ${order.amount!.toStringAsFixed(2)} ${order.currency ?? ''}' : ''}',
              style: const TextStyle(color: PayGrow.grey, fontSize: 11.5),
            ),
          ),
          if (!captured)
            TextButton(
              onPressed: () => _capture(order),
              child: const Text(
                'Capture',
                style: TextStyle(color: PayGrow.orange, fontSize: 11.5),
              ),
            ),
        ],
      ),
    );
  }

  Widget _composer() {
    if (_mode == ComposerMode.voice) {
      return VoiceWindow(
        api: _api,
        cards: () => _store.lastCards,
        onResult: _onVoiceResult,
        onClose: () => setState(() => _mode = ComposerMode.idle),
      );
    }

    if (_mode == ComposerMode.recording) {
      return Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_dictation.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8, left: 4),
              child: Text(
                _dictation.toString(),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: PayGrow.white,
                  fontSize: 12.5,
                  height: 1.4,
                ),
              ),
            ),
          RecordingBar(
            elapsed: _elapsed,
            onCancel: _cancelInlineRecording,
            onSend: _sendInlineRecording,
          ),
        ],
      );
    }

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 4, 6, 4),
      decoration: BoxDecoration(
        color: PayGrow.surfaceAlt,
        borderRadius: BorderRadius.circular(26),
        border: Border.all(color: PayGrow.line),
      ),
      child: ValueListenableBuilder<TextEditingValue>(
        valueListenable: _input,
        builder: (context, value, _) {
          final bool typed = value.text.trim().isNotEmpty;
          return Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _input,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _sendTyped(),
                  style: const TextStyle(color: PayGrow.white, fontSize: 13.5),
                  decoration: const InputDecoration(
                    isDense: true,
                    border: InputBorder.none,
                    hintText: 'Type your list…',
                    hintStyle: TextStyle(color: PayGrow.grey, fontSize: 13),
                  ),
                ),
              ),
              // Voice belongs to the empty box: once there is text the shopper
              // is typing, so the two voice buttons step aside.
              if (!typed) ...[
                IconButton(
                  onPressed: _busy ? null : _startInlineRecording,
                  icon: const Icon(Icons.mic_none_rounded),
                  color: PayGrow.white,
                  tooltip: 'Record',
                ),
                IconButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() => _mode = ComposerMode.voice),
                  icon: const Icon(Icons.graphic_eq_rounded),
                  color: PayGrow.orange,
                  tooltip: 'Voice window',
                ),
              ],
              if (typed) ...[
                const SizedBox(width: 2),
                InkWell(
                  onTap: _busy ? null : _sendTyped,
                  borderRadius: BorderRadius.circular(30),
                  child: Container(
                    width: 40,
                    height: 40,
                    decoration: const BoxDecoration(
                      color: PayGrow.white,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.arrow_upward_rounded,
                      color: PayGrow.black,
                      size: 20,
                    ),
                  ),
                ),
              ],
            ],
          );
        },
      ),
    );
  }

  Widget _drawer() {
    final sessions = _store.sessions;
    return Drawer(
      backgroundColor: PayGrow.black,
      width: 312,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 18, 20, 10),
              child: Row(
                children: [
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                    decoration: BoxDecoration(
                      color: PayGrow.orange.withValues(alpha: 0.16),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                        color: PayGrow.orange.withValues(alpha: 0.4),
                      ),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.receipt_long_rounded,
                            size: 14, color: PayGrow.orange),
                        SizedBox(width: 6),
                        Text(
                          'Order History',
                          style: TextStyle(
                            color: PayGrow.orange,
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const Spacer(),
                  IconButton(
                    onPressed: _signOut,
                    tooltip: 'Sign out',
                    icon: const Icon(Icons.logout_rounded,
                        color: PayGrow.grey, size: 18),
                  ),
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close_rounded,
                        color: PayGrow.grey, size: 20),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Row(
                children: [
                  Container(
                    width: 26,
                    height: 26,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: PayGrow.surfaceAlt,
                      shape: BoxShape.circle,
                      border: Border.all(color: PayGrow.line),
                    ),
                    child: Text(
                      (_accountEmail ?? 'G').substring(0, 1).toUpperCase(),
                      style: const TextStyle(
                        color: PayGrow.white,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      _accountEmail ?? 'Guest',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: PayGrow.grey,
                        fontSize: 11.5,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Row(
                children: [
                  Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: _historyState == 'saved'
                          ? PayGrow.green
                          : PayGrow.orange,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _historyState == 'saved'
                          ? 'History is saved'
                          : _historyState == 'temporary'
                              ? 'History is temporary — database not connected'
                              : 'Checking history…',
                      style: const TextStyle(
                        color: PayGrow.grey,
                        fontSize: 10.5,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: SizedBox(
                width: double.infinity,
                height: 42,
                child: OutlinedButton.icon(
                  onPressed: () {
                    Navigator.of(context).pop();
                    _newOrder();
                  },
                  icon: const Icon(Icons.add_rounded,
                      size: 17, color: PayGrow.orange),
                  label: const Text(
                    'New order',
                    style: TextStyle(
                      color: PayGrow.orange,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  style: OutlinedButton.styleFrom(
                    side: BorderSide(
                      color: PayGrow.orange.withValues(alpha: 0.45),
                    ),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                ),
              ),
            ),
            Expanded(
              child: sessions.isEmpty
                  ? const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 20),
                      child: Text(
                        'No chats yet. Say something and it will appear here with its orders.',
                        style: TextStyle(color: PayGrow.grey, fontSize: 12),
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      itemCount: sessions.length,
                      itemBuilder: (context, index) =>
                          _sessionTile(sessions[index]),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sessionTile(ChatSession session) {
    final active = _store.active?.id == session.id;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: active ? PayGrow.surfaceAlt : PayGrow.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: active ? PayGrow.orange.withValues(alpha: 0.45) : PayGrow.line,
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () {
          _store.select(session);
          Navigator.of(context).pop();
          _openSession(session.id);
        },
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      session.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: PayGrow.white,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    session.timeLabel,
                    style: const TextStyle(color: PayGrow.grey, fontSize: 10.5),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                '${session.messages.length} messages · ${session.orders.length} orders',
                style: const TextStyle(color: PayGrow.grey, fontSize: 10.5),
              ),
              if (session.orders.isNotEmpty) ...[
                const SizedBox(height: 8),
                ...session.orders.map(
                  (order) => Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Row(
                      children: [
                        Icon(
                          order.isCaptured
                              ? Icons.check_circle_rounded
                              : Icons.schedule_rounded,
                          size: 12,
                          color: order.isCaptured
                              ? PayGrow.orange
                              : PayGrow.grey,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            '${order.orderId} · ${order.status}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: PayGrow.grey,
                              fontSize: 10.5,
                            ),
                          ),
                        ),
                        if (order.amount != null)
                          Text(
                            order.amount!.toStringAsFixed(2),
                            style: const TextStyle(
                              color: PayGrow.white,
                              fontSize: 10.5,
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.number, required this.text});

  final String number;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 20,
          height: 20,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: PayGrow.green.withValues(alpha: 0.16),
            shape: BoxShape.circle,
            border: Border.all(color: PayGrow.green.withValues(alpha: 0.45)),
          ),
          child: Text(
            number,
            style: const TextStyle(
              color: PayGrow.green,
              fontSize: 10.5,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            text,
            style: const TextStyle(
              color: PayGrow.grey,
              fontSize: 12.5,
              height: 1.45,
            ),
          ),
        ),
      ],
    );
  }
}
