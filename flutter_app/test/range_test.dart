import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/export_service.dart';

void main() {
  test('動画境界内の区間は受け付ける', () {
    expect(() => const ClipRange(0, 1000).validate(1000), returnsNormally);
  });
  test('負数・空区間・逆転・動画外を保存しない', () {
    for (final range in [
      const ClipRange(-1, 100),
      const ClipRange(100, 100),
      const ClipRange(200, 100),
      const ClipRange(0, 1001),
    ]) {
      expect(() => range.validate(1000), throwsArgumentError);
    }
  });
}
