/// The recorder that takes the composer's place.
///
/// Fixed layout on purpose: the label gives way, the clock and the level bars keep
/// their space, and the two round buttons sit at the ends. Nothing overflows.

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// The inline recorder that replaces the text box the moment the mic is tapped.
///
/// Everything is inside a fixed layout: the label flexes, the clock and the
/// level bars keep their own space, and both round buttons are pinned to the
/// ends so the send arrow can never hang over the edge.
class RecordingBar extends StatefulWidget {
  const RecordingBar({
    super.key,
    required this.elapsed,
    required this.onCancel,
    required this.onSend,
    this.hint = 'Listening…',
  });

  final Duration elapsed;
  final VoidCallback onCancel;
  final VoidCallback onSend;
  final String hint;

  @override
  State<RecordingBar> createState() => _RecordingBarState();
}

class _RecordingBarState extends State<RecordingBar>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  String get _clock {
    final total = widget.elapsed.inSeconds;
    final minutes = (total ~/ 60).toString().padLeft(2, '0');
    final seconds = (total % 60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 56,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      decoration: BoxDecoration(
        color: PayGrow.surfaceAlt,
        borderRadius: BorderRadius.circular(28),
        border: Border.all(color: PayGrow.line),
      ),
      child: Row(
        children: [
          _roundButton(
            icon: Icons.close_rounded,
            onTap: widget.onCancel,
            filled: false,
          ),
          const SizedBox(width: 10),
          AnimatedBuilder(
            animation: _pulse,
            builder: (context, _) {
              final double t = 0.5 + 0.5 * math.sin(_pulse.value * 2 * math.pi);
              return Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: PayGrow.orange.withValues(alpha: 0.45 + 0.55 * t),
                ),
              );
            },
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              widget.hint,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: PayGrow.white,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            _clock,
            style: const TextStyle(color: PayGrow.grey, fontSize: 11.5),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 46,
            height: 22,
            child: AnimatedBuilder(
              animation: _pulse,
              builder: (context, _) =>
                  CustomPaint(painter: _LevelBars(phase: _pulse.value)),
            ),
          ),
          const SizedBox(width: 10),
          _roundButton(
            icon: Icons.arrow_upward_rounded,
            onTap: widget.onSend,
            filled: true,
          ),
        ],
      ),
    );
  }

  Widget _roundButton({
    required IconData icon,
    required VoidCallback onTap,
    required bool filled,
  }) {
    return Material(
      color: filled ? PayGrow.white : PayGrow.surface,
      shape: const CircleBorder(),
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: SizedBox(
          width: 40,
          height: 40,
          child: Icon(
            icon,
            size: 20,
            color: filled ? PayGrow.black : PayGrow.white,
          ),
        ),
      ),
    );
  }
}

class _LevelBars extends CustomPainter {
  _LevelBars({required this.phase});

  final double phase;

  @override
  void paint(Canvas canvas, Size size) {
    const int bars = 7;
    final double step = size.width / bars;
    final paint = Paint()
      ..color = PayGrow.orange
      ..strokeCap = StrokeCap.round
      ..strokeWidth = 3;

    for (int i = 0; i < bars; i++) {
      final double wave = math.sin((phase * 2 * math.pi) + i * 0.8);
      final double height = 5 + (size.height - 6) * (0.5 + 0.5 * wave);
      final double x = step * i + step / 2;
      canvas.drawLine(
        Offset(x, size.height / 2 - height / 2),
        Offset(x, size.height / 2 + height / 2),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _LevelBars oldDelegate) =>
      oldDelegate.phase != phase;
}
