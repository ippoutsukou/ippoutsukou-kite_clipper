import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';

import 'export_service.dart';
import 'editor_viewport.dart';
import 'editor_controls.dart';
import 'timeline_frames.dart';
import 'preview_readiness.dart';
import 'range_timeline.dart';
import 'time_entry.dart';
import 'clip_session.dart';
import 'clip_list.dart';
import 'segment_chips.dart';
import 'batch_plan.dart';
import 'batch_runner.dart';
import 'batch_dialogs.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  MediaKit.ensureInitialized();
  runApp(const KiteApp());
}

class KiteApp extends StatelessWidget {
  const KiteApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Kite Clipper',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff1266ed)),
          scaffoldBackgroundColor: const Color(0xfff5f7fa),
          fontFamily: 'Yu Gothic UI',
          sliderTheme: const SliderThemeData(
            activeTrackColor: Color(0xff1266ed),
            inactiveTrackColor: Color(0xffdce5f1),
            thumbColor: Color(0xff1266ed),
            rangeThumbShape: RoundRangeSliderThumbShape(enabledThumbRadius: 8),
            trackHeight: 3,
          ),
          outlinedButtonTheme: OutlinedButtonThemeData(
            style: OutlinedButton.styleFrom(
              backgroundColor: Colors.white,
              minimumSize: const Size(48, 44),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
          ),
          filledButtonTheme: FilledButtonThemeData(
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xff0766f7),
              minimumSize: const Size(48, 44),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
          ),
        ),
        home: const Workspace(),
      );
}

class Workspace extends StatefulWidget {
  const Workspace({super.key});
  @override
  State<Workspace> createState() => _WorkspaceState();
}

class _WorkspaceState extends State<Workspace> {
  final player = Player();
  late final VideoController controller;
  final subscriptions = <StreamSubscription<dynamic>>[];
  String? source;
  String? output;
  String? error;
  Uint8List? thumbnail;
  int durationMs = 0;
  RangeValues range = const RangeValues(0, 1);
  bool busy = false;
  bool loading = false;
  int? pendingSeek;
  Future<void>? seekTask;
  bool muted = false;
  final timelineLoader = TimelineLoader(extractTimelineFrame);
  List<Uint8List?> timelineFrames = List.filled(8, null);
  String? timelineSource;
  final session = ClipSession(caseInsensitivePaths: Platform.isWindows);
  StreamSubscription<String>? mediaError;
  // 一括保存中だけ値を持つ。batchProgress が null でなければ進捗欄を表示する。
  BatchRunner? batchRunner;
  String? batchProgress;
  double? batchFraction;

  void rememberActive() {
    final item = session.active;
    if (item == null || item.path != source) return;
    if (durationMs > 0) {
      item.setDuration(durationMs);
      item.startMs = range.start.round();
      item.endMs = range.end.round();
    }
    item.thumbnail = thumbnail;
    item.error = error;
    item.output = output;
  }

  void loadTimeline() {
    final path = source;
    if (path == null || durationMs <= 0 || timelineSource == path) return;
    timelineSource = path;
    unawaited(timelineLoader.load(path, durationMs, (index, bytes) {
      if (!mounted) return;
      setState(() {
        timelineFrames[index] = bytes;
        thumbnail ??= bytes;
        rememberActive();
      });
    }));
  }

  Future<void> togglePlayback() async {
    try {
      await seekTask;
      if (!mounted || busy || loading) return;
      await player.playOrPause();
    } catch (_) {
      if (mounted) setState(() => error = '再生操作を完了できませんでした。');
    }
  }

  Future<void> toggleMute() async {
    try {
      final next = !muted;
      await player.setVolume(next ? 0 : 100);
      if (mounted) setState(() => muted = next);
    } catch (_) {
      if (mounted) setState(() => error = '音声を切り替えられませんでした。');
    }
  }

  void previewAt(int milliseconds) {
    pendingSeek = milliseconds;
    seekTask ??= drainSeeks();
  }

  Future<void> drainSeeks() async {
    try {
      await player.pause();
      while (mounted && pendingSeek != null) {
        final target = pendingSeek!;
        pendingSeek = null;
        await player.seek(Duration(milliseconds: target));
      }
    } catch (_) {
      if (mounted) setState(() => error = 'プレビュー位置を変更できませんでした。');
    } finally {
      seekTask = null;
    }
  }

  @override
  void initState() {
    super.initState();
    // 初回のVideoウィジェット構築まで遅延させず、起動時に出力を接続する。
    controller = VideoController(player);
  }

  @override
  void dispose() {
    session.invalidate();
    mediaError?.cancel();
    timelineLoader.cancel();
    pendingSeek = null;
    for (final sub in subscriptions) {
      sub.cancel();
    }
    player.dispose();
    super.dispose();
  }

  Future<void> pick() async {
    if (busy || loading) return;
    rememberActive();
    setState(() => loading = true);
    try {
      final files = await openFiles(acceptedTypeGroups: [
        const XTypeGroup(
            label: '動画',
            extensions: ['mp4', 'mov', 'mkv', 'webm'],
            mimeTypes: ['video/*']),
      ]);
      if (!mounted || files.isEmpty) return;
      final added = session.add(files.map((file) => file.path).toList());
      setState(() {});
      if (session.active == null && added.isNotEmpty)
        await openClip(added.first);
    } on StateError {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('動画は最大30本です。追加する本数を減らしてください。')));
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('動画を追加できませんでした。')));
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> selectClip(ClipItem item) async {
    if (busy || loading) return;
    if (identical(session.active, item) && durationMs > 0 && error == null)
      return;
    rememberActive();
    setState(() => loading = true);
    try {
      await openClip(item);
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> openClip(ClipItem item) async {
    final ticket = session.select(item);
    bool current() => mounted && session.accepts(ticket, item);
    pendingSeek = null;
    timelineLoader.cancel();
    await mediaError?.cancel();
    mediaError = null;
    StreamSubscription<Duration>? durationSubscription;
    try {
      await seekTask;
      await player.stop();
      if (!current()) return;
      setState(() {
        source = item.path;
        durationMs = 0;
        range = const RangeValues(0, 1);
        error = null;
        output = item.output;
        thumbnail = item.thumbnail;
        timelineSource = null;
        timelineFrames = List.filled(8, null);
      });
      final length = Completer<Duration>();
      durationSubscription = player.stream.duration.listen((value) {
        if (current() && value.inMilliseconds > 0 && !length.isCompleted)
          length.complete(value);
      });
      mediaError = player.stream.error.listen((_) {
        if (current())
          setState(() {
            error = '再生できません。別の動画形式で試してください。';
            rememberActive();
          });
      });
      await openPreparedPreview(
        ready: () async {
          await controller.platform.future;
          await WidgetsBinding.instance.endOfFrame;
        }(),
        isActive: current,
        open: () => player.open(Media(item.path), play: false),
      );
      if (!current()) return;
      final duration = await length.future.timeout(const Duration(seconds: 20));
      if (!current()) return;
      item.setDuration(duration.inMilliseconds);
      setState(() {
        durationMs = item.durationMs!;
        range = RangeValues(item.startMs!.toDouble(), item.endMs!.toDouble());
        output = item.output;
      });
      await player.seek(Duration(milliseconds: item.startMs!));
      if (!current()) return;
      rememberActive();
      loadTimeline();
    } catch (_) {
      if (current())
        setState(() {
          source = item.path;
          durationMs = 0;
          error = '動画を読み込めませんでした。一覧から別の動画を選ぶか、同じ項目を選んで再試行してください。';
          rememberActive();
        });
    } finally {
      await durationSubscription?.cancel();
    }
  }

  Future<void> showClipPicker() async {
    if (busy || loading) return;
    final chosen = await showModalBottomSheet<ClipItem>(
        context: context,
        isScrollControlled: true,
        builder: (context) => SafeArea(
                child: SizedBox(
              height: MediaQuery.sizeOf(context).height * 0.7,
              child: StatefulBuilder(
                  builder: (sheetContext, updateSheet) => ClipList(
                      items: session.items,
                      selected: session.active,
                      enabled: !busy && !loading,
                      onSelect: (item) => Navigator.pop(context, item),
                      onMove: (item, delta) {
                        moveClip(item, delta);
                        updateSheet(() {});
                      },
                      onRemove: (item) {
                        Navigator.pop(context);
                        unawaited(removeClip(item));
                      })),
            )));
    if (chosen != null && mounted) await selectClip(chosen);
  }

  void moveClip(ClipItem item, int delta) {
    if (busy || loading || !session.items.contains(item)) return;
    rememberActive();
    setState(() => session.move(item, delta));
  }

  Future<void> removeClip(ClipItem item) async {
    if (busy || loading || !session.items.contains(item)) return;
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('一覧から外しますか？'),
              content: SingleChildScrollView(
                  child: Text(
                      '${item.name}\n\nこの動画の編集範囲は一覧から失われます。元動画と保存済みファイルは削除しません。')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('キャンセル')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('一覧から外す')),
              ],
            ));
    if (!mounted ||
        confirmed != true ||
        busy ||
        loading ||
        !session.items.contains(item)) return;
    rememberActive();
    if (!identical(session.active, item)) {
      setState(() => session.remove(item));
    } else {
      setState(() => loading = true);
      session.invalidate();
      timelineLoader.cancel();
      pendingSeek = null;
      try {
        await mediaError?.cancel();
        mediaError = null;
        await seekTask;
        await player.stop();
        if (!mounted) return;
        session.remove(item);
        final next = session.active;
        if (next != null) {
          await openClip(next);
        } else {
          setState(() {
            source = null;
            output = null;
            error = null;
            thumbnail = null;
            durationMs = 0;
            range = const RangeValues(0, 1);
            timelineSource = null;
            timelineFrames = List.filled(8, null);
          });
        }
      } catch (_) {
        if (mounted) setState(() => error = '動画を停止できなかったため、一覧の変更を完了できませんでした。');
      } finally {
        if (mounted) setState(() => loading = false);
      }
    }
    if (mounted && !session.items.contains(item)) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('一覧から外しました。元動画・保存済みファイルはそのままです。')));
    }
  }

  Future<void> save() async {
    if (busy || loading || source == null || durationMs <= 0) return;
    setState(() {
      busy = true;
      error = null;
      output = null;
    });
    try {
      String? directory;
      if (Platform.isWindows) {
        directory = await getDirectoryPath(confirmButtonText: 'ここに保存');
        if (directory == null) return;
      }
      await player.pause();
      final result = await ExportService().export(
        source!,
        ClipRange(range.start.round(), range.end.round()),
        durationMs,
        directory: directory,
      );
      if (mounted && result != null) setState(() => output = result);
    } catch (e) {
      if (mounted) setState(() => error = '保存できませんでした: $e');
    } finally {
      if (mounted)
        setState(() {
          busy = false;
          rememberActive();
        });
    }
  }

  bool get hasBatchTargets =>
      session.items.any((item) => item.segments.isNotEmpty);

  /// 全動画の全区間を、選んだフォルダへ1件ずつ保存する。
  Future<void> saveAll() async {
    if (busy || loading) return;
    rememberActive();
    final plan = await showDialog<BatchPlan>(
        context: context,
        builder: (_) => BatchConfirmDialog(
            items: session.items, android: Platform.isAndroid));
    if (!mounted || plan == null || busy || loading) return;
    final service = ExportService();
    setState(() {
      busy = true;
      error = null;
      batchProgress = '保存先を選んでいます…';
      batchFraction = null;
    });
    BatchFolder? folder;
    try {
      await player.pause();
      final now = DateTime.now();
      if (Platform.isAndroid) {
        folder = await service.pickAndroidBatchFolder(now);
      } else {
        final parent = await getDirectoryPath(confirmButtonText: 'ここに保存');
        if (parent != null) {
          folder = await service.createBatchFolder(parent, now);
        }
      }
    } catch (e) {
      error = '保存先フォルダを用意できませんでした：${describeBatchError(e)}';
    }
    if (!mounted) return;
    if (folder == null) {
      setState(() {
        busy = false;
        batchProgress = null;
      });
      return;
    }
    final target = folder;
    final total = plan.entries.length;
    var done = 0;
    final runner = BatchRunner((entry) => service.exportInto(
        target,
        entry.fileName,
        entry.item.path,
        ClipRange(entry.segment.startMs, entry.segment.endMs),
        entry.item.durationMs!));
    setState(() {
      batchRunner = runner;
      batchProgress = '保存を始めています…';
      batchFraction = 0;
    });
    try {
      await service.keepScreenOn(true);
    } catch (_) {
      // 消灯防止に失敗しても保存自体は続ける。
    }
    BatchSummary? summary;
    try {
      summary = await runner.run(plan, onStart: (index, entry) {
        if (!mounted || runner.cancelRequested) return;
        setState(() {
          batchProgress =
              '${index + 1} / $total 件目を保存中：${entry.item.name} 区間${entry.segmentNumber}';
          batchFraction = index / total;
        });
      }, onResult: (result) {
        if (!mounted) return;
        setState(() {
          batchFraction = ++done / total;
          // 画面側の output は rememberActive で選択中区間へ書き戻されるため、ここで揃える。
          if (result.status == BatchStatus.saved &&
              identical(result.entry.segment, editing?.current)) {
            output = result.output;
          }
        });
      });
    } catch (e) {
      error = '一括保存を完了できませんでした：${describeBatchError(e)}';
    } finally {
      unawaited(service.keepScreenOn(false).catchError((_) {}));
      if (mounted) {
        setState(() {
          busy = false;
          batchRunner = null;
          batchProgress = null;
          batchFraction = null;
          if (editing != null) output = editing!.output;
        });
      }
    }
    final result = summary;
    if (mounted && result != null) {
      await showDialog<void>(
          context: context,
          builder: (_) =>
              BatchResultDialog(summary: result, folderLabel: target.label));
    }
  }

  /// 保存中の1件は止めず、終わった時点で残りを取りやめる。
  void cancelBatch() {
    final runner = batchRunner;
    if (runner == null || !runner.running) return;
    runner.cancel();
    setState(() => batchProgress = '中止しています…（保存中の1件が終わるまで待っています）');
  }

  String time(double ms) {
    final n = ms.round();
    return '${(n ~/ 60000).toString().padLeft(2, '0')}:${((n ~/ 1000) % 60).toString().padLeft(2, '0')}.${(n % 1000).toString().padLeft(3, '0')}';
  }

  Widget candidates(bool compact) => Container(
        width: compact
            ? null
            : (MediaQuery.sizeOf(context).width * 0.24).clamp(260.0, 360.0),
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: const Color(0xffe4e9f0))),
        child: ClipList(
            items: session.items,
            selected: session.active,
            enabled: !busy && !loading,
            onSelect: selectClip,
            onMove: moveClip,
            onRemove: removeClip),
      );

  Widget editor() => EditorViewport(
        preview: ClipRRect(
          borderRadius: BorderRadius.circular(5),
          child: Video(
              controller: controller,
              fit: BoxFit.contain,
              controls: NoVideoControls),
        ),
        controls: [
          StreamBuilder<Duration>(
            stream: player.stream.position,
            initialData: player.state.position,
            builder: (context, position) => StreamBuilder<bool>(
              stream: player.stream.playing,
              initialData: player.state.playing,
              builder: (context, playing) => PlaybackBar(
                positionMs: position.data?.inMilliseconds ?? 0,
                durationMs: durationMs,
                playing: playing.data ?? false,
                muted: muted,
                enabled: !busy && !loading && durationMs > 0,
                onToggle: togglePlayback,
                onMute: toggleMute,
                onSeek: (value) => previewAt(
                    value.clamp(0, durationMs > 0 ? durationMs - 1 : 0)),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const Icon(Icons.content_cut, size: 18),
              const SizedBox(width: 8),
              const Text('保存する範囲',
                  style: TextStyle(fontWeight: FontWeight.w600)),
              const Spacer(),
              Text(
                  '${((range.end - range.start) / 1000).toStringAsFixed(1)} 秒'),
            ],
          ),
          const SizedBox(height: 6),
          RangeTimeline(
            frames: timelineFrames,
            values: range,
            durationMs: durationMs,
            others: [
              if (editing != null)
                for (var i = 0; i < editing!.segments.length; i++)
                  if (i != editing!.activeSegment)
                    RangeValues(editing!.segments[i].startMs.toDouble(),
                        editing!.segments[i].endMs.toDouble()),
            ],
            compact: MediaQuery.sizeOf(context).height < 700,
            onChanged: busy || loading || durationMs == 0
                ? null
                : (v) {
                    if (v.end - v.start < 100) return;
                    final target = rangePreviewPosition(range, v, durationMs);
                    setState(() {
                      range = v;
                      output = null;
                      rememberActive();
                    });
                    previewAt(target);
                  },
          ),
          if (editing != null) ...[
            const SizedBox(height: 6),
            SegmentChips(
              segments: editing!.segments,
              selected: editing!.activeSegment,
              enabled: segmentsEnabled,
              onSelect: selectSegment,
              onAdd: addSegment,
              onRemove: (index) => unawaited(removeSegment(index)),
            ),
          ],
          const SizedBox(height: 6),
          Wrap(
            spacing: 16,
            runSpacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              _timeBox('開始', time(range.start)),
              _timeBox('終了', time(range.end)),
              const Text(
                '高速コピーでは境界がずれる場合があります',
                style: TextStyle(color: Colors.blueGrey, fontSize: 12),
              ),
            ],
          ),
        ],
      );

  Future<void> editTime(bool start) async {
    if (busy || loading || durationMs <= 0) return;
    final currentSource = source;
    final value = await showDialog<int>(
        context: context,
        builder: (_) => TimeEntryDialog(
            start: start,
            initial: time(start ? range.start : range.end),
            other: (start ? range.end : range.start).round(),
            duration: durationMs));
    if (!mounted || value == null || busy || loading || source != currentSource)
      return;
    setState(() {
      range = start
          ? RangeValues(value.toDouble(), range.end)
          : RangeValues(range.start, value.toDouble());
      output = null;
      rememberActive();
    });
    previewAt(value.clamp(0, durationMs - 1));
  }

  /// 編集中の動画。長さ確定前・読み込み失敗時は区間操作を受け付けない。
  ClipItem? get editing {
    final item = session.active;
    return item != null &&
            item.path == source &&
            durationMs > 0 &&
            item.segments.isNotEmpty
        ? item
        : null;
  }

  bool get segmentsEnabled => !busy && !loading && editing != null;

  /// 区間の切替は動画を開き直さず、範囲の差し替えとシークだけ行う。
  void showSegment(ClipItem item) {
    setState(() {
      range = RangeValues(item.startMs!.toDouble(), item.endMs!.toDouble());
      output = item.output;
    });
    previewAt(item.startMs!.clamp(0, durationMs - 1));
  }

  void selectSegment(int index) {
    final item = editing;
    if (!segmentsEnabled || item == null || index == item.activeSegment) return;
    rememberActive();
    item.selectSegment(index);
    showSegment(item);
  }

  void addSegment() {
    final item = editing;
    if (!segmentsEnabled || item == null) return;
    rememberActive();
    try {
      item.addSegment(player.state.position.inMilliseconds);
    } on StateError catch (e) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e.message)));
      return;
    }
    showSegment(item);
  }

  Future<void> removeSegment(int index) async {
    final item = editing;
    if (!segmentsEnabled || item == null || item.segments.length <= 1) return;
    rememberActive();
    final target = item.segments[index];
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text('区間${index + 1}を削除しますか？'),
              content: const SingleChildScrollView(
                  child: Text('この区間の範囲は失われます。元動画と保存済みファイルは削除しません。')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('キャンセル')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('この区間を削除')),
              ],
            ));
    // 確認中に動画切替・保存・他の削除が起きていたら何もしない。
    final now = item.segments.indexOf(target);
    if (!mounted ||
        confirmed != true ||
        !segmentsEnabled ||
        !identical(editing, item) ||
        now < 0 ||
        item.segments.length <= 1) return;
    item.removeSegment(now);
    showSegment(item);
  }

  Widget _timeBox(String label, String value) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label),
          const SizedBox(width: 8),
          OutlinedButton(
            onPressed: busy || loading || durationMs <= 0
                ? null
                : () => editTime(label == '開始'),
            style: OutlinedButton.styleFrom(
                minimumSize: const Size(0, 36),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                foregroundColor: const Color(0xff14213d),
                side: const BorderSide(color: Color(0xffd3d9e3))),
            child: Text(value, semanticsLabel: '$label時刻 $value を編集'),
          ),
        ],
      );

  @override
  Widget build(BuildContext context) => PopScope(
      // 一括保存中に戻る操作で画面を閉じると保存が中断されるため止める。
      canPop: batchRunner == null,
      child: Scaffold(
        appBar: AppBar(
          backgroundColor: Colors.white,
          surfaceTintColor: Colors.white,
          leading: source != null &&
                  (Platform.isAndroid || MediaQuery.sizeOf(context).width < 800)
              ? IconButton(
                  tooltip: '動画一覧（${session.items.length}本）',
                  onPressed: busy || loading ? null : showClipPicker,
                  icon: const Icon(Icons.video_library_outlined,
                      color: Color(0xff1664dd)))
              : const Icon(Icons.sailing, color: Color(0xff1664dd)),
          title: Text(source == null ? 'Kite Clipper' : 'クリップを確認',
              style: const TextStyle(
                  fontWeight: FontWeight.w700, color: Color(0xff14213d))),
          centerTitle: true,
          actions: [
            TextButton(
              onPressed: busy || loading ? null : pick,
              child: const Text('動画を追加…'),
            ),
            const SizedBox(width: 8),
          ],
        ),
        body: Column(
          children: [
            if (batchProgress != null)
              Container(
                color: const Color(0xffedf5ff),
                padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(children: [
                      Expanded(
                          child: Text(batchProgress!,
                              maxLines: 2, overflow: TextOverflow.ellipsis)),
                      if (batchRunner != null)
                        TextButton(
                            onPressed: batchRunner!.cancelRequested
                                ? null
                                : cancelBatch,
                            child: const Text('中止')),
                    ]),
                    const SizedBox(height: 4),
                    LinearProgressIndicator(value: batchFraction),
                  ],
                ),
              )
            else if (busy || loading)
              const LinearProgressIndicator(),
            if (error != null)
              EditorNotice(
                failed: true,
                details: error!,
                onDismiss: () => setState(() => error = null),
              ),
            if (output != null && error == null)
              EditorNotice(
                failed: false,
                details:
                    '区間${(editing?.activeSegment ?? 0) + 1}を保存しました。再生して範囲と音声を確認してください。\n$output',
                onDismiss: () => setState(() => output = null),
              ),
            Expanded(
              child: source == null
                  ? Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(
                            Icons.video_library_outlined,
                            size: 64,
                            color: Color(0xff2374e8),
                          ),
                          const SizedBox(height: 20),
                          const Text(
                            '動画から、残したい瞬間を。',
                            style: TextStyle(
                              fontSize: 24,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: 12),
                          const Text('動画を選んで、確認・範囲調整・保存'),
                          const SizedBox(height: 28),
                          FilledButton.icon(
                            onPressed: loading ? null : pick,
                            icon: const Icon(Icons.add),
                            label: const Text('動画を追加…'),
                          ),
                        ],
                      ),
                    )
                  : LayoutBuilder(
                      builder: (context, box) {
                        final compact =
                            Platform.isAndroid || box.maxWidth < 800;
                        return Padding(
                          padding: const EdgeInsets.all(16),
                          child: compact
                              ? Column(
                                  children: [
                                    Expanded(child: editor()),
                                  ],
                                )
                              : Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    candidates(false),
                                    const SizedBox(width: 16),
                                    Expanded(child: editor()),
                                  ],
                                ),
                        );
                      },
                    ),
            ),
            Container(
              color: Colors.white,
              padding: const EdgeInsets.all(16),
              child: SafeArea(
                top: false,
                child: LayoutBuilder(builder: (context, box) {
                  final label = Text(
                    busy
                        ? '保存中…'
                        : '${session.active?.name ?? "選択中の動画"} ・ 区間${(editing?.activeSegment ?? 0) + 1}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  );
                  final saveAllButton = OutlinedButton.icon(
                    onPressed:
                        busy || loading || !hasBatchTargets ? null : saveAll,
                    icon: const Icon(Icons.library_add_check_outlined,
                        size: 18),
                    label: const Text('すべて保存…'),
                  );
                  final saveButton = FilledButton.icon(
                    onPressed:
                        source == null || busy || loading || durationMs <= 0
                            ? null
                            : save,
                    icon: const Icon(Icons.save_alt, size: 18),
                    label: const Text('この区間を保存'),
                  );
                  // 狭い画面では名前とボタンを2段にして、ボタン文字の見切れを防ぐ。
                  if (box.maxWidth < 560) {
                    return Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        label,
                        const SizedBox(height: 8),
                        Row(children: [
                          Expanded(child: saveAllButton),
                          const SizedBox(width: 8),
                          Expanded(child: saveButton),
                        ]),
                      ],
                    );
                  }
                  return Row(children: [
                    Expanded(child: label),
                    saveAllButton,
                    const SizedBox(width: 8),
                    saveButton,
                  ]);
                }),
              ),
            ),
          ],
        ),
      ));
}
