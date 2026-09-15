import 'package:flutter/services.dart';

import 'batch_plan.dart';

typedef BatchExport = Future<String> Function(BatchEntry entry);

enum BatchStatus { saved, failed, notRun }

class BatchResult {
  const BatchResult(this.entry, this.status, {this.output, this.error});
  final BatchEntry entry;
  final BatchStatus status;
  final String? output;
  final String? error;
}

/// 残りを続けても成功しない失敗（保存先フォルダが消えた等）。以降の件は未実行にする。
class BatchAbort implements Exception {
  const BatchAbort(this.message);
  final String message;
  @override
  String toString() => message;
}

class BatchSummary {
  const BatchSummary(this.results,
      {required this.skippedSaved, this.cancelled = false, this.abortReason});
  final List<BatchResult> results;
  final int skippedSaved;
  final bool cancelled;
  final String? abortReason;
  int _count(BatchStatus status) =>
      results.where((r) => r.status == status).length;
  int get saved => _count(BatchStatus.saved);
  int get failed => _count(BatchStatus.failed);
  int get notRun => _count(BatchStatus.notRun);
  List<BatchResult> get failures =>
      results.where((r) => r.status == BatchStatus.failed).toList();
}

/// 1件ずつ順番に保存する。保存処理は差し替え可能（テストでは動画を使わない）。
class BatchRunner {
  BatchRunner(this.export);
  final BatchExport export;
  bool _running = false;
  bool _cancelRequested = false;
  bool get running => _running;
  bool get cancelRequested => _cancelRequested;

  /// 保存中の1件は止めず、次の件へ進む前に停止する（書きかけのファイルを作らない）。
  void cancel() {
    if (_running) _cancelRequested = true;
  }

  Future<BatchSummary> run(BatchPlan plan,
      {void Function(int index, BatchEntry entry)? onStart,
      void Function(BatchResult result)? onResult}) async {
    if (_running) throw StateError('一括保存は実行中です');
    _running = true;
    _cancelRequested = false;
    final results = <BatchResult>[];
    String? abortReason;
    try {
      for (var i = 0; i < plan.entries.length; i++) {
        final entry = plan.entries[i];
        if (_cancelRequested || abortReason != null) {
          results.add(BatchResult(entry, BatchStatus.notRun));
          continue;
        }
        onStart?.call(i, entry);
        BatchResult result;
        try {
          final output = await export(entry);
          if (output.isEmpty) throw StateError('保存先が返されませんでした');
          // 失敗時は以前の保存結果（既存ファイル）を残すため、成功時だけ更新する。
          entry.segment.output = output;
          result = BatchResult(entry, BatchStatus.saved, output: output);
        } on BatchAbort catch (e) {
          abortReason = e.message;
          result = BatchResult(entry, BatchStatus.failed, error: e.message);
        } catch (e) {
          result = BatchResult(entry, BatchStatus.failed,
              error: describeBatchError(e));
        }
        results.add(result);
        onResult?.call(result);
      }
      return BatchSummary(List.unmodifiable(results),
          skippedSaved: plan.skippedSaved,
          cancelled: _cancelRequested,
          abortReason: abortReason);
    } finally {
      _running = false;
      _cancelRequested = false;
    }
  }
}

String describeBatchError(Object error) => switch (error) {
      PlatformException(:final message, :final code) => message ?? code,
      StateError(:final message) => message,
      UnsupportedError(:final message) => message ?? '未対応の操作です',
      ArgumentError(:final message) => '$message',
      _ => '保存できませんでした',
    };
