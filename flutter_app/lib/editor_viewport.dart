import 'package:flutter/material.dart';

/// 操作欄の高さを先に確保し、残った高さだけを動画に割り当てる。
class EditorViewport extends StatelessWidget {
  const EditorViewport(
      {super.key, required this.preview, required this.controls});
  final Widget preview;
  final List<Widget> controls;

  @override
  Widget build(BuildContext context) => Column(
        children: [
          Expanded(child: preview),
          ...controls,
        ],
      );
}

/// 両端が動画末尾に達しても EOF ではなく最後の映像を表示する。
int rangePreviewPosition(
    RangeValues previous, RangeValues next, int durationMs) {
  final target = next.end != previous.end ? next.end : next.start;
  return target.round().clamp(0, durationMs > 0 ? durationMs - 1 : 0);
}
