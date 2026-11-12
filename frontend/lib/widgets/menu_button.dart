/// The menu mark. Three bars of different length with the top one in the brand
/// colour, because a plain hamburger looked like every other app.

import 'package:flutter/material.dart';

import '../theme.dart';

/// The menu button: three staggered bars with an orange leading bar, so it
/// reads as a designed mark rather than a stock hamburger.
class MenuButton extends StatelessWidget {
  const MenuButton({super.key, required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Open menu',
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(
            color: PayGrow.surfaceAlt,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: PayGrow.line),
          ),
          child: Center(
            child: SizedBox(
              width: 20,
              height: 14,
              child: CustomPaint(painter: _MenuMark()),
            ),
          ),
        ),
      ),
    );
  }
}

class _MenuMark extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final white = Paint()
      ..color = PayGrow.white
      ..strokeCap = StrokeCap.round
      ..strokeWidth = 2.2;
    final orange = Paint()
      ..color = PayGrow.orange
      ..strokeCap = StrokeCap.round
      ..strokeWidth = 2.2;

    final double y1 = 1.1;
    final double y2 = size.height / 2;
    final double y3 = size.height - 1.1;

    // top bar: short and orange, indented from the left
    canvas.drawLine(Offset(5, y1), Offset(size.width, y1), orange);
    canvas.drawLine(Offset(0, y2), Offset(size.width, y2), white);
    canvas.drawLine(Offset(5, y3), Offset(size.width, y3), white);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
