/// 描画準備が完了するまで、初回の動画読み込みを開始しない。
Future<void> openPreparedPreview({
  required Future<void> ready,
  required bool Function() isActive,
  required Future<void> Function() open,
  Duration timeout = const Duration(seconds: 15),
}) async {
  await ready.timeout(timeout);
  if (isActive()) await open();
}
