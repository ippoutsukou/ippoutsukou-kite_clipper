package dev.kiteclipper.kite_clipper_flutter

import android.app.Activity
import android.content.Intent
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaCodec
import android.media.MediaMuxer
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.view.WindowManager
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.FloatBuffer
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.util.concurrent.Executors

/** 保存失敗の診断用。どの段階・どの形式で失敗したかを記録する。 */
private class ExportProgress {
    var stage = "引数確認"
    val formats = mutableListOf<String>()
}

class MainActivity : FlutterActivity() {
    private var pending: MethodChannel.Result? = null
    private var arguments: Map<*, *>? = null
    private var pendingFolder: MethodChannel.Result? = null
    // 1本にまとめる保存。保存先を選んでから実行する。
    private var pendingMerge: MethodChannel.Result? = null
    private var mergeParts: List<*>? = null
    private var exportChannel: MethodChannel? = null
    // 動画の選択と取り込み（コピー）中。メインスレッドでのみ読み書きする。
    private var pendingPick: MethodChannel.Result? = null
    private var pickBusy = false
    private var folderName: String? = null
    // 一括保存のフォルダ作成・1件書き込み中。メインスレッドでのみ読み書きする。
    private var batchBusy = false
    private val worker = Executors.newSingleThreadExecutor()
    private val thumbnailWorker = Executors.newSingleThreadExecutor()
    // AI候補の測定用。解析は1件ずつ、CLIPのセッションは使い回す。
    private val analyzeWorker = Executors.newSingleThreadExecutor()
    private var analyzing = false
    private var clipSession: OrtSession? = null
    // 進捗通知に使う。中止フラグは解析スレッドから読むため @Volatile。
    private var analyzeChannel: MethodChannel? = null
    @Volatile private var cancelAnalyze = false
    private val main = Handler(Looper.getMainLooper())

    private fun isBusy() =
        pending != null || pendingFolder != null || batchBusy || pendingPick != null || pickBusy ||
            pendingMerge != null

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/thumbnails")
            .setMethodCallHandler { call, result ->
                if (call.method != "frame") { result.notImplemented(); return@setMethodCallHandler }
                val source = call.argument<String>("source")
                val position = call.argument<Number>("positionMs")?.toLong()
                if (source == null || position == null || position < 0) {
                    result.error("arguments", "画像取得の指定が不正です", null)
                    return@setMethodCallHandler
                }
                thumbnailWorker.execute {
                    val retriever = android.media.MediaMetadataRetriever()
                    var frame: android.graphics.Bitmap? = null
                    var scaled: android.graphics.Bitmap? = null
                    try {
                        retriever.setDataSource(source)
                        frame = if (android.os.Build.VERSION.SDK_INT >= 27) {
                            retriever.getScaledFrameAtTime(position * 1000,
                                android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC, 160, 90)
                        } else {
                            retriever.getFrameAtTime(position * 1000,
                                android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                        }
                        val bitmap = frame
                        val bytes = if (bitmap == null) null else {
                            val ratio = minOf(160.0 / bitmap.width, 90.0 / bitmap.height, 1.0)
                            scaled = android.graphics.Bitmap.createScaledBitmap(bitmap,
                                maxOf(1, (bitmap.width * ratio).toInt()),
                                maxOf(1, (bitmap.height * ratio).toInt()), true)
                            val stream = java.io.ByteArrayOutputStream()
                            scaled!!.compress(android.graphics.Bitmap.CompressFormat.JPEG, 75, stream)
                            stream.toByteArray()
                        }
                        main.post { result.success(bytes) }
                    } catch (_: Exception) {
                        main.post { result.success(null) }
                    } finally {
                        if (scaled !== frame) scaled?.recycle()
                        frame?.recycle()
                        runCatching { retriever.release() }
                    }
                }
            }
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/picker")
            .setMethodCallHandler { call, result ->
                if (call.method != "pickVideos") { result.notImplemented(); return@setMethodCallHandler }
                if (isBusy() || pendingPick != null) { result.error("busy", "処理中です", null); return@setMethodCallHandler }
                pendingPick = result
                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "video/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                try { startActivityForResult(intent, PICK_REQUEST) }
                catch (e: Exception) {
                    pendingPick = null
                    result.error("picker", "動画を選ぶ画面を開けません", null)
                }
            }
        val analyze = MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/analyze")
        analyzeChannel = analyze
        analyze.setMethodCallHandler { call, result ->
                if (call.method == "cancelAnalyze") {
                    // 処理中の1枚が終わった時点で止める。
                    cancelAnalyze = true
                    result.success(null)
                    return@setMethodCallHandler
                }
                if (call.method != "analyze") { result.notImplemented(); return@setMethodCallHandler }
                if (analyzing) { result.error("busy", "解析中です", null); return@setMethodCallHandler }
                val source = call.argument<String>("source")
                val intervalMs = call.argument<Number>("intervalMs")?.toLong() ?: 1000L
                val maxFrames = call.argument<Number>("maxFrames")?.toInt() ?: 300
                // 競技モデルを複数まとめて採点する。CLIPの計算は共通のため追加コストはほぼない。
                val coefs = call.argument<List<List<Double>>>("coefs")
                val intercepts = call.argument<List<Double>>("intercepts")
                if (source == null || coefs == null || intercepts == null ||
                    coefs.isEmpty() || coefs.size != intercepts.size ||
                    coefs.any { it.size != CLIP_EMBED_DIM } ||
                    intervalMs < 100 || maxFrames < 1) {
                    result.error("arguments", "解析の指定が不正です", null)
                    return@setMethodCallHandler
                }
                analyzing = true
                cancelAnalyze = false
                analyzeWorker.execute {
                    try {
                        val measured = analyze(source, intervalMs, maxFrames, coefs, intercepts)
                        main.post { analyzing = false; result.success(measured) }
                    } catch (e: Throwable) {
                        // メモリ不足などは Exception ではないため、Throwable で受けてアプリごと落とさない。
                        val hint = if (e is OutOfMemoryError) "（メモリ不足）" else ""
                        val message = "${e.javaClass.simpleName}$hint ${e.message ?: ""}".trim()
                        main.post { analyzing = false; result.error("analyze", message, null) }
                    }
                }
            }
        val export = MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/export")
        exportChannel = export
        export.setMethodCallHandler { call, result ->
                when (call.method) {
                    "merge" -> startMerge(call.argument<String>("name"),
                        call.argument<List<*>>("parts"), result)
                    "export" -> startExport(call.arguments as? Map<*, *>, result)
                    "pickFolder" -> startPickFolder(call.argument<String>("name"), result)
                    "exportTo" -> exportTo(
                        call.argument<String>("folder"), call.argument<String>("name"),
                        call.argument<String>("source"),
                        call.argument<Number>("startMs")?.toLong(),
                        call.argument<Number>("endMs")?.toLong(), result)
                    // 作業記録の保存場所。アプリ専用領域なので権限は不要。
                    "appDataDir" -> result.success(filesDir.absolutePath)
                    "keepScreenOn" -> {
                        if (call.argument<Boolean>("on") == true) {
                            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        } else {
                            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        }
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    /** 1件保存：保存先をその都度選ぶ（ビルド11までと同じ流れ）。 */
    private fun startExport(args: Map<*, *>?, result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (android.os.Build.VERSION.SDK_INT < 26) {
            result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
            return
        }
        pending = result
        arguments = args
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/mp4"
            putExtra(Intent.EXTRA_TITLE, "kite_clip_${System.currentTimeMillis()}.mp4")
        }
        try { startActivityForResult(intent, EXPORT_REQUEST) }
        catch (e: Exception) { pending = null; arguments = null; result.error("picker", "保存先を開けません", null) }
    }

    /** 一括保存：フォルダを1回選び、その中に実行ごとのフォルダを作る。 */
    private fun startPickFolder(name: String?, result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (name.isNullOrBlank()) { result.error("arguments", "フォルダ名が不正です", null); return }
        pendingFolder = result
        folderName = name
        try { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), FOLDER_REQUEST) }
        catch (e: Exception) {
            pendingFolder = null; folderName = null
            result.error("picker", "フォルダ選択画面を開けません", null)
        }
    }

    /** 一括保存の1件：実行ごとのフォルダにファイルを作って書き込む。同名は保存先側が別名にする。 */
    private fun exportTo(folder: String?, name: String?, source: String?, startMs: Long?, endMs: Long?,
                         result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (android.os.Build.VERSION.SDK_INT < 26) {
            result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
            return
        }
        if (folder == null || name.isNullOrBlank() || source == null || startMs == null || endMs == null ||
            startMs < 0 || endMs <= startMs) {
            result.error("arguments", "保存の指定が不正です", null)
            return
        }
        batchBusy = true
        worker.execute {
            val progress = ExportProgress()
            var created: Uri? = null
            try {
                progress.stage = "保存先ファイルを作成"
                val file = DocumentsContract.createDocument(contentResolver, Uri.parse(folder), "video/mp4", name)
                    ?: throw java.io.IOException("ファイルを作成できません")
                created = file
                writeClip(file, source, startMs, endMs, progress)
                main.post { batchBusy = false; result.success(file.toString()) }
            } catch (e: Exception) {
                // ファイル自体を作れない場合はフォルダの問題として扱い、Dart側で残りを中止する。
                val code = if (created == null) "folder" else "export"
                val message = diagnostic(progress, e)
                main.post { batchBusy = false; result.error(code, message, null) }
            }
        }
    }

    @Deprecated("Activity result bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            EXPORT_REQUEST -> onExportDestination(resultCode, data)
            MERGE_REQUEST -> onMergeDestination(resultCode, data)
            FOLDER_REQUEST -> onFolderChosen(resultCode, data)
            PICK_REQUEST -> onVideosPicked(resultCode, data)
        }
    }

    private fun onExportDestination(resultCode: Int, data: Intent?) {
        val callback = pending ?: return
        val args = arguments
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            pending = null; arguments = null; callback.success(null); return
        }
        worker.execute {
            val progress = ExportProgress()
            try {
                require(args != null)
                val source = args["source"] as String
                val start = (args["startMs"] as Number).toLong()
                val end = (args["endMs"] as Number).toLong()
                writeClip(uri, source, start, end, progress)
                main.post { pending = null; arguments = null; callback.success(uri.toString()) }
            } catch (e: Exception) {
                val message = diagnostic(progress, e)
                main.post { pending = null; arguments = null; callback.error("export", message, null) }
            }
        }
    }

    private fun onFolderChosen(resultCode: Int, data: Intent?) {
        val callback = pendingFolder ?: return
        val name = folderName
        pendingFolder = null; folderName = null
        val tree = data?.data
        if (resultCode != Activity.RESULT_OK || tree == null || name == null) {
            callback.success(null); return
        }
        batchBusy = true
        worker.execute {
            try {
                val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
                val parentName = displayName(parent) ?: "選んだフォルダ"
                val folder = DocumentsContract.createDocument(contentResolver, parent,
                    DocumentsContract.Document.MIME_TYPE_DIR, name)
                    ?: throw java.io.IOException("フォルダを作成できません")
                val label = "$parentName/${displayName(folder) ?: name}"
                main.post { batchBusy = false; callback.success(mapOf("uri" to folder.toString(), "label" to label)) }
            } catch (e: Exception) {
                val reason = if (e is SecurityException) "アクセスが拒否されました" else "別のフォルダを選んでください"
                main.post { batchBusy = false; callback.error("folder", "保存先フォルダを作成できませんでした（$reason）", null) }
            }
        }
    }

    /**
     * 同梱のCLIPモデルをアプリ専用領域へ取り出す（毎回コピーしない）。
     * 同梱ファイルはAPK内で圧縮されているため、assets.openFd でサイズを問い合わせることはできない。
     * 取り出し済みファイルの大きさを既知の値（CLIP_MODEL_BYTES）と比べて判定する。
     */
    private fun clipModelFile(): File {
        val target = File(filesDir, "vision_model_quantized.onnx")
        if (target.exists() && target.length() == CLIP_MODEL_BYTES) return target
        val key = io.flutter.FlutterInjector.instance().flutterLoader()
            .getLookupKeyForAsset("assets/clip/vision_model_quantized.onnx")
        val temp = File(filesDir, "vision_model_quantized.onnx.tmp")
        assets.open(key).use { input -> temp.outputStream().use { input.copyTo(it) } }
        if (temp.length() != CLIP_MODEL_BYTES) {
            temp.delete()
            throw java.io.IOException("モデルの取り出し結果が想定と違います")
        }
        // 取り出し途中で終了した古いコピーだけを置き換える（利用者のファイルには触れない）。
        if (target.exists()) target.delete()
        if (!temp.renameTo(target)) throw java.io.IOException("モデルを取り出せません")
        return target
    }

    private fun clipSession(): OrtSession {
        clipSession?.let { return it }
        val environment = OrtEnvironment.getEnvironment()
        val created = environment.createSession(clipModelFile().absolutePath, OrtSession.SessionOptions())
        clipSession = created
        return created
    }

    /**
     * 動画から intervalMs ごとにフレームを取り出し、CLIP埋め込み→採用確率を計算する。
     * 速度測定が目的のため、段階ごとの所要時間も返す。保存処理には触れない。
     */
    private fun analyze(
        source: String, intervalMs: Long, maxFrames: Int,
        coefs: List<List<Double>>, intercepts: List<Double>,
    ): Map<String, Any> {
        val started = System.currentTimeMillis()
        val modelStart = System.currentTimeMillis()
        val session = clipSession()
        val environment = OrtEnvironment.getEnvironment()
        val inputName = session.inputNames.first()
        val modelMs = System.currentTimeMillis() - modelStart

        val retriever = android.media.MediaMetadataRetriever()
        val timesMs = mutableListOf<Long>()
        // モデルごとの点数。並び順は受け取った係数と同じ。
        val scores = List(coefs.size) { mutableListOf<Double>() }
        var extractMs = 0L
        var inferMs = 0L
        var durationMs = 0L
        var truncated = false
        var cancelled = false
        try {
            retriever.setDataSource(source)
            durationMs = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
            require(durationMs > 0) { "動画の長さを取得できません" }
            val width = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: CLIP_IMAGE_SIZE
            val height = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: CLIP_IMAGE_SIZE
            // Python版と同じ前処理：短辺224へ縮小してから中央224x224を切り出す。
            val scale = CLIP_IMAGE_SIZE.toDouble() / minOf(width, height).coerceAtLeast(1)
            val scaledWidth = maxOf(CLIP_IMAGE_SIZE, Math.round(width * scale).toInt())
            val scaledHeight = maxOf(CLIP_IMAGE_SIZE, Math.round(height * scale).toInt())

            val batch = ArrayList<FloatArray>(BATCH_SIZE)
            val batchTimes = ArrayList<Long>(BATCH_SIZE)

            fun runBatch() {
                if (batch.isEmpty()) return
                val inferStart = System.currentTimeMillis()
                val buffer = FloatBuffer.allocate(batch.size * 3 * CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE)
                batch.forEach { buffer.put(it) }
                buffer.rewind()
                OnnxTensor.createTensor(environment, buffer,
                    longArrayOf(batch.size.toLong(), 3, CLIP_IMAGE_SIZE.toLong(), CLIP_IMAGE_SIZE.toLong())
                ).use { tensor ->
                    session.run(mapOf(inputName to tensor)).use { output ->
                        @Suppress("UNCHECKED_CAST")
                        val embeddings = output[0].value as Array<FloatArray>
                        for ((index, raw) in embeddings.withIndex()) {
                            var norm = 0.0
                            for (value in raw) norm += value.toDouble() * value
                            norm = Math.sqrt(norm)
                            timesMs.add(batchTimes[index])
                            for ((modelIndex, coefficients) in coefs.withIndex()) {
                                var sum = intercepts[modelIndex]
                                if (norm > 1e-12) {
                                    for (i in raw.indices) sum += coefficients[i] * (raw[i] / norm)
                                }
                                scores[modelIndex].add(1.0 / (1.0 + Math.exp(-sum)))
                            }
                        }
                    }
                }
                inferMs += System.currentTimeMillis() - inferStart
                batch.clear()
                batchTimes.clear()
            }

            val total = minOf(maxFrames.toLong(), (durationMs + intervalMs - 1) / intervalMs).toInt()
            var positionMs = 0L
            while (positionMs < durationMs) {
                if (cancelAnalyze) { cancelled = true; break }
                if (timesMs.size + batch.size >= maxFrames) { truncated = true; break }
                if (timesMs.size % PROGRESS_STEP == 0) notifyProgress(timesMs.size, total)
                val extractStart = System.currentTimeMillis()
                val frame = retriever.getScaledFrameAtTime(
                    positionMs * 1000,
                    android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC,
                    scaledWidth, scaledHeight)
                extractMs += System.currentTimeMillis() - extractStart
                if (frame != null) {
                    batch.add(preprocess(frame))
                    batchTimes.add(positionMs)
                    frame.recycle()
                    if (batch.size >= BATCH_SIZE) runBatch()
                }
                positionMs += intervalMs
            }
            runBatch()
        } finally {
            runCatching { retriever.release() }
        }
        return mapOf(
            "durationMs" to durationMs,
            "timesMs" to timesMs,
            "scores" to scores,
            "modelMs" to modelMs,
            "extractMs" to extractMs,
            "inferMs" to inferMs,
            "totalMs" to (System.currentTimeMillis() - started),
            "truncated" to truncated,
            "cancelled" to cancelled,
        )
    }

    /** 解析の進捗をDart側へ知らせる（画面表示用。失敗しても解析は続ける）。 */
    private fun notifyProgress(done: Int, total: Int) {
        val channel = analyzeChannel ?: return
        main.post {
            runCatching { channel.invokeMethod("progress", mapOf("done" to done, "total" to total)) }
        }
    }

    /** Bitmap の中央224x224を CHW float32 に正規化する（CLIPのmean/std）。 */
    private fun preprocess(frame: android.graphics.Bitmap): FloatArray {
        val left = maxOf(0, (frame.width - CLIP_IMAGE_SIZE) / 2)
        val top = maxOf(0, (frame.height - CLIP_IMAGE_SIZE) / 2)
        val pixels = IntArray(CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE)
        frame.getPixels(pixels, 0, CLIP_IMAGE_SIZE, left, top, CLIP_IMAGE_SIZE, CLIP_IMAGE_SIZE)
        val plane = CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE
        val output = FloatArray(3 * plane)
        for (i in pixels.indices) {
            val pixel = pixels[i]
            val r = ((pixel shr 16) and 0xff) / 255f
            val g = ((pixel shr 8) and 0xff) / 255f
            val b = (pixel and 0xff) / 255f
            output[i] = (r - CLIP_MEAN[0]) / CLIP_STD[0]
            output[plane + i] = (g - CLIP_MEAN[1]) / CLIP_STD[1]
            output[2 * plane + i] = (b - CLIP_MEAN[2]) / CLIP_STD[2]
        }
        return output
    }

    /**
     * 選んだ動画をアプリ専用領域へ少しずつコピーし、コピー先のパスだけを返す。
     * file_selector のAndroid実装のように中身をDart側へ渡すと、大きい動画でメモリ不足になる。
     */
    private fun onVideosPicked(resultCode: Int, data: Intent?) {
        val callback = pendingPick ?: return
        pendingPick = null
        val uris = mutableListOf<Uri>()
        if (resultCode == Activity.RESULT_OK && data != null) {
            data.clipData?.let { clip ->
                for (index in 0 until clip.itemCount) uris.add(clip.getItemAt(index).uri)
            }
            if (uris.isEmpty()) data.data?.let { uris.add(it) }
        }
        if (uris.isEmpty()) { callback.success(emptyList<String>()); return }
        pickBusy = true
        worker.execute {
            val paths = mutableListOf<String>()
            var failure: String? = null
            try {
                for (uri in uris) paths.add(copyPickedVideo(uri))
            } catch (e: Throwable) {
                failure = when (e) {
                    is java.io.IOException -> "動画をコピーできませんでした（空き容量を確認してください）"
                    is SecurityException -> "選んだ動画を読み取れませんでした"
                    else -> "動画を取り込めませんでした：${e.javaClass.simpleName}"
                }
            }
            main.post {
                pickBusy = false
                // 一部だけ成功した場合は、取り込めた分を返す。
                if (paths.isEmpty() && failure != null) callback.error("picker", failure, null)
                else callback.success(paths)
            }
        }
    }

    private fun copyPickedVideo(uri: Uri): String {
        val folder = File(filesDir, "videos").apply { mkdirs() }
        val size = contentResolver.query(
            uri, arrayOf(android.provider.OpenableColumns.SIZE), null, null, null
        )?.use { cursor ->
            if (cursor.moveToFirst() && !cursor.isNull(0)) cursor.getLong(0) else -1L
        } ?: -1L
        val base = (displayName(uri) ?: "video.mp4")
            .replace(Regex("[\\\\/:*?\"<>| -]"), "_").takeLast(80)
        var target = File(folder, base)
        var index = 2
        while (target.exists()) {
            // 同じ名前・同じ大きさなら、以前取り込んだものを使い回す。
            if (size >= 0 && target.length() == size) return target.absolutePath
            val dot = base.lastIndexOf('.')
            val stem = if (dot > 0) base.substring(0, dot) else base
            val extension = if (dot > 0) base.substring(dot) else ""
            target = File(folder, "${stem}_$index$extension")
            index++
        }
        val temp = File(folder, "${target.name}.tmp")
        contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "動画を開けません" }
            temp.outputStream().use { output -> input.copyTo(output, 64 * 1024) }
        }
        if (!temp.renameTo(target)) {
            temp.delete()
            throw java.io.IOException("コピーを保存できません")
        }
        return target.absolutePath
    }

    private fun displayName(uri: Uri): String? = runCatching {
        contentResolver.query(uri, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }
    }.getOrNull()

    /** 指定の保存先URIへ範囲を書き込む。1件保存・一括保存で共用（ビルド11までの処理を切り出したもの）。 */
    private fun writeClip(uri: Uri, source: String, startMs: Long, endMs: Long, progress: ExportProgress) {
        val start = startMs * 1000
        val end = endMs * 1000
        require(start >= 0 && end > start)
        progress.stage = "保存先を開く"
        contentResolver.openFileDescriptor(uri, "w")!!.use { descriptor ->
            progress.stage = "MP4書き込み準備"
            val muxer = MediaMuxer(descriptor.fileDescriptor, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val extractor = MediaExtractor()
            try {
                progress.stage = "元動画を開く"
                extractor.setDataSource(source)
                val tracks = mutableMapOf<Int, Int>()
                var video = -1
                var audio = -1
                var pcmAudio = -1
                var bufferSize = 8 * 1024 * 1024
                for (i in 0 until extractor.trackCount) {
                    progress.stage = "トラック形式の確認"
                    val format = extractor.getTrackFormat(i)
                    val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
                    progress.formats.add(if (mime.matches(Regex("[a-zA-Z0-9.+/_-]{1,80}"))) mime else "形式不明")
                    if (mime.startsWith("video/") && video == -1) {
                        require(mime == "video/avc" || mime == "video/hevc") { "未対応の映像形式です" }
                        video = i
                        if (format.containsKey(MediaFormat.KEY_ROTATION)) muxer.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                    } else if (mime.startsWith("audio/") && audio == -1) {
                        require(mime == "audio/mp4a-latm" || mime == "audio/raw") { "未対応の音声形式です" }
                        audio = i
                        if (mime == "audio/raw") {
                            pcmAudio = i
                            continue
                        }
                    } else continue
                    if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) bufferSize = maxOf(bufferSize, format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                    progress.stage = "MP4トラック登録"
                    tracks[i] = muxer.addTrack(format)
                }
                progress.stage = "保存範囲の先頭を検索"
                require(video >= 0) { "映像トラックがありません" }
                extractor.selectTrack(video)
                extractor.seekTo(start, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                val actualStart = extractor.sampleTime
                require(actualStart >= 0 && actualStart < end)
                tracks.keys.forEach { extractor.selectTrack(it) }
                extractor.seekTo(actualStart, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                require(bufferSize <= 64 * 1024 * 1024) { "必要なバッファが大きすぎます" }
                progress.stage = "バッファ確保"
                val buffer = ByteBuffer.allocateDirect(bufferSize)
                val info = MediaCodec.BufferInfo()
                progress.stage = "MP4書き込み開始"
                if (pcmAudio >= 0) {
                    progress.stage = "PCM音声をAACへ変換"
                    var aacTrack = -1
                    PcmAac.encode(source, pcmAudio, actualStart, end,
                        onFormat = { format ->
                            aacTrack = muxer.addTrack(format)
                            muxer.start()
                        },
                        onSample = { data, sampleInfo ->
                            muxer.writeSampleData(aacTrack, data, sampleInfo)
                        })
                } else {
                    muxer.start()
                }
                var videoSamples = 0
                while (extractor.sampleTrackIndex >= 0) {
                    val track = extractor.sampleTrackIndex
                    val pts = extractor.sampleTime
                    if (pts >= actualStart && pts < end) {
                        buffer.clear()
                        progress.stage = "映像・音声データ読み取り"
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) break
                        require(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED == 0) { "暗号化動画は未対応です" }
                        info.set(0, size, pts - actualStart,
                            if (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                        progress.stage = "映像・音声データ書き込み"
                        muxer.writeSampleData(tracks.getValue(track), buffer, info)
                        if (track == video) videoSamples++
                    }
                    progress.stage = "次のデータへ移動"
                    if (!extractor.advance()) break
                }
                progress.stage = "出力内容確認"
                require(videoSamples > 0) { "範囲内に映像がありません" }
                progress.stage = "MP4最終処理"
                muxer.stop()
            } finally {
                // 後片付けの例外で本来の保存失敗の原因を上書きしない。
                runCatching { extractor.release() }
                runCatching { muxer.release() }
            }
            progress.stage = "保存先を閉じる"
        }
    }

    /** 1本にまとめる：保存先ファイルを選ぶ。実際の書き込みは選択後。 */
    private fun startMerge(name: String?, parts: List<*>?, result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (android.os.Build.VERSION.SDK_INT < 26) {
            result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
            return
        }
        if (name.isNullOrBlank() || parts == null || parts.size < 2) {
            result.error("arguments", "まとめる指定が不正です", null)
            return
        }
        pendingMerge = result
        mergeParts = parts
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/mp4"
            putExtra(Intent.EXTRA_TITLE, name)
        }
        try { startActivityForResult(intent, MERGE_REQUEST) }
        catch (e: Exception) {
            pendingMerge = null; mergeParts = null
            result.error("picker", "保存先を開けません", null)
        }
    }

    private fun onMergeDestination(resultCode: Int, data: Intent?) {
        val callback = pendingMerge ?: return
        val parts = mergeParts
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null || parts == null) {
            pendingMerge = null; mergeParts = null; callback.success(null); return
        }
        worker.execute {
            val progress = ExportProgress()
            try {
                mergeClips(uri, parts, progress)
                main.post { pendingMerge = null; mergeParts = null; callback.success(uri.toString()) }
            } catch (e: Exception) {
                // 途中まで書いた出力は使えないため削除する。
                runCatching { DocumentsContract.deleteDocument(contentResolver, uri) }
                val known = e.message?.takeIf { it.startsWith("形式がそろっていません") || it.startsWith("この形式") }
                val message = known ?: diagnostic(progress, e).replaceFirst("ビルド4：保存失敗", "1本にまとめる処理で失敗")
                main.post { pendingMerge = null; mergeParts = null; callback.error("merge", message, null) }
            }
        }
    }

    /** 動画・音声の形式。これがそろっていないと、端末の仕組みではつなげない。 */
    private data class Shape(val video: String, val width: Int, val height: Int,
                             val audio: String, val sampleRate: Int, val channels: Int)

    private fun shapeOf(extractor: MediaExtractor): Shape {
        var video: MediaFormat? = null
        var audio: MediaFormat? = null
        for (i in 0 until extractor.trackCount) {
            val format = extractor.getTrackFormat(i)
            val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
            if (mime.startsWith("video/") && video == null) video = format
            if (mime.startsWith("audio/") && audio == null) audio = format
        }
        val v = video ?: throw IllegalArgumentException("映像トラックがありません")
        fun number(format: MediaFormat?, key: String) =
            if (format != null && format.containsKey(key)) format.getInteger(key) else 0
        return Shape(
            v.getString(MediaFormat.KEY_MIME) ?: "",
            number(v, MediaFormat.KEY_WIDTH), number(v, MediaFormat.KEY_HEIGHT),
            audio?.getString(MediaFormat.KEY_MIME) ?: "",
            number(audio, MediaFormat.KEY_SAMPLE_RATE), number(audio, MediaFormat.KEY_CHANNEL_COUNT))
    }

    private fun describe(shape: Shape) =
        "${shape.video} ${shape.width}x${shape.height} / ${shape.audio.ifEmpty { "音声なし" }}"

    /**
     * 複数の区間を順につないで1本のMP4にする。作り直し（再エンコード）はしないため、
     * 形式（映像・大きさ・音声）がそろっている動画だけを扱える。
     */
    private fun mergeClips(uri: Uri, parts: List<*>, progress: ExportProgress) {
        progress.stage = "保存先を開く"
        contentResolver.openFileDescriptor(uri, "w")!!.use { descriptor ->
            progress.stage = "MP4書き込み準備"
            val muxer = MediaMuxer(descriptor.fileDescriptor, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val extractors = mutableListOf<MediaExtractor>()
            try {
                // 1) すべての区間の形式を先に確かめる（1つでも違えば何も書かずに終える）。
                var first: Shape? = null
                val sources = mutableListOf<Triple<MediaExtractor, Long, Long>>()
                for (part in parts) {
                    progress.stage = "元動画を開く"
                    val map = part as? Map<*, *> ?: throw IllegalArgumentException("まとめる指定が不正です")
                    val source = map["source"] as? String ?: throw IllegalArgumentException("まとめる指定が不正です")
                    val start = (map["startMs"] as Number).toLong() * 1000
                    val end = (map["endMs"] as Number).toLong() * 1000
                    require(start >= 0 && end > start)
                    val extractor = MediaExtractor()
                    extractors.add(extractor)
                    extractor.setDataSource(source)
                    progress.stage = "トラック形式の確認"
                    val shape = shapeOf(extractor)
                    require(shape.video == "video/avc" || shape.video == "video/hevc") { "未対応の映像形式です" }
                    if (shape.audio.isNotEmpty() && shape.audio != "audio/mp4a-latm") {
                        throw IllegalArgumentException("この形式の音声はまとめられません（${shape.audio}）")
                    }
                    val known = first
                    if (known == null) first = shape
                    else if (shape != known) {
                        throw IllegalArgumentException(
                            "形式がそろっていません：${describe(known)} と ${describe(shape)}")
                    }
                    sources.add(Triple(extractor, start, end))
                }

                // 2) 先頭の区間の形式でトラックを作り、順に書き込む。
                val head = sources.first().first
                val tracks = mutableMapOf<Int, Int>()
                var video = -1
                var audioTrack = -1
                var bufferSize = 8 * 1024 * 1024
                for (i in 0 until head.trackCount) {
                    val format = head.getTrackFormat(i)
                    val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
                    progress.formats.add(if (mime.matches(Regex("[a-zA-Z0-9.+/_-]{1,80}"))) mime else "形式不明")
                    if (mime.startsWith("video/") && video == -1) {
                        video = i
                        if (format.containsKey(MediaFormat.KEY_ROTATION)) muxer.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                    } else if (mime.startsWith("audio/") && audioTrack == -1) {
                        audioTrack = i
                    } else continue
                    if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) bufferSize = maxOf(bufferSize, format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                    progress.stage = "MP4トラック登録"
                    tracks[i] = muxer.addTrack(format)
                }
                require(video >= 0) { "映像トラックがありません" }
                require(bufferSize <= 64 * 1024 * 1024) { "必要なバッファが大きすぎます" }
                progress.stage = "バッファ確保"
                val buffer = ByteBuffer.allocateDirect(bufferSize)
                val info = MediaCodec.BufferInfo()
                progress.stage = "MP4書き込み開始"
                muxer.start()
                var offset = 0L
                var videoSamples = 0
                for ((index, entry) in sources.withIndex()) {
                    val extractor = entry.first
                    val start = entry.second
                    val end = entry.third
                    progress.stage = "区間${index + 1}の先頭を検索"
                    extractor.selectTrack(video)
                    extractor.seekTo(start, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                    val actualStart = extractor.sampleTime
                    require(actualStart >= 0 && actualStart < end) { "範囲内に映像がありません" }
                    tracks.keys.forEach { extractor.selectTrack(it) }
                    extractor.seekTo(actualStart, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                    while (extractor.sampleTrackIndex >= 0) {
                        val track = extractor.sampleTrackIndex
                        val pts = extractor.sampleTime
                        if (pts >= actualStart && pts < end) {
                            buffer.clear()
                            progress.stage = "区間${index + 1}の読み取り"
                            val size = extractor.readSampleData(buffer, 0)
                            if (size < 0) break
                            require(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED == 0) { "暗号化動画は未対応です" }
                            info.set(0, size, pts - actualStart + offset,
                                if (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                            progress.stage = "区間${index + 1}の書き込み"
                            tracks[track]?.let { muxer.writeSampleData(it, buffer, info) }
                            if (track == video) videoSamples++
                        }
                        if (!extractor.advance()) break
                    }
                    // 次の区間は、この区間の長さ分だけ後ろにずらす（音ズレ・重なりを防ぐ）。
                    offset += end - actualStart
                    notifyMerge(index + 1, sources.size)
                }
                progress.stage = "出力内容確認"
                require(videoSamples > 0) { "範囲内に映像がありません" }
                progress.stage = "MP4最終処理"
                muxer.stop()
            } finally {
                extractors.forEach { runCatching { it.release() } }
                runCatching { muxer.release() }
            }
            progress.stage = "保存先を閉じる"
        }
    }

    private fun notifyMerge(done: Int, total: Int) {
        main.post {
            exportChannel?.invokeMethod("mergeProgress", mapOf("done" to done, "total" to total))
        }
    }

    private fun diagnostic(progress: ExportProgress, e: Exception): String {
        val knownReasons = setOf("PCM16以外の音声は未対応です", "音声のサンプルレート・チャンネル数が未対応です", "PCMデータの構造が未対応です", "音声変換がタイムアウトしました", "範囲内に音声がありません", "未対応の映像形式です", "未対応の音声形式です", "映像トラックがありません", "必要なバッファが大きすぎます", "暗号化動画は未対応です", "範囲内に映像がありません")
        val reason = e.message?.takeIf { it in knownReasons } ?: when (e) {
            is SecurityException -> "アクセスが拒否されました"
            is java.io.IOException -> "入出力処理に失敗しました（容量不足とは限りません）"
            is IllegalArgumentException -> "形式・範囲またはAPI引数が受け付けられませんでした"
            is IllegalStateException -> "動画処理エンジンが処理を完了できませんでした"
            else -> "詳細は診断コードで確認が必要です"
        }
        return "ビルド4：保存失敗\n段階：${progress.stage}\n理由：$reason\n形式：${progress.formats.take(8).joinToString().ifEmpty { "未取得" }}\n種類：${e.javaClass.simpleName}\n保存先に未完成ファイルが残る場合があります。"
    }

    companion object {
        private const val EXPORT_REQUEST = 8102
        private const val FOLDER_REQUEST = 8103
        private const val MERGE_REQUEST = 8105
        private const val PICK_REQUEST = 8104
        // vendor/clip/SOURCE.txt と clip_embedder.py に合わせた前処理仕様。
        private const val CLIP_EMBED_DIM = 512
        // assets/clip/vision_model_quantized.onnx のバイト数。モデルを差し替えたら更新すること。
        private const val CLIP_MODEL_BYTES = 89117001L
        private const val CLIP_IMAGE_SIZE = 224
        // 4K動画でのメモリ不足を避けるため1枚ずつ処理する（速度差は実測で1枚39ms対45ms程度）。
        private const val BATCH_SIZE = 1
        private const val PROGRESS_STEP = 8
        private val CLIP_MEAN = floatArrayOf(0.48145466f, 0.4578275f, 0.40821073f)
        private val CLIP_STD = floatArrayOf(0.26862954f, 0.26130258f, 0.27577711f)
    }
}
