import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/range_timeline.dart';

void main() {
  for (final width in [320.0, 960.0]) {
    testWidgets('範囲の暗幕と両端操作 $width', (tester) async {
      var values = const RangeValues(2000, 8000);
      await tester.pumpWidget(MaterialApp(theme: ThemeData(splashFactory: InkRipple.splashFactory),
        home: Scaffold(body: Center(child: SizedBox(width: width,
          child: StatefulBuilder(builder: (context, update) => RangeTimeline(
            frames: List.filled(8, null), values: values, durationMs: 10000,
            compact: width < 400, onChanged: (next) => update(() => values = next),
          )),
        )))));
      final slider = find.byType(RangeSlider);
      final rect = tester.getRect(slider);
      final usable = rect.width - 48;
      await tester.dragFrom(Offset(rect.left + 24 + usable * 0.2, rect.center.dy), const Offset(20, 0));
      await tester.pumpAndSettle();
      expect(values.start, greaterThan(2000));
      await tester.dragFrom(Offset(rect.left + 24 + usable * 0.8, rect.center.dy), const Offset(-20, 0));
      await tester.pumpAndSettle();
      expect(values.end, lessThan(8000));
      expect(tester.takeException(), isNull);
      final paints = tester.widgetList<CustomPaint>(find.byType(CustomPaint));
      final shade = paints.map((p) => p.painter).whereType<RangeShade>().single;
      expect(shade.start, values.start / 10000);
      expect(shade.end, values.end / 10000);
      expect(tester.getSize(find.byType(RangeTimeline)).height, lessThanOrEqualTo(80));
    });
  }
}
