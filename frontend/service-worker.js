self.addEventListener('install', function(event) {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', function(event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function(event) {
    if (event.data) {
        try {
            const data = event.data.json();
            const title = data.title || 'New Notification';
            const options = {
                body: data.body || '',
                icon: '/static/icons/icon-192.png',
                badge: '/static/icons/icon-192.png',
                vibrate: [200, 100, 200]
            };
            event.waitUntil(self.registration.showNotification(title, options));
        } catch (e) {
            console.error('Push event error:', e);
        }
    }
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window" }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                let client = clientList[i];
                if (client.url.indexOf('/') !== -1 && 'focus' in client)
                    return client.focus();
            }
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});
