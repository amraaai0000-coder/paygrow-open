/// Colours, spacing and the one theme the app uses.

import 'package:flutter/material.dart';

/// One place for every colour and spacing value the app uses.
class PayGrow {
  PayGrow._();

  static const Color black = Color(0xFF0A0A0B);
  static const Color surface = Color(0xFF141416);
  static const Color surfaceAlt = Color(0xFF1C1C1F);
  static const Color line = Color(0xFF2A2A2E);
  static const Color orange = Color(0xFFFF7A1A);

  /// Taken straight from the PayGrow logo.
  static const Color green = Color(0xFF01CA05);
  static const Color navy = Color(0xFF001148);
  static const Color orangeDim = Color(0xFFB85712);
  static const Color grey = Color(0xFF8A8A8F);
  static const Color white = Color(0xFFF7F7F8);

  /// Screen margins: 20 on the sides, 16 above/below the safe area.
  static const double pageMargin = 20;
  static const double gap = 12;

  static ThemeData theme() => ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: black,
        canvasColor: black,
        colorScheme: const ColorScheme.dark(
          primary: orange,
          secondary: orange,
          surface: surface,
        ),
        textTheme: const TextTheme(
          bodyMedium: TextStyle(color: white, fontSize: 14, height: 1.45),
          bodySmall: TextStyle(color: grey, fontSize: 12, height: 1.4),
        ),
        dividerColor: line,
      );
}
