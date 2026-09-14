import 'package:flutter/material.dart';

String playbackTime(int ms) {
  final seconds = ms ~/ 1000;
  return '${(seconds ~/ 60).toString().padLeft(2, '0')}:${(seconds % 60).toString().padLeft(2, '0')}';
}

/// 動画へのタップやマウス移動を必要としない再生操作。
class PlaybackBar extends StatelessWidget {
  const PlaybackBar(
      {super.key,
      required this.positionMs,
      required this.durationMs,
      required this.playing,
      required this.muted,
      required this.enabled,
      required this.onToggle,
      required this.onMute,
      required this.onSeek});
  final int positionMs, durationMs;
  final bool playing, muted, enabled;
  final VoidCallback onToggle, onMute;
  final ValueChanged<int> onSeek;

  @override
  Widget build(BuildContext context) => Container(
        height: 52,
        decoration: const BoxDecoration(
            color: Colors.white,
            border: Border(bottom: BorderSide(color: Color(0xffe4e9f0)))),
        child: Row(children: [
          IconButton(
              tooltip: playing ? '一時停止' : '再生',
              onPressed: enabled ? onToggle : null,
              icon: Icon(
                  playing ? Icons.pause_rounded : Icons.play_arrow_rounded)),
          IconButton(
              tooltip: muted ? '音声をオン' : 'ミュート',
              onPressed: enabled ? onMute : null,
              icon: Icon(muted
                  ? Icons.volume_off_outlined
                  : Icons.volume_up_outlined)),
          Expanded(
              child: Slider(
                  semanticFormatterCallback: (v) => playbackTime(v.round()),
                  max: durationMs > 0 ? durationMs.toDouble() : 1,
                  value: positionMs
                      .clamp(0, durationMs > 0 ? durationMs : 1)
                      .toDouble(),
                  onChanged: enabled && durationMs > 0
                      ? (v) => onSeek(v.round())
                      : null)),
          Padding(
              padding: const EdgeInsets.only(right: 10),
              child: Text(
                  '${playbackTime(positionMs)} / ${playbackTime(durationMs)}',
                  style: const TextStyle(fontSize: 11))),
        ]),
      );
}

class EditorNotice extends StatelessWidget {
  const EditorNotice(
      {super.key,
      required this.failed,
      required this.details,
      required this.onDismiss});
  final bool failed;
  final String details;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) => Container(
        height: 48,
        padding: const EdgeInsets.only(left: 12),
        color: failed ? const Color(0xffffeded) : const Color(0xffe9f7ef),
        child: Row(children: [
          Icon(failed ? Icons.error_outline : Icons.check_circle_outline,
              size: 18,
              color: failed ? Colors.red.shade700 : Colors.green.shade700),
          const SizedBox(width: 8),
          Expanded(
              child: Text(failed ? '処理を完了できませんでした' : '動画を保存しました',
                  maxLines: 1, overflow: TextOverflow.ellipsis)),
          TextButton(
              onPressed: () => showDialog<void>(
                  context: context,
                  builder: (context) => AlertDialog(
                        title: Text(failed ? 'エラーの詳細' : '保存結果'),
                        content: SingleChildScrollView(
                            child: SelectableText(details)),
                        actions: [
                          TextButton(
                              onPressed: () => Navigator.pop(context),
                              child: const Text('閉じる'))
                        ],
                      )),
              child: const Text('詳細')),
          IconButton(
              tooltip: '通知を閉じる',
              onPressed: onDismiss,
              icon: const Icon(Icons.close, size: 18)),
        ]),
      );
}
