import 'package:flutter/material.dart';

import 'batch_plan.dart';
import 'batch_runner.dart';
import 'clip_session.dart';

const _note = TextStyle(fontSize: 12, color: Colors.blueGrey);

/// 一括保存の確認。開始時は保存する計画を返し、キャンセルは null。
class BatchConfirmDialog extends StatefulWidget {
  const BatchConfirmDialog(
      {super.key, required this.items, this.android = false});
  final List<ClipItem> items;
  final bool android;
  @override
  State<BatchConfirmDialog> createState() => _BatchConfirmDialogState();
}

class _BatchConfirmDialogState extends State<BatchConfirmDialog> {
  bool includeSaved = false;

  @override
  Widget build(BuildContext context) {
    final plan = BatchPlan.from(widget.items, includeSaved: includeSaved);
    final hasSaved = BatchPlan.from(widget.items).skippedSaved > 0;
    final seconds = (plan.totalMs / 1000).toStringAsFixed(1);
    return AlertDialog(
      title: const Text('すべて保存'),
      content: SingleChildScrollView(
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            Text(
                plan.isEmpty
                    ? '保存する区間がありません'
                    : '動画${plan.videoCount}本・区間${plan.entries.length}個（合計$seconds秒）',
                style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            if (plan.unconfirmedVideos > 0)
              Text(
                  'まだ開いていない動画${plan.unconfirmedVideos}本は、長さが未確認のため含まれません。'),
            if (plan.noProposalVideos > 0)
              Text('AIの提案がなかった動画${plan.noProposalVideos}本は含めません'
                  '（一覧の「︙」から全体を対象に戻せます）。'),
            if (plan.wholeVideos > 0)
              Text(
                  '区間を決めていない動画${plan.wholeVideos}本は、全体'
                  '（合計${(plan.wholeMs / 1000).toStringAsFixed(1)}秒）がそのまま入ります。',
                  style: const TextStyle(color: Color(0xffa36a00))),
            if (plan.skippedSaved > 0)
              Text('保存済みの区間${plan.skippedSaved}個はスキップします。'),
            if (hasSaved)
              CheckboxListTile(
                  value: includeSaved,
                  onChanged: (value) =>
                      setState(() => includeSaved = value ?? false),
                  title: const Text('保存済みの区間も保存し直す'),
                  controlAffinity: ListTileControlAffinity.leading,
                  contentPadding: EdgeInsets.zero,
                  dense: true),
            if (plan.isEmpty)
              Text(
                  hasSaved
                      ? 'すべて保存済みです。保存し直す場合はチェックを入れてください。'
                      : '動画を一覧から選んで開くと、区間ができます。',
                  style: const TextStyle(color: Color(0xffa33a3a))),
            const SizedBox(height: 8),
            const Text(
                '選んだフォルダの中に「kite_clips_日時」フォルダを作り、「001_動画名_区間1.mp4」の形で保存します。既存のファイルは上書きしません。',
                style: _note),
            if (widget.android) ...[
              const SizedBox(height: 6),
              const Text(
                  '保存中は画面を消灯させません。アプリを閉じたり別のアプリに切り替えたりすると、中断される場合があります。',
                  style: _note),
            ],
          ])),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('キャンセル')),
        FilledButton(
            onPressed: plan.isEmpty ? null : () => Navigator.pop(context, plan),
            child: const Text('保存先を選ぶ…')),
      ],
    );
  }
}

/// 一括保存の結果。失敗は動画名・区間・理由を並べる。
class BatchResultDialog extends StatelessWidget {
  const BatchResultDialog(
      {super.key, required this.summary, required this.folderLabel});
  final BatchSummary summary;
  final String folderLabel;

  String get title {
    if (summary.abortReason != null || summary.cancelled) return '一括保存を中止しました';
    if (summary.failed > 0) return '一部を保存できませんでした';
    return 'すべて保存しました';
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(title),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              Text(
                  '成功${summary.saved}件・失敗${summary.failed}件・スキップ${summary.skippedSaved}件・未実行${summary.notRun}件',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              SelectableText('保存先：$folderLabel'),
              if (summary.abortReason != null) ...[
                const SizedBox(height: 8),
                Text('中止の理由：${summary.abortReason}'),
              ],
              if (summary.cancelled) ...[
                const SizedBox(height: 8),
                const Text('中止の指示により、残りは保存していません。'),
              ],
              for (final failure in summary.failures) ...[
                const SizedBox(height: 8),
                SelectableText(
                    '${failure.entry.item.name} 区間${failure.entry.segmentNumber}：${failure.error}'),
              ],
              if (summary.failed > 0) ...[
                const SizedBox(height: 8),
                const Text('失敗した保存先に未完成のファイルが残る場合があります。自動では削除しません。',
                    style: _note),
              ],
            ])),
        actions: [
          FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('閉じる')),
        ],
      );
}
