import 'package:flutter/material.dart';

import 'merge_plan.dart';
import 'merge_service.dart';

const _note = TextStyle(fontSize: 12, color: Colors.blueGrey);

String _seconds(int milliseconds) => (milliseconds / 1000).toStringAsFixed(1);

/// まとめる前の確認。実行なら true。
class MergeConfirmDialog extends StatelessWidget {
  const MergeConfirmDialog({super.key, required this.plan, this.android = false});
  final MergePlan plan;
  final bool android;

  @override
  Widget build(BuildContext context) {
    final entries = plan.entries;
    const shown = 10;
    return AlertDialog(
      title: const Text('1本にまとめて保存'),
      content: SingleChildScrollView(
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            Text(
                '動画${plan.videoCount}本・区間${entries.length}個'
                '（合計${_seconds(plan.totalMs)}秒）',
                style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            const Text('つなぐ順番', style: TextStyle(fontWeight: FontWeight.w700)),
            for (final entry in entries.take(shown))
              Text('　${entries.indexOf(entry) + 1}. ${entry.item.name}'
                  '（区間${entry.segmentNumber}・${_seconds(entry.lengthMs)}秒）'),
            if (entries.length > shown)
              Text('　ほか${entries.length - shown}個'),
            if (plan.missing.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                  '元動画が見つからない動画${plan.missing.length}本は含めません：'
                  '${plan.missing.map((item) => item.name).join('、')}',
                  style: const TextStyle(color: Color(0xffa33a3a))),
            ],
            if (plan.unconfirmedVideos > 0)
              Text('まだ開いていない動画${plan.unconfirmedVideos}本は、長さが未確認のため含まれません。'),
            const SizedBox(height: 8),
            const Text('順番を変えるときは、一覧の「︙」から「上へ」「下へ」で動画を並べ替えてください。',
                style: _note),
            const SizedBox(height: 6),
            Text(
                android
                    ? '形式（映像の形式・画面の大きさ・フレームレート・音声）がそろった動画だけをつなげます。'
                        '違いがある場合は、その場で理由を出して中止します。'
                    : '形式がそろっていれば、作り直さずにそのままつなぎます（画質はそのまま）。'
                        '違いがある場合は理由を出して中止します。',
                style: _note),
            const SizedBox(height: 6),
            const Text('元動画と、これまでに保存したファイルは変更しません。', style: _note),
          ])),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('キャンセル')),
        FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('保存先を選ぶ…')),
      ],
    );
  }
}

/// まとめている間の進捗と中止。
class MergeProgressDialog extends StatelessWidget {
  const MergeProgressDialog(
      {super.key,
      required this.phase,
      required this.done,
      required this.total,
      required this.cancelling,
      required this.onCancel});
  final MergePhase phase;
  final int done;
  final int total;
  final bool cancelling;
  final VoidCallback onCancel;

  String get _label => switch (phase) {
        MergePhase.cutting => '区間を取り出しています（$done / $total）',
        MergePhase.checking => '形式を確かめています（$done / $total）',
        MergePhase.joining => 'つないでいます…',
      };

  @override
  Widget build(BuildContext context) => AlertDialog(
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(cancelling ? '中止しています…' : _label),
          const SizedBox(height: 12),
          LinearProgressIndicator(
              value: phase == MergePhase.joining || total <= 0
                  ? null
                  : (done / total).clamp(0.0, 1.0)),
        ]),
        actions: [
          TextButton(
              onPressed: cancelling ? null : onCancel, child: const Text('中止')),
        ],
      );
}

/// まとめ終わったあとの結果。
class MergeResultDialog extends StatelessWidget {
  const MergeResultDialog(
      {super.key, required this.outcome, required this.entries});
  final MergeOutcome outcome;
  final int entries;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('1本にまとめました'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              Text('区間$entries個をつなぎました'
                  '（${outcome.lossless ? '作り直しなし' : '作り直しあり'}・'
                  '${(outcome.elapsed.inMilliseconds / 1000).toStringAsFixed(1)}秒）'),
              const SizedBox(height: 8),
              const Text('保存先', style: TextStyle(fontWeight: FontWeight.w700)),
              SelectableText(outcome.output),
            ])),
        actions: [
          FilledButton(
              onPressed: () => Navigator.pop(context), child: const Text('閉じる')),
        ],
      );
}
