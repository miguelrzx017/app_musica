package org.miguelribeiro.mymusicgospel;

import android.app.PendingIntent;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.session.MediaSession;
import androidx.media3.session.MediaSessionService;
import androidx.media3.session.MediaSession.ControllerInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.HashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Dono da reprodução Android. Media3 mantém o áudio, a fila e a sessão de mídia
 * vivos enquanto a Activity/Kivy está suspensa e publica os controles no sistema.
 */
public final class PlaybackMediaService extends MediaSessionService {
    private static final String TAG = "MyMusicPlayback";
    private static final String ACTION_START = "org.miguelribeiro.mymusicgospel.START";
    private static final String ACTION_PREPARE = "org.miguelribeiro.mymusicgospel.PREPARE";
    private static final String ACTION_SYNC = "org.miguelribeiro.mymusicgospel.SYNC";
    private static final String ACTION_PLAY = "org.miguelribeiro.mymusicgospel.PLAY";
    private static final String ACTION_PAUSE = "org.miguelribeiro.mymusicgospel.PAUSE";
    private static final String ACTION_SEEK = "org.miguelribeiro.mymusicgospel.SEEK";
    private static final String ACTION_STOP = "org.miguelribeiro.mymusicgospel.STOP";
    private static final String EXTRA_QUEUE = "queue_json";
    private static final String EXTRA_POSITION = "position_ms";
    private static final String PREFS = "mymusic_media_session";
    private static final String PREF_QUEUE = "queue_json";
    private static final String PREF_POSITION = "position_ms";
    private static final String PREF_PLAYING = "playing";
    private static final String PREF_MEDIA_ID = "media_id";
    private static final String EXTRA_RESTORE = "restore_id";
    private static final long STATE_UPDATE_MS = 250L;     // tocando
    private static final long STATE_IDLE_MS = 1000L;      // pausado: poupa bateria
    private static final long POSITION_SAVE_MS = 5000L;

    private static volatile PlaybackMediaService instance;
    private static volatile String snapshotMediaId = "";
    private static volatile long snapshotPositionMs;
    private static volatile long snapshotDurationMs;
    private static volatile boolean snapshotPlaying;
    private static volatile boolean snapshotHasItem;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private ExoPlayer player;
    private MediaSession mediaSession;
    private String databasePath = "";
    private String activeUser = "";
    private String trackedMediaId = "";
    private String trackedUser = "";
    private long trackedStartedAtMs;
    private long trackedListenedMs;
    private long trackedRowId = -1L;
    private long lastStatsWriteAtMs;
    private long lastSamplePositionMs;
    private long lastSampleAtElapsedMs;
    private boolean lastSampleWasPlaying;
    private String lastQueueJson = "";
    private String restoreId = "";
    private int consecutiveErrors;
    private long lastPositionSaveAt;
    // Banco de dados fora da thread principal: evita ANR/fechamento em segundo plano
    // quando o Python segura o banco ao trocar de faixa.
    private final ExecutorService dbExecutor = Executors.newSingleThreadExecutor();
    private final HashMap<Long, Long> rowIdByStart = new HashMap<>();

    private final Runnable stateUpdater = new Runnable() {
        @Override public void run() {
            updateSnapshot();
            if (player != null) {
                sampleListenedTime();
                if (player.isPlaying()) saveProgress(false);
                long now = SystemClock.elapsedRealtime();
                if (now - lastPositionSaveAt >= POSITION_SAVE_MS) savePosition();
            }
            boolean playing = player != null && player.isPlaying();
            mainHandler.postDelayed(this, playing ? STATE_UPDATE_MS : STATE_IDLE_MS);
        }
    };

    public static void startPlayback(Context context, String queueJson) {
        start(context, ACTION_START, queueJson, 0L);
    }

    public static void preparePaused(Context context, String queueJson) {
        start(context, ACTION_PREPARE, queueJson, 0L);
    }

    public static void syncQueue(Context context, String queueJson) {
        start(context, ACTION_SYNC, queueJson, 0L);
    }

    public static void playPlayback(Context context) {
        PlaybackMediaService service = instance;
        if (service != null) {
            service.postCommand(ACTION_PLAY, null, 0L);
            return;
        }
        android.content.SharedPreferences preferences =
                context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String json = preferences.getString(PREF_QUEUE, "");
        if (!json.isEmpty()) {
            context.startService(new Intent(context, PlaybackMediaService.class)
                    .setAction(ACTION_START)
                    .putExtra(EXTRA_QUEUE, json)
                    .putExtra(EXTRA_POSITION, preferences.getLong(PREF_POSITION, 0L))
                    .putExtra(EXTRA_RESTORE, preferences.getString(PREF_MEDIA_ID, "")));
        }
    }

    public static void pausePlayback(Context context) {
        command(context, ACTION_PAUSE, 0L);
    }

    public static void seekTo(Context context, long positionMs) {
        command(context, ACTION_SEEK, positionMs);
    }

    public static void stopPlayback(Context context) {
        PlaybackMediaService service = instance;
        if (service != null) service.postCommand(ACTION_STOP, null, 0L);
    }

    public static boolean isPlaying() { return snapshotPlaying; }
    public static boolean hasMediaItem() { return snapshotHasItem; }
    public static String getCurrentMediaId() { return snapshotMediaId; }
    public static long getPositionMs() { return snapshotPositionMs; }
    public static long getDurationMs() { return snapshotDurationMs; }

    private static void start(Context context, String action, String json, long positionMs) {
        Intent intent = new Intent(context, PlaybackMediaService.class)
                .setAction(action)
                .putExtra(EXTRA_QUEUE, json)
                .putExtra(EXTRA_POSITION, positionMs);
        // startPlayback vem da Activity visível. O MediaSessionService promove
        // a sessão quando o player realmente começa a tocar e publica a notificação.
        // Evita iniciar o prazo de startForegroundService enquanto o Media3 ainda
        // prepara o arquivo ou quando a fila não consegue iniciar.
        context.startService(intent);
    }

    private static void command(Context context, String action, long positionMs) {
        PlaybackMediaService service = instance;
        if (service != null) {
            service.postCommand(action, null, positionMs);
        } else if (Build.VERSION.SDK_INT < 26) {
            context.startService(new Intent(context, PlaybackMediaService.class).setAction(action)
                    .putExtra(EXTRA_POSITION, positionMs));
        }
    }

    @Override public void onCreate() {
        super.onCreate();
        instance = this;
        player = new ExoPlayer.Builder(this)
                .setWakeMode(C.WAKE_MODE_LOCAL)
                .build();
        AudioAttributes audioAttributes = new AudioAttributes.Builder()
                .setUsage(C.USAGE_MEDIA)
                .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                .build();
        player.setAudioAttributes(audioAttributes, true);
        player.setHandleAudioBecomingNoisy(true);
        player.setRepeatMode(Player.REPEAT_MODE_ALL);
        player.addListener(new Player.Listener() {
            @Override public void onMediaItemTransition(@Nullable MediaItem item, int reason) {
                String newId = item == null ? "" : item.mediaId;
                boolean faixaTerminou = reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO;
                if (faixaTerminou && !trackedMediaId.isEmpty()) {
                    saveProgress(true, true);
                } else if (!trackedMediaId.isEmpty() && !trackedMediaId.equals(newId)) {
                    saveProgress(false, true);
                }
                if (faixaTerminou || !newId.equals(trackedMediaId)) beginTracking(item);
                savePosition();
                updateSnapshot();
            }

            @Override public void onIsPlayingChanged(boolean isPlaying) {
                sampleListenedTime();
                if (!isPlaying) { saveProgress(false, true); savePosition(); }
                if (isPlaying) consecutiveErrors = 0;
                updateSnapshot();
            }

            @Override public void onPlaybackStateChanged(int playbackState) {
                sampleListenedTime();
                if (playbackState == Player.STATE_ENDED) saveProgress(true, true);
                updateSnapshot();
            }

            @Override public void onPositionDiscontinuity(
                    Player.PositionInfo oldPosition,
                    Player.PositionInfo newPosition,
                    int reason) {
                // Uma busca altera a posição sem reproduzir o trecho pulado.
                resetPlaybackSample();
            }

            @Override public void onPlayerError(PlaybackException error) {
                Log.e(TAG, "Falha ao reproduzir faixa", error);
                try {
                    consecutiveErrors++;
                    if (consecutiveErrors >= Math.max(1, player.getMediaItemCount())) {
                        player.pause();          // todas falharam: não entra em loop
                        consecutiveErrors = 0;
                    } else if (player.hasNextMediaItem()) {
                        player.seekToNextMediaItem();
                        player.prepare();
                        player.play();
                    }
                } catch (Throwable ignored) { }
                updateSnapshot();
            }
        });
        MediaSession.Builder sessionBuilder = new MediaSession.Builder(this, player);
        Intent sessionIntent = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (sessionIntent != null) {
            sessionIntent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent sessionActivity = PendingIntent.getActivity(
                    this,
                    0,
                    sessionIntent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
            );
            sessionBuilder.setSessionActivity(sessionActivity);
        }
        mediaSession = sessionBuilder.build();
        mainHandler.post(stateUpdater);
    }

    @Nullable @Override public MediaSession onGetSession(MediaSession.ControllerInfo controllerInfo) {
        return mediaSession;
    }

    @Override public int onStartCommand(@Nullable Intent intent, int flags, int startId) {
        if (intent != null) {
            String action = intent.getAction();
            String json = intent.getStringExtra(EXTRA_QUEUE);
            long positionMs = intent.getLongExtra(EXTRA_POSITION, 0L);
            String restore = intent.getStringExtra(EXTRA_RESTORE);
            if (restore != null) restoreId = restore;
            postCommand(action, json, positionMs);
        } else if (player != null && player.getMediaItemCount() == 0) {
            android.content.SharedPreferences preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
            String saved = preferences.getString(PREF_QUEUE, "");
            if (!saved.isEmpty()) {
                long position = preferences.getLong(PREF_POSITION, 0L);
                restoreId = preferences.getString(PREF_MEDIA_ID, "");
                String action = preferences.getBoolean(PREF_PLAYING, false)
                        ? ACTION_START : ACTION_PREPARE;
                postCommand(action, saved, position);
            }
        }
        return super.onStartCommand(intent, flags, startId);
    }

    private void postCommand(@Nullable String action, @Nullable String json, long positionMs) {
        if (action == null) return;
        mainHandler.post(() -> {
            try {
                if (ACTION_START.equals(action)) {
                    applyQueue(json, positionMs, true, false);
                } else if (ACTION_PREPARE.equals(action)) {
                    applyQueue(json, positionMs, false, false);
                } else if (ACTION_SYNC.equals(action)) {
                    applyQueue(json, positionMs, snapshotPlaying, true);
                } else if (ACTION_PLAY.equals(action)) {
                    if (player.getMediaItemCount() > 0) player.play();
                } else if (ACTION_PAUSE.equals(action)) {
                    saveProgress(false, true);
                    player.pause();
                } else if (ACTION_SEEK.equals(action)) {
                    player.seekTo(positionMs);
                } else if (ACTION_STOP.equals(action)) {
                    saveProgress(false, true);
                    player.pause();
                    player.clearMediaItems();
                    trackedMediaId = "";
                    trackedRowId = -1L;
                    lastQueueJson = "";
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply();
                    updateSnapshot();
                }
            } catch (Exception error) {
                Log.e(TAG, "Não foi possível aplicar comando " + action, error);
            }
        });
    }

    private void applyQueue(@Nullable String json, long requestedPositionMs,
                            boolean autoplay, boolean preserveCurrent) throws Exception {
        if (json == null || json.isEmpty()) return;
        JSONObject root = new JSONObject(json);
        JSONArray tracks = root.getJSONArray("tracks");
        ArrayList<MediaItem> items = new ArrayList<>();
        int requestedIndex = Math.max(0, root.optInt("index", 0));
        int startIndex = -1;
        String oldId = preserveCurrent && player.getCurrentMediaItem() != null
                ? player.getCurrentMediaItem().mediaId : "";
        long oldPosition = preserveCurrent ? Math.max(0L, player.getCurrentPosition()) : requestedPositionMs;
        boolean shouldPlay = preserveCurrent ? player.isPlaying() : autoplay;

        for (int i = 0; i < tracks.length(); i++) {
            JSONObject track = tracks.getJSONObject(i);
            String id = track.optString("id", "");
            String path = track.optString("path", "");
            if (id.isEmpty() || path.isEmpty() || !new File(path).isFile()) continue;
            String title = track.optString("title", "My Music Gospel");
            MediaMetadata.Builder metadata = new MediaMetadata.Builder()
                    .setTitle(title)
                    .setDisplayTitle(title)
                    .setArtist(track.optString("artist", ""))
                    .setAlbumTitle(track.optString("album", ""));
            String artworkPath = track.optString("artwork", "");
            if (!artworkPath.isEmpty() && new File(artworkPath).isFile()) {
                metadata.setArtworkUri(Uri.fromFile(new File(artworkPath)));
            }
            MediaItem item = new MediaItem.Builder()
                    .setMediaId(id)
                    .setUri(Uri.fromFile(new File(path)))
                    .setMediaMetadata(metadata.build())
                    .build();
            if (id.equals(oldId)) startIndex = items.size();
            items.add(item);
        }
        if (items.isEmpty()) {
            player.clearMediaItems();
            updateSnapshot();
            return;
        }
        if (startIndex < 0) {
            startIndex = Math.min(requestedIndex, items.size() - 1);
            oldPosition = Math.max(0L, requestedPositionMs);
            if (!restoreId.isEmpty()) {            // retoma na faixa em que estava
                for (int i = 0; i < items.size(); i++) {
                    if (items.get(i).mediaId.equals(restoreId)) { startIndex = i; break; }
                }
            }
        }
        restoreId = "";
        lastQueueJson = json;
        activeUser = root.optString("user", "");
        databasePath = root.optString("database", "");
        if (!preserveCurrent || !oldId.equals(items.get(startIndex).mediaId)) {
            trackedMediaId = "";
            trackedRowId = -1L;
            beginTracking(items.get(startIndex));
        }
        player.setMediaItems(items, startIndex, oldPosition);
        player.prepare();
        if (shouldPlay) player.play(); else player.pause();
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString(PREF_QUEUE, json).apply();
        updateSnapshot();
    }

    private void beginTracking(@Nullable MediaItem item) {
        trackedMediaId = item == null ? "" : item.mediaId;
        trackedUser = activeUser;
        trackedStartedAtMs = System.currentTimeMillis();
        trackedListenedMs = 0L;
        trackedRowId = -1L;
        lastStatsWriteAtMs = 0L;
        resetPlaybackSample();
    }

    private void resetPlaybackSample() {
        if (player == null) {
            lastSamplePositionMs = 0L;
            lastSampleWasPlaying = false;
        } else {
            lastSamplePositionMs = Math.max(0L, player.getCurrentPosition());
            lastSampleWasPlaying = player.isPlaying();
        }
        lastSampleAtElapsedMs = SystemClock.elapsedRealtime();
    }

    private void sampleListenedTime() {
        if (player == null || trackedMediaId.isEmpty()
                || player.getCurrentMediaItem() == null
                || !trackedMediaId.equals(player.getCurrentMediaItem().mediaId)) {
            resetPlaybackSample();
            return;
        }

        long now = SystemClock.elapsedRealtime();
        long position = Math.max(0L, player.getCurrentPosition());
        long elapsed = Math.max(0L, now - lastSampleAtElapsedMs);
        long advance = position - lastSamplePositionMs;
        if (lastSampleWasPlaying && advance > 0L && advance <= elapsed + 250L) {
            trackedListenedMs += advance;
        }
        lastSamplePositionMs = position;
        lastSampleAtElapsedMs = now;
        lastSampleWasPlaying = player.isPlaying();
    }

    private void saveProgress(boolean complete) {
        saveProgress(complete, complete);
    }

    private void saveProgress(boolean complete, boolean force) {
        if (player == null || trackedMediaId.isEmpty()) return;
        final long seconds = trackedListenedMs / 1000L;
        if (seconds <= 0 || databasePath.isEmpty()) return;
        long elapsed = SystemClock.elapsedRealtime();
        if (!force && elapsed - lastStatsWriteAtMs < 5000L) return;
        lastStatsWriteAtMs = elapsed;
        final String mediaId = trackedMediaId;
        final String user = trackedUser;
        final long startedAt = trackedStartedAtMs;
        final String dbPath = databasePath;
        try {
            dbExecutor.execute(() -> writeProgress(dbPath, mediaId, user, startedAt, seconds, complete));
        } catch (Exception error) {
            Log.w(TAG, "Fila de gravação indisponível", error);
        }
    }

    private void writeProgress(String dbPath, String mediaId, String user,
                               long startedAt, long seconds, boolean complete) {
        SQLiteDatabase database = null;
        try {
            long musicId = Long.parseLong(mediaId);
            database = SQLiteDatabase.openDatabase(dbPath, null, SQLiteDatabase.OPEN_READWRITE);
            database.execSQL("PRAGMA busy_timeout = 3000");
            ContentValues values = new ContentValues();
            values.put("segundos_ouvidos", seconds);
            values.put("completa", complete ? 1 : 0);
            Long rowId = rowIdByStart.get(startedAt);
            if (rowId == null) {
                values.put("musica_id", musicId);
                values.put("usuario", user);
                values.put("reproduzida_em", new SimpleDateFormat(
                        "yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(new Date(startedAt)));
                long id = database.insertOrThrow("historico", null, values);
                if (rowIdByStart.size() > 8) rowIdByStart.clear();
                rowIdByStart.put(startedAt, id);
            } else {
                database.update("historico", values, "id = ?", new String[]{Long.toString(rowId)});
            }
        } catch (Throwable error) {
            Log.w(TAG, "Não foi possível registrar escuta no banco do app", error);
        } finally {
            if (database != null) {
                try { database.close(); } catch (Throwable ignored) { }
            }
        }
    }

    private void savePosition() {
        if (player == null || player.getCurrentMediaItem() == null) return;
        long position = Math.max(0L, player.getCurrentPosition());
        lastPositionSaveAt = SystemClock.elapsedRealtime();
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putLong(PREF_POSITION, position)
                .putString(PREF_MEDIA_ID, player.getCurrentMediaItem().mediaId)
                .putBoolean(PREF_PLAYING, player.isPlaying()).apply();
    }

    private void updateSnapshot() {
        if (player == null) return;
        MediaItem item = player.getCurrentMediaItem();
        snapshotHasItem = item != null;
        snapshotMediaId = item == null ? "" : item.mediaId;
        snapshotPositionMs = item == null ? 0L : Math.max(0L, player.getCurrentPosition());
        long duration = item == null ? 0L : player.getDuration();
        snapshotDurationMs = duration == C.TIME_UNSET || duration < 0 ? 0L : duration;
        snapshotPlaying = player.isPlaying();
    }

    /** Nunca deixa a falha de promoção a primeiro plano derrubar o app em segundo plano. */
    @Override public void onUpdateNotification(MediaSession session, boolean startInForegroundRequired) {
        try {
            super.onUpdateNotification(session, startInForegroundRequired);
        } catch (Throwable error) {
            Log.w(TAG, "Notificação de mídia adiada", error);
            mainHandler.postDelayed(() -> {
                try { super.onUpdateNotification(session, false); } catch (Throwable ignored) { }
            }, 1000L);
        }
    }

    /** Fechar o app pelos recentes não interrompe a música que está tocando. */
    @Override public void onTaskRemoved(@Nullable Intent rootIntent) {
        if (player == null || !player.getPlayWhenReady() || player.getMediaItemCount() == 0) {
            stopSelf();
        }
    }

    @Override public void onDestroy() {
        mainHandler.removeCallbacks(stateUpdater);
        saveProgress(false, true);
        if (mediaSession != null) {
            mediaSession.release();
            mediaSession = null;
        }
        if (player != null) {
            player.release();
            player = null;
        }
        if (instance == this) instance = null;
        snapshotMediaId = "";
        snapshotPositionMs = 0L;
        snapshotDurationMs = 0L;
        snapshotPlaying = false;
        snapshotHasItem = false;
        dbExecutor.shutdown();
        super.onDestroy();
    }
}
