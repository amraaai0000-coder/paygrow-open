/// Plays the narration and reports the words to show as it goes.
///
/// The audio arrives already split into sentences. Playing them in order and
/// matching the text to the position is what makes the words look spoken rather
/// than printed.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';

import 'models.dart';

/// Plays the narration the backend already synthesised, one sentence at a time,
/// and reports the words that should be on screen as the audio moves, that is
/// what makes the text appear to be spoken rather than dumped.
class NarrationPlayer {
  final AudioPlayer _player = AudioPlayer();
  bool _cancelled = false;

  Future<void> play(
    List<AudioChunk> chunks, {
    required void Function(String visibleText) onText,
  }) async {
    _cancelled = false;
    final buffer = <String>[];

    for (final chunk in chunks) {
      if (_cancelled) return;

      if (chunk.audio == null || chunk.audio!.isEmpty) {
        buffer.add(chunk.text);
        onText(buffer.join(' '));
        continue;
      }

      try {
        final file = await _writeTemp(chunk.audio!);
        await _player.setFilePath(file.path);

        final words = chunk.text.split(' ');
        final total = _player.duration ?? const Duration(milliseconds: 2500);
        final timer = Timer.periodic(const Duration(milliseconds: 90), (_) {
          if (_cancelled) return;
          final elapsed = _player.position.inMilliseconds;
          final fraction = total.inMilliseconds <= 0
              ? 1.0
              : (elapsed / total.inMilliseconds).clamp(0.0, 1.0);
          final shown = (fraction * words.length).floor().clamp(0, words.length);
          onText([...buffer, words.take(shown).join(' ')].join(' ').trim());
        });

        await _player.play();
        timer.cancel();
        buffer.add(chunk.text);
        onText(buffer.join(' ').trim());
      } catch (_) {
        buffer.add(chunk.text);
        onText(buffer.join(' ').trim());
      }
    }
  }

  Future<File> _writeTemp(String base64Audio) async {
    final directory = await getTemporaryDirectory();
    final file = File(
      '${directory.path}/paygrow_${DateTime.now().microsecondsSinceEpoch}.wav',
    );
    await file.writeAsBytes(base64Decode(base64Audio));
    return file;
  }

  Future<void> stop() async {
    _cancelled = true;
    try {
      await _player.stop();
    } catch (_) {
      // nothing playing
    }
  }

  void dispose() {
    _cancelled = true;
    _player.dispose();
  }
}
