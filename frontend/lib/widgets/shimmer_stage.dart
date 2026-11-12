/// A processing label with a slow sweep across it.
///
/// The colours are the brand orange with a grey band, which reads as work in
/// progress without shouting.

import 'package:flutter/material.dart';

import '../theme.dart';

/// A processing label with a shimmer that sweeps right to left, mostly orange
/// with a grey band, so it reads as "working" without shouting.
class ShimmerStage extends StatefulWidget {
  const ShimmerStage({super.key, required this.label});

  final String label;

  @override
  State<ShimmerStage> createState() => _ShimmerStageState();
}

class _ShimmerStageState extends State<ShimmerStage>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1500),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        // 1 → -1 moves the highlight from the right edge to the left edge
        final double shift = 1.0 - (_controller.value * 2.0);
        return ShaderMask(
          blendMode: BlendMode.srcIn,
          shaderCallback: (Rect bounds) => LinearGradient(
            begin: Alignment(shift + 1.0, 0),
            end: Alignment(shift - 1.0, 0),
            colors: const [
              PayGrow.orange,
              PayGrow.orange,
              PayGrow.grey,
              PayGrow.orange,
            ],
            stops: const [0.0, 0.42, 0.55, 0.7],
          ).createShader(bounds),
          child: Text(
            widget.label,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.2,
              color: PayGrow.white,
            ),
          ),
        );
      },
    );
  }
}
