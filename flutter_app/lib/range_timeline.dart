import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'timeline_frames.dart';

/// 画像と暗幕・境界線を同じ24pxのトラック余白に合わせる。
class RangeTimeline extends StatelessWidget {
  const RangeTimeline(
      {super.key,
      required this.frames,
      required this.values,
      required this.durationMs,
      required this.onChanged,
      this.compact = false});
  final List<Uint8List?> frames;
  final RangeValues values;
  final int durationMs;
  final ValueChanged<RangeValues>? onChanged;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final maximum = durationMs > 0 ? durationMs.toDouble() : 1.0;
    final safe = RangeValues(
        values.start.clamp(0, maximum), values.end.clamp(0, maximum));
    return Column(mainAxisSize: MainAxisSize.min, children: [
      if (!compact)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: List.generate(
                  5,
                  (i) => Text('${(durationMs * i / 4000).toStringAsFixed(1)}秒',
                      style: const TextStyle(
                          fontSize: 10, color: Color(0xff738198))))),
        ),
      SizedBox(
          height: compact ? 48 : 64,
          child: Stack(fit: StackFit.expand, children: [
            TimelineFrames(frames: frames),
            IgnorePointer(
                child: CustomPaint(
                    painter: RangeShade(
                        start: safe.start / maximum, end: safe.end / maximum))),
            SliderTheme(
                data: SliderTheme.of(context).copyWith(
                  activeTrackColor: Colors.transparent,
                  inactiveTrackColor: Colors.transparent,
                  disabledActiveTrackColor: Colors.transparent,
                  disabledInactiveTrackColor: Colors.transparent,
                  trackHeight: 2,
                  rangeThumbShape:
                      const RoundRangeSliderThumbShape(enabledThumbRadius: 7),
                ),
                child: RangeSlider(
                    min: 0,
                    max: maximum,
                    values: safe,
                    labels: RangeLabels(
                        '${(safe.start / 1000).toStringAsFixed(3)}秒',
                        '${(safe.end / 1000).toStringAsFixed(3)}秒'),
                    onChanged: onChanged)),
          ])),
    ]);
  }
}

class RangeShade extends CustomPainter {
  const RangeShade({required this.start, required this.end});
  final double start, end;
  @override
  void paint(Canvas canvas, Size size) {
    final width = (size.width - 48).clamp(0.0, double.infinity);
    final left = 24 + width * start;
    final right = 24 + width * end;
    final top = 6.0;
    final bottom = size.height - 6;
    final shade = Paint()..color = const Color(0x990f2033);
    canvas.drawRect(Rect.fromLTRB(24, top, left, bottom), shade);
    canvas.drawRect(Rect.fromLTRB(right, top, size.width - 24, bottom), shade);
    final border = Paint()
      ..color = const Color(0xff1266ff)
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    canvas.drawRect(Rect.fromLTRB(left, top, right, bottom), border);
    canvas.drawLine(Offset(left, 1), Offset(left, size.height - 1), border);
    canvas.drawLine(Offset(right, 1), Offset(right, size.height - 1), border);
    final tick = Paint()
      ..color = const Color(0xff8e9bac)
      ..strokeWidth = 1;
    for (var i = 0; i <= 16; i++) {
      final x = 24 + width * i / 16;
      canvas.drawLine(Offset(x, 0), Offset(x, i % 4 == 0 ? 5 : 3), tick);
    }
  }

  @override
  bool shouldRepaint(covariant RangeShade oldDelegate) =>
      oldDelegate.start != start || oldDelegate.end != end;
}
