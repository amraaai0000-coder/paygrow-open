/// The voice window that replaces the composer.
///
/// It listens, stops on its own when the shopper pauses, sends, speaks the answer
/// and starts listening again. The cards and the text belong to the chat behind
/// it, so this panel shows state and controls only.

import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../api.dart';
import '../models.dart';
import '../narration.dart';
import '../theme.dart';
import '../widgets/shimmer_stage.dart';

enum VoicePhase { connecting, listening, muted, thinking }

/// The voice window replaces the typing box and runs the turn by itself.
///
/// It shows ONLY what the box is for: the connection state, the processing
/// labels and a mute switch. The cards and the narration text belong to the
/// chat behind it, exactly like a typed turn - so they are handed to the host
/// through [onResult] and drawn there.
class VoiceWindow extends StatefulWidget {
  const VoiceWindow({
    super.key,
    required this.api,
    required this.cards,
    required this.onResult,
    required this.onClose,
  });

  final PayGrowApi api;
  final List<AgentCard> Function() cards;
  final void Function(AgentResult result) onResult;
  final VoidCallback onClose;

  @override
  State<VoiceWindow> createState() => _VoiceWindowState();
}

class _VoiceWindowState extends State<VoiceWindow>
    with SingleTickerProviderStateMixin {
  static const double _silenceFloorDb = -35;
  static const Duration _silenceWindow = Duration(milliseconds: 450);
  static const Duration _maxListen = Duration(seconds: 20);

  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  )..repeat(reverse: true);

  final AudioRecorder _recorder = AudioRecorder();
  final NarrationPlayer _narration = NarrationPlayer();

  VoicePhase _phase = VoicePhase.connecting;
  String? _error;
  bool _busy = false;
  bool _disposed = false;

  StreamSubscription<Amplitude>? _ampSub;
  Timer? _maxTimer;
  bool _heardSpeech = false;
  DateTime _lastLoudAt = DateTime.now();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _connectThenListen());
  }

  @override
  void dispose() {
    _disposed = true;
    _ampSub?.cancel();
    _maxTimer?.cancel();
    _narration.dispose();
    _recorder.dispose();
    _pulse.dispose();
    super.dispose();
  }

  /// Show "Connecting" until /health answers, then start listening at once.
  Future<void> _connectThenListen() async {
    if (_disposed) return;
    setState(() {
      _phase = VoicePhase.connecting;
      _error = null;
    });

    try {
      await widget.api.health().timeout(const Duration(seconds: 45));
    } catch (_) {
      if (!_disposed) {
        setState(() {
          _error = 'Server is not responding. Tap the mic to try again.';
          _phase = VoicePhase.listening;
        });
      }
      return;
    }

    if (!_disposed) await _listen();
  }

  Future<void> _listen() async {
    if (_disposed) return;
    setState(() {
      _phase = VoicePhase.listening;
      _error = null;
    });

    try {
      if (!await _recorder.hasPermission()) {
        setState(() => _error = 'Microphone permission was denied.');
        return;
      }
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/pg_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _recorder.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
        ),
        path: path,
      );
    } catch (e) {
      setState(() => _error = 'Could not start the microphone: $e');
      return;
    }

    _heardSpeech = false;
    _lastLoudAt = DateTime.now();

    _ampSub?.cancel();
    _ampSub = _recorder
        .onAmplitudeChanged(const Duration(milliseconds: 100))
        .listen((amp) {
      if (_disposed || _busy) return;
      final now = DateTime.now();
      if (amp.current > _silenceFloorDb) {
        _heardSpeech = true;
        _lastLoudAt = now;
      } else if (_heardSpeech && now.difference(_lastLoudAt) > _silenceWindow) {
        _finishTurn();
      }
    });

    _maxTimer?.cancel();
    _maxTimer = Timer(_maxListen, () {
      if (!_busy) _finishTurn();
    });
  }

  Future<void> _toggleMute() async {
    if (_phase == VoicePhase.muted) {
      await _listen();
      return;
    }
    _ampSub?.cancel();
    _maxTimer?.cancel();
    try {
      await _recorder.stop();
    } catch (_) {}
    if (!_disposed) {
      setState(() => _phase = VoicePhase.muted);
    }
  }

  Future<void> _finishTurn() async {
    if (_busy || _disposed) return;
    _busy = true;
    _ampSub?.cancel();
    _maxTimer?.cancel();

    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {
      path = null;
    }

    if (path == null || _disposed) {
      _busy = false;
      return;
    }

    final file = File(path);
    if (!file.existsSync() || file.lengthSync() < 1200) {
      _busy = false;
      await _listen();
      return;
    }

    setState(() => _phase = VoicePhase.thinking);

    try {
      final result = await widget.api.voice(file, cards: widget.cards());
      // The chat draws the cards and the text - the box stays a control panel.
      widget.onResult(result);
      if (_disposed) return;

      // Text to speech happens ONLY here, in the fully automated voice mode.
      await _narration.play(result.audio, onText: (_) {});
    } on ApiException catch (e) {
      if (!_disposed) setState(() => _error = e.message);
    } catch (e) {
      if (!_disposed) setState(() => _error = 'Network problem: $e');
    }

    _busy = false;
    if (!_disposed) await _listen();
  }

  Future<void> _stopAll() async {
    _ampSub?.cancel();
    _maxTimer?.cancel();
    await _narration.stop();
    try {
      await _recorder.stop();
    } catch (_) {}
    widget.onClose();
  }

  String get _label {
    switch (_phase) {
      case VoicePhase.connecting:
        return 'Connecting…';
      case VoicePhase.listening:
        return 'Listening';
      case VoicePhase.muted:
        return 'Muted';
      case VoicePhase.thinking:
        return 'Working on it';
    }
  }

  String get _hint {
    switch (_phase) {
      case VoicePhase.connecting:
        return 'Waking the server up.';
      case VoicePhase.listening:
        return 'Speak — it sends itself when you pause.';
      case VoicePhase.muted:
        return 'Nothing is being recorded.';
      case VoicePhase.thinking:
        return 'Sending the list.';
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool thinking = _phase == VoicePhase.thinking;
    final bool muted = _phase == VoicePhase.muted;

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 12, 14),
      decoration: BoxDecoration(
        color: PayGrow.surface,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: PayGrow.line),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              AnimatedBuilder(animation: _pulse, builder: (context, _) => _orb()),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    thinking
                        ? const ShimmerStage(label: 'Understanding your order')
                        : Text(
                            _label,
                            style: const TextStyle(
                              color: PayGrow.white,
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                    const SizedBox(height: 3),
                    Text(
                      _hint,
                      style: const TextStyle(color: PayGrow.grey, fontSize: 11.5),
                    ),
                  ],
                ),
              ),
              IconButton(
                onPressed: _toggleMute,
                tooltip: muted ? 'Unmute' : 'Mute',
                icon: Icon(
                  muted ? Icons.mic_off_rounded : Icons.mic_rounded,
                  color: muted ? PayGrow.orange : PayGrow.grey,
                  size: 20,
                ),
              ),
              IconButton(
                onPressed: _stopAll,
                tooltip: 'Close voice window',
                icon: const Icon(Icons.close_rounded, color: PayGrow.grey, size: 20),
              ),
            ],
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                _error!,
                style: const TextStyle(color: PayGrow.orange, fontSize: 11.5),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _orb() {
    final double t = _pulse.value;
    final Color color = _phase == VoicePhase.thinking
        ? PayGrow.orangeDim
        : (_phase == VoicePhase.muted ? PayGrow.grey : PayGrow.orange);

    return Container(
      width: 46,
      height: 46,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: color.withValues(alpha: 0.12 + 0.10 * t),
        border: Border.all(color: color.withValues(alpha: 0.55)),
      ),
      child: Center(
        child: Container(
          width: 18 + 5 * math.sin(t * math.pi),
          height: 18 + 5 * math.sin(t * math.pi),
          decoration: BoxDecoration(shape: BoxShape.circle, color: color),
        ),
      ),
    );
  }
}
