package com.recoact.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

/**
 * 녹음 중 OS가 앱 프로세스를 죽이지 않도록 마이크 foreground service를 유지한다.
 * 실제 오디오 캡처는 capacitor-voice-recorder가 처리하며, 이 서비스는 알림과
 * 프로세스 우선순위 유지를 담당한다.
 */
public class RecordingService extends Service {
    private static final String CHANNEL_ID = "reco_act_recording";
    private static final int NOTIF_ID = 1001;

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "회의 녹음",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("회의 녹음 진행 중 알림");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Intent launchIntent = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent contentIntent = launchIntent != null
            ? PendingIntent.getActivity(this, 0, launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE)
            : null;

        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Reco-Act")
            .setContentText("회의를 녹음하는 중입니다")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .setContentIntent(contentIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build();

        startForeground(NOTIF_ID, notification);
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        stopForeground(STOP_FOREGROUND_REMOVE);
    }
}
