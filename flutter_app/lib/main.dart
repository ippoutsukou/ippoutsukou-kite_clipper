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
import 'session_store.dart';
import 'ai_segments.dart';
import 'analysis_service.dart';
import 'analysis_dialogs.dart';
import 'video_picker.dart';
import 'omakase.dart';
import 'merge_plan.dart';
import 'merge_service.dart';
import 'merge_dialogs.dart';

/// ビルド時に --dart-define=BUILD_LABEL=... で埋め込む。未指定なら dev。
const buildLabel = String.fromEnvironment('BUILD_LABEL', defaultValue: 'dev');

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
  // 起動時に前回の作業記録から差し替えるため final にしない。
  ClipSession session = ClipSession(caseInsensitivePaths: Platform.isWindows);
  // 作業記録。場所を決められない・前回の記録を読めない場合は null（記録しない）。
  SessionStore? sessionStore;
  Timer? saveTimer;
  Future<void>? saving;
  bool saveFailureShown = false;
  // AI解析の測定用。最後に選んだ競技を覚えておく。
  String sportId = sportModelIds.first;
  // おまかせモードの設定（作業記録に残す）。
  OmakaseSettings omakase = const OmakaseSettings();
  late final AppLifecycleListener lifecycle;
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
    // バックグラウンドへ移る・閉じる直前に作業記録を書き出す。
    lifecycle = AppLifecycleListener(onStateChange: (state) {
      if (state != AppLifecycleState.resumed) unawaited(flushSave());
    });
    loading = true;
    unawaited(restoreSession());
  }

  @override
  void dispose() {
    saveTimer?.cancel();
    lifecycle.dispose();
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

  /// 画面の状態が変わるたびに作業記録の書き出しを予約する（連続した変更は0.5秒でまとめる）。
  @override
  void setState(VoidCallback fn) {
    super.setState(fn);
    scheduleSave();
  }

  void scheduleSave() {
    if (sessionStore == null) return;
    saveTimer?.cancel();
    saveTimer = Timer(
        const Duration(milliseconds: 500), () => unawaited(flushSave()));
  }

  Future<void> flushSave() {
    saveTimer?.cancel();
    saveTimer = null;
    final store = sessionStore;
    if (store == null) return Future<void>.value();
    if (mounted) rememberActive();
    // 書き込みを直列にし、後から始めた保存が常に最新の状態を書く。
    final next =
        (saving ?? Future<void>.value())
            .then((_) => store.save(session, omakase: omakase));
    saving = next.catchError((Object _) {
      if (mounted && !saveFailureShown) {
        saveFailureShown = true;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('作業内容を記録できませんでした。次回起動時に復元されない場合があります。')));
      }
    });
    return saving!;
  }

  /// 前回の一覧と区間を戻し、前回選んでいた動画（元動画があるもの）を開く。
  Future<void> restoreSession() async {
    try {
      final store = await SessionStore.open();
      if (store == null || !mounted) return;
      final restored =
          await store.load(caseInsensitivePaths: Platform.isWindows);
      if (!mounted) return;
      sessionStore = store;
      setState(() => omakase = restored.omakase);
      final notes = [
        if (restored.warning != null) restored.warning!,
        if (restored.skipped > 0)
          '読み込めなかった動画${restored.skipped}本は一覧に含めていません。',
      ];
      if (notes.isNotEmpty) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(notes.join('\n'))));
      }
      if (restored.session.items.isEmpty || session.items.isNotEmpty) return;
      setState(() => session = restored.session);
      ClipItem? target = session.active;
      if (target == null || target.error != null) {
        target = null;
        for (final item in session.items) {
          if (item.error == null) {
            target = item;
            break;
          }
        }
      }
      if (target != null) {
        await openClip(target);
      } else {
        // 元動画が1本も見つからなくても、一覧を見て外せるよう編集画面を出す。
        final first = session.items.first;
        session.select(first);
        setState(() {
          source = first.path;
          error = first.error;
          output = null;
          thumbnail = null;
          durationMs = 0;
        });
      }
    } catch (_) {
      // 読めない理由が分からないまま前回の記録を上書きしないよう、今回は記録しない。
      sessionStore = null;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('前回の作業を読み込めませんでした。今回の作業内容は記録されません。')));
      }
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> pick() async {
    if (busy || loading) return;
    rememberActive();
    setState(() => loading = true);
    var added = <ClipItem>[];
    try {
      final paths = await VideoPicker().pick();
      if (!mounted || paths.isEmpty) return;
      added = session.add(paths);
      setState(() {});
      if (session.active == null && added.isNotEmpty)
        await openClip(added.first);
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('動画を追加できませんでした。')));
    } finally {
      if (mounted) setState(() => loading = false);
    }
    // おまかせONなら、今回追加した動画だけを順に解析して区間を入れる。
    if (mounted && omakase.enabled && added.isNotEmpty) await runOmakase(added);
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
                      },
                      onInclude: (item) {
                        includeWhole(item);
                        updateSheet(() {});
                      },
                      onRemoveMany: (items) {
                        Navigator.pop(context);
                        unawaited(removeClips(items));
                      })),
            )));
    if (chosen != null && mounted) await selectClip(chosen);
  }

  /// 提案0個で外した動画を、全体のまま保存・まとめる対象に戻す。
  void includeWhole(ClipItem item) {
    if (busy || loading || !session.items.contains(item)) return;
    setState(() => item.noProposals = false);
  }

  void moveClip(ClipItem item, int delta) {
    if (busy || loading || !session.items.contains(item)) return;
    rememberActive();
    setState(() => session.move(item, delta));
  }

  Future<void> removeClip(ClipItem item) => removeClips([item]);

  /// 動画を一覧から外す（1本でも複数でも同じ流れ）。元動画・保存済みファイルは消さない。
  Future<void> removeClips(List<ClipItem> requested) async {
    List<ClipItem> targets() =>
        [for (final item in requested) if (session.items.contains(item)) item];
    if (busy || loading || targets().isEmpty) return;
    final shown = targets();
    const namesShown = 5;
    final names = [
      for (final item in shown.take(namesShown)) item.name,
      if (shown.length > namesShown) 'ほか${shown.length - namesShown}本',
    ].join('\n');
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text(shown.length == 1
                  ? '一覧から外しますか？'
                  : '${shown.length}本を一覧から外しますか？'),
              content: SingleChildScrollView(
                  child: Text(
                      '$names\n\n${shown.length == 1 ? 'この動画' : 'これらの動画'}の編集範囲は一覧から失われます。元動画と保存済みファイルは削除しません。')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('キャンセル')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: Text(shown.length == 1
                        ? '一覧から外す'
                        : '${shown.length}本を外す')),
              ],
            ));
    // 確認中に一覧が変わっていても、残っている動画だけを外す。
    final removing = targets();
    if (!mounted ||
        confirmed != true ||
        busy ||
        loading ||
        removing.isEmpty) return;
    rememberActive();
    final active = session.active;
    if (active == null || !removing.contains(active)) {
      setState(() {
        for (final item in removing) {
          session.remove(item);
        }
      });
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
        // 開いている動画は最後に外し、残った動画の中から次を選ばせる。
        for (final item in removing) {
          if (!identical(item, active)) session.remove(item);
        }
        session.remove(active);
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
    final removed = removing.where((item) => !session.items.contains(item)).length;
    if (mounted && removed > 0) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(
              '${removing.length == 1 ? '' : '$removed本を'}一覧から外しました。元動画・保存済みファイルはそのままです。')));
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

  /// AI候補の提案。競技と間隔を選び、端末内で解析して区間を提案する。
  /// 同じ条件の保存済み結果があれば解析せずに使う。
  Future<void> runAnalysis() async {
    final item = editing;
    if (busy || loading || item == null) return;
    if (!AnalysisService.supported) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('AI解析は現在 Android のみ対応しています。')));
      return;
    }
    final saved = item.analysis;
    final request = await showDialog<AnalysisRequest>(
        context: context,
        builder: (_) => AnalysisStartDialog(
            sportId: saved?.sportId ?? sportId,
            savedSportId: saved != null && !saved.partial ? saved.sportId : null,
            savedIntervalMs:
                saved != null && !saved.partial ? saved.intervalMs : null));
    if (!mounted || request == null || busy || loading || editing != item) return;
    setState(() {
      sportId = request.sportId;
      busy = true;
      error = null;
    });

    SportModel? model;
    var models = <SportModel>[];
    AnalysisResult? result;
    var fromSaved = false;
    final service = AnalysisService();
    final progress = ValueNotifier<List<int>>(const [0, 0]);
    final cancelling = ValueNotifier<bool>(false);
    var dialogOpen = false;
    try {
      // 全競技モデルを読み込み、同じ取り出し結果でまとめて採点する。
      models = [for (final id in sportModelIds) await loadSportModel(id)];
      model = models.firstWhere((m) => m.id == request.sportId,
          orElse: () => models.first);
      final reusable = saved != null &&
          !request.reanalyze &&
          saved.matches(
              sportId: request.sportId,
              intervalMs: request.intervalMs,
              durationMs: durationMs);
      if (reusable) {
        fromSaved = true;
        result = AnalysisResult(
            durationMs: saved.durationMs,
            timesMs: saved.timesMs,
            scores: saved.scores,
            modelMs: 0,
            extractMs: 0,
            inferMs: 0,
            totalMs: 0,
            truncated: false,
            cancelled: false);
      } else {
        await stopPlaybackForAnalysis();
        if (!mounted) return;
        dialogOpen = true;
        unawaited(showDialog<void>(
            context: context,
            barrierDismissible: false,
            builder: (_) => ValueListenableBuilder<List<int>>(
                valueListenable: progress,
                builder: (_, counts, __) => ValueListenableBuilder<bool>(
                    valueListenable: cancelling,
                    builder: (_, stopping, __) => AnalysisProgressDialog(
                        done: counts[0],
                        total: counts[1],
                        cancelling: stopping,
                        onCancel: () {
                          cancelling.value = true;
                          unawaited(service.cancel());
                        })))));
        final interval = resolveIntervalMs(
            durationMs: durationMs, wantedMs: request.intervalMs);
        result = await service.analyze(
            source: item.path,
            model: model,
            others: [for (final m in models) if (m.id != model.id) m],
            intervalMs: interval,
            onProgress: (done, total) => progress.value = [done, total]);
        item.analysis = AnalysisRecord(
            sportId: request.sportId,
            intervalMs: interval,
            durationMs: result.durationMs,
            timesMs: result.timesMs,
            scores: result.scores,
            partial: result.cancelled || result.truncated);
      }
    } catch (e) {
      error = 'AI解析に失敗しました：${describeBatchError(e)}';
    } finally {
      if (mounted) {
        if (dialogOpen) Navigator.of(context, rootNavigator: true).pop();
        setState(() => busy = false);
      }
      progress.dispose();
      cancelling.dispose();
    }
    // 解析で再生を止めた場合は、元の動画・範囲へ戻す。
    if (!fromSaved && mounted && session.items.contains(item)) {
      setState(() => loading = true);
      try {
        await openClip(item);
      } finally {
        if (mounted) setState(() => loading = false);
      }
    }
    if (!mounted || result == null || model == null) return;
    final proposals = buildProposedSegments(
        timesMs: result.timesMs,
        scores: result.scores,
        model: model,
        durationMs: result.durationMs);
    // 最後まで解析して0個なら、全長のまま保存・結合されないよう対象から外す（おまかせと同じ）。
    if (session.items.contains(item)) {
      setState(() => item.noProposals = proposals.isEmpty &&
          !(result!.cancelled || result.truncated));
    }
    // 他の競技モデルでいくつ提案できたかを数える（採点済みの点数を使うだけなので軽い）。
    final otherCounts = <String, int>{};
    for (final other in models) {
      final otherScores = result.scoresByModel[other.id];
      if (other.id == model.id || otherScores == null) continue;
      otherCounts[other.id] = buildProposedSegments(
              timesMs: result.timesMs,
              scores: otherScores,
              model: other,
              durationMs: result.durationMs)
          .length;
    }
    await showDialog<void>(
        context: context,
        builder: (_) => AnalysisResultDialog(
            result: result!,
            segments: proposals,
            sportId: request.sportId,
            fromSaved: fromSaved,
            otherCounts: otherCounts,
            onApply: () => applyProposals(item, proposals)));
  }

  /// 4K動画では再生用と解析用のデコーダーが同時に動くと落ちる端末があるため、解析前に止める。
  Future<void> stopPlaybackForAnalysis() async {
    timelineLoader.cancel();
    pendingSeek = null;
    await seekTask;
    await player.stop();
  }

  /// おまかせモードのON/OFF。ONにするときは競技と間隔を選ぶ。
  Future<void> toggleOmakase() async {
    if (busy || loading) return;
    if (omakase.enabled) {
      setState(() => omakase = OmakaseSettings(
          sportId: omakase.sportId, intervalMs: omakase.intervalMs));
      return;
    }
    final request = await showDialog<AnalysisRequest>(
        context: context,
        builder: (_) => AnalysisStartDialog(
            sportId: omakase.sportId,
            intervalMs: omakase.intervalMs,
            title: 'おまかせモード',
            confirmLabel: 'ONにする',
            note: 'ONの間に「動画を追加…」で追加した動画を、この条件で1本ずつ解析して区間を入れます。'
                '保存はしません。すでに一覧にある動画は対象外です。'));
    if (!mounted || request == null) return;
    setState(() => omakase = OmakaseSettings(
        enabled: true, sportId: request.sportId, intervalMs: request.intervalMs));
  }

  /// おまかせ：追加した動画を1本ずつ解析し、提案区間を入れる。保存はしない。
  Future<void> runOmakase(List<ClipItem> targets) async {
    if (busy || loading || targets.isEmpty || !AnalysisService.supported) {
      return;
    }
    final settings = omakase;
    rememberActive();
    setState(() {
      busy = true;
      error = null;
    });
    final service = AnalysisService();
    final progress = ValueNotifier<List<int>>(const [0, 0]);
    final heading = ValueNotifier<String>('');
    final cancelling = ValueNotifier<bool>(false);
    final outcomes = <OmakaseOutcome>[];
    final withSegments = <ClipItem>[];
    var stopped = false;
    var dialogOpen = false;
    try {
      final models = [for (final id in sportModelIds) await loadSportModel(id)];
      final model = models.firstWhere((m) => m.id == settings.sportId,
          orElse: () => models.first);
      await stopPlaybackForAnalysis();
      stopped = true;
      if (!mounted) return;
      dialogOpen = true;
      unawaited(showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => ListenableBuilder(
              listenable: Listenable.merge([progress, heading, cancelling]),
              builder: (_, __) => AnalysisProgressDialog(
                  heading: heading.value,
                  done: progress.value[0],
                  total: progress.value[1],
                  cancelling: cancelling.value,
                  onCancel: () {
                    cancelling.value = true;
                    unawaited(service.cancel());
                  }))));
      for (var index = 0; index < targets.length; index++) {
        final item = targets[index];
        if (cancelling.value || !session.items.contains(item)) {
          outcomes.add(OmakaseOutcome(item.name, OmakaseStatus.skipped));
          continue;
        }
        heading.value = 'おまかせ ${index + 1}/${targets.length}本目：${item.name}';
        progress.value = const [0, 0];
        try {
          final interval = resolveIntervalMs(
              durationMs: item.durationMs ?? 0, wantedMs: settings.intervalMs);
          final result = await service.analyze(
              source: item.path,
              model: model,
              others: [for (final m in models) if (m.id != model.id) m],
              intervalMs: interval,
              onProgress: (done, total) => progress.value = [done, total]);
          if (!mounted) return;
          final outcome = applyOmakaseResult(
              item: item,
              result: result,
              model: model,
              sportId: settings.sportId,
              intervalMs: interval);
          outcomes.add(outcome);
          if (outcome.status == OmakaseStatus.added) withSegments.add(item);
          setState(() => syncEditorFrom(item));
        } catch (e) {
          outcomes.add(OmakaseOutcome(item.name, OmakaseStatus.failed,
              message: describeBatchError(e)));
        }
      }
    } catch (e) {
      error = 'おまかせを開始できませんでした：${describeBatchError(e)}';
    } finally {
      if (mounted) {
        if (dialogOpen) Navigator.of(context, rootNavigator: true).pop();
        setState(() => busy = false);
      }
      progress.dispose();
      heading.dispose();
      cancelling.dispose();
    }
    if (!mounted) return;
    // 区間が入った最初の動画を開く。なければ止める前の動画へ戻す。
    final target = withSegments
            .where((item) => session.items.contains(item))
            .firstOrNull ??
        session.active;
    if (stopped && target != null) {
      setState(() => loading = true);
      try {
        await openClip(target);
      } finally {
        if (mounted) setState(() => loading = false);
      }
    }
    if (!mounted || outcomes.isEmpty) return;
    await showDialog<void>(
        context: context,
        builder: (_) =>
            OmakaseResultDialog(sportId: settings.sportId, outcomes: outcomes));
  }

  /// 開いている動画の区間を外から変えたとき、画面の範囲を合わせる。
  /// 合わせないと、次の作業記録の書き出しで古い範囲が区間へ書き戻される。
  void syncEditorFrom(ClipItem item) {
    if (!identical(session.active, item) || item.path != source) return;
    final start = item.startMs, end = item.endMs, duration = item.durationMs;
    if (start == null || end == null || duration == null) return;
    durationMs = duration;
    range = RangeValues(start.toDouble(), end.toDouble());
    output = item.output;
  }

  /// 提案区間を一覧へ反映する。触っていない初期区間は置き換える。
  void applyProposals(ClipItem item, List<ProposedSegment> proposals) {
    if (busy || loading || !identical(editing, item) || proposals.isEmpty) return;
    rememberActive();
    final applied = applyProposedSegments(item, proposals);
    setState(() {});
    showSegment(item);
    final notes = [
      '${applied.added}個の区間を追加しました',
      if (applied.replaced) '最初からあった区間は置き換えました',
      if (applied.skipped > 0) '${applied.skipped}個は上限のため追加していません',
    ];
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(notes.join('。'))));
  }

  /// 複数の区間を順につないで1本にする。元動画・既存の保存ファイルは変更しない。
  Future<void> runMerge() async {
    if (busy || loading) return;
    rememberActive();
    final plan = MergePlan.from(session.items);
    if (!plan.canMerge) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('つなぐには区間が2個以上必要です。'
              '${plan.noProposalVideos > 0 ? 'AIの提案がなかった動画${plan.noProposalVideos}本は対象外です。' : ''}')));
      return;
    }
    // Windowsは先に元動画の形式を調べ、違いがあればつなぎ方を選んでもらう。
    final formats = await MergeService().inspectWindows(plan.entries);
    if (!mounted || busy || loading) return;
    final mode = await showDialog<MergeMode>(
        context: context,
        builder: (_) => MergeConfirmDialog(
            plan: plan, android: Platform.isAndroid, formats: formats));
    if (!mounted || mode == null || busy || loading) return;
    String? directory;
    if (Platform.isWindows) {
      directory = await getDirectoryPath(confirmButtonText: 'ここに保存');
      if (!mounted || directory == null) return;
    } else if (!Platform.isAndroid) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('この環境ではまだ1本にまとめられません。')));
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    final service = MergeService();
    final progress = ValueNotifier<List<int>>(const [0, 0]);
    final phase = ValueNotifier<MergePhase>(MergePhase.cutting);
    final cancelling = ValueNotifier<bool>(false);
    var dialogOpen = false;
    MergeOutcome? outcome;
    String? failure;
    var cancelled = false;
    try {
      // 4K動画では再生と切り出しが同時に動くと不安定な端末があるため、先に止める。
      await stopPlaybackForAnalysis();
      if (!mounted) return;
      dialogOpen = true;
      unawaited(showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => ListenableBuilder(
              listenable: Listenable.merge([progress, phase, cancelling]),
              builder: (_, __) => MergeProgressDialog(
                  phase: phase.value,
                  done: progress.value[0],
                  total: progress.value[1],
                  cancelling: cancelling.value,
                  canCancel: !Platform.isAndroid,
                  onCancel: () {
                    cancelling.value = true;
                    unawaited(service.cancel());
                  }))));
      void report(MergePhase current, int done, int total) {
        phase.value = current;
        progress.value = [done, total];
      }

      // Androidは処理中に画面が消えると中断されるため、消灯を防ぐ。
      if (Platform.isAndroid) await ExportService().keepScreenOn(true);
      // Androidは保存先の選択もネイティブ側で行う（選ばずに閉じたら中止）。
      outcome = Platform.isAndroid
          ? await service.mergeAndroid(
              entries: plan.entries, onProgress: report)
          : await service.mergeWindows(
              entries: plan.entries,
              directory: directory!,
              mode: mode,
              onProgress: report);
    } on MergeCancelled {
      cancelled = true;
    } on MergeUnsupported catch (e) {
      failure = e.message;
    } catch (e) {
      failure = describeBatchError(e);
    } finally {
      if (mounted) {
        if (dialogOpen) Navigator.of(context, rootNavigator: true).pop();
        setState(() => busy = false);
      }
      if (Platform.isAndroid) {
        await ExportService().keepScreenOn(false);
      }
      progress.dispose();
      phase.dispose();
      cancelling.dispose();
    }
    // 再生を止めたので、元の動画を開き直す。
    final active = session.active;
    if (mounted && active != null && session.items.contains(active)) {
      setState(() => loading = true);
      try {
        await openClip(active);
      } finally {
        if (mounted) setState(() => loading = false);
      }
    }
    if (!mounted) return;
    // 失敗・中止は必ず知らせる（開き直しでエラー欄が消えるため、ダイアログで出す）。
    if (failure != null) {
      await showDialog<void>(
          context: context,
          builder: (_) => MergeFailureDialog(message: failure!));
      return;
    }
    if (cancelled || outcome == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('1本にまとめる処理を中止しました。ファイルは作っていません。')));
      return;
    }
    await showDialog<void>(
        context: context,
        builder: (_) => MergeResultDialog(
            outcome: outcome!, entries: plan.entries.length));
  }

  bool get hasBatchTargets =>
      session.items.any((item) => item.segments.isNotEmpty);

  /// 1本にまとめられる環境か。
  static bool get mergeSupported => Platform.isWindows || Platform.isAndroid;

  /// つなぐには区間が2個以上必要（元動画の有無は実行時に確かめる）。
  bool get canMerge =>
      mergeSupported &&
      session.items.fold<int>(0, (sum, item) => sum + item.segments.length) >= 2;

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
            onRemove: removeClip,
            onInclude: includeWhole,
            onRemoveMany: removeClips),
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
              OutlinedButton.icon(
                onPressed: busy || loading || editing == null ? null : runAnalysis,
                icon: const Icon(Icons.auto_awesome, size: 16),
                label: const Text('AI解析'),
                style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 36),
                    padding: const EdgeInsets.symmetric(horizontal: 10)),
              ),
              const SizedBox(width: 12),
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
              // 広い画面では一覧が左に出ているため、飾りのアイコンは置かない。
              : null,
          title: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(source == null ? 'Kite Clipper' : 'クリップを確認',
                style: const TextStyle(
                    fontWeight: FontWeight.w700, color: Color(0xff14213d))),
            // どのビルドを起動しているか見分けるための表示。
            const Text('ビルド $buildLabel',
                style: TextStyle(fontSize: 11, color: Color(0xff6b7280))),
          ]),
          centerTitle: true,
          actions: [
            if (AnalysisService.supported)
              Padding(
                padding: const EdgeInsets.only(right: 4),
                child: Tooltip(
                  message: omakase.enabled
                      ? 'おまかせON：${omakase.sportId}・${omakase.intervalMs ~/ 1000}秒間隔。押すとOFF'
                      : '追加した動画を自動で解析して区間を入れます',
                  child: FilterChip(
                    avatar: const Icon(Icons.auto_awesome, size: 16),
                    showCheckmark: false,
                    label: Text(omakase.enabled &&
                            MediaQuery.sizeOf(context).width >= 600
                        ? 'おまかせ：${omakase.sportId}'
                        : 'おまかせ'),
                    selected: omakase.enabled,
                    onSelected:
                        busy || loading ? null : (_) => toggleOmakase(),
                  ),
                ),
              ),
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
                  final mergeButton = OutlinedButton.icon(
                    onPressed: busy || loading || !canMerge ? null : runMerge,
                    icon: const Icon(Icons.merge_outlined, size: 18),
                    label: const Text('1本にまとめて保存…'),
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
                        if (mergeSupported) ...[
                          const SizedBox(height: 8),
                          mergeButton,
                        ],
                      ],
                    );
                  }
                  return Row(children: [
                    Expanded(child: label),
                    if (mergeSupported) ...[
                      mergeButton,
                      const SizedBox(width: 8),
                    ],
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
