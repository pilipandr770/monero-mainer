/**
 * Локальный браузерный майнер для Monero
 * Упрощённая версия для демонстрации концепции
 * В production замените на полноценный WASM с CryptoNight
 */

class LocalMiner {
    constructor(config) {
        this.wallet = config.wallet;
        this.pool = config.pool || 'gulf.moneroocean.stream:10004';
        this.threads = config.threads || navigator.hardwareConcurrency || 4;
        this.throttle = config.throttle || 0.3;
        this.worker = config.worker || 'web' + Math.random().toString(36).substr(2, 9);
        
        this.isRunning = false;
        this.hashrate = 0;
        this.totalHashes = 0;
        this.acceptedShares = 0;
        
        this.workers = [];
        this.workerHashes = new Array(this.threads).fill(0); // хранит хеши каждого воркера
        this.startTime = null;

        console.log('🔧 LocalMiner инициализирован:', {
            wallet: this.wallet.substr(0, 10) + '...',
            threads: this.threads,
            throttle: this.throttle
        });
    }
    
    start() {
        if (this.isRunning) {
            console.warn('Майнер уже запущен');
            return;
        }
        
        this.isRunning = true;
        this.startTime = Date.now();
        
        // Создаём Web Workers для многопоточности
        for (let i = 0; i < this.threads; i++) {
            this.createWorkerThread(i);
        }
        
        // Запускаем мониторинг
        this.startMonitoring();
        
        console.log('✅ Майнер запущен на', this.threads, 'потоках');
    }

    createWorkerThread(threadId) {
        // Симуляция майнинга (в production здесь будет WASM с CryptoNight)
        // Уменьшаем базовый хешрейт для реалистичности: 5-30 H/s на поток
        const baseSpeed = 5 + Math.random() * 25;
        const workerCode = `
            let hashes = 0;
            let throttle = ${this.throttle};
            let baseSpeed = ${baseSpeed};

            function mine() {
                // Генерируем некоторое количество хешей за такт
                const generated = Math.floor(baseSpeed * (1 - throttle));
                if (generated > 0) {
                    hashes += generated;
                    postMessage({ type: 'hashes', value: hashes });
                }

                // Throttle контролирует задержку между итерациями
                const delay = Math.max(10, Math.floor(1000 * throttle));
                setTimeout(mine, delay);
            }

            mine();
        `;
        
        const blob = new Blob([workerCode], { type: 'application/javascript' });
        const worker = new Worker(URL.createObjectURL(blob));
        
        worker.onmessage = (e) => {
            if (e.data.type === 'hashes') {
                // Сохраняем значение для конкретного воркера и суммируем
                this.workerHashes[threadId] = e.data.value;
                this.totalHashes = this.workerHashes.reduce((a, b) => a + b, 0);
        };
        
        this.workers.push(worker);
    }
    
    startMonitoring() {
        let lastTotal = 0;
        let lastTime = Date.now();

        this.monitoringInterval = setInterval(() => {
            const now = Date.now();
            const elapsed = (now - lastTime) / 1000; // секунды
            const currentTotal = this.totalHashes;
            const diff = currentTotal - lastTotal;

            // Хешрейт в H/s
            this.hashrate = elapsed > 0 ? (diff / elapsed) : 0;
            lastTotal = currentTotal;
            lastTime = now;

            // Симуляция отправки шар на пул (редко)
            if (Math.random() > 0.95 && this.hashrate > 0) {
                this.acceptedShares++;
                console.log('✓ Шара принята пулом. Всего:', this.acceptedShares);
            }

            // Логирование
            if (this.hashrate > 0) {
                console.log(`⛏️ Майнинг: ${this.hashrate.toFixed(2)} H/s | Шары: ${this.acceptedShares}`);
            }
        }, 2000);
    }
    
    stop() {
        if (!this.isRunning) return;
        
        this.isRunning = false;
        
        // Останавливаем workers
        this.workers.forEach(worker => worker.terminate());
        this.workers = [];
        
        // Останавливаем мониторинг
        if (this.monitoringInterval) {
            clearInterval(this.monitoringInterval);
        }
        
        console.log('⏹️ Майнер остановлен');
    }
    
    getHashrate() {
        return this.hashrate;
    }
    
    getTotalHashes() {
        return this.totalHashes;
    }
    
    getAcceptedShares() {
        return this.acceptedShares;
    }
    
    getStats() {
        return {
            hashrate: this.hashrate,
            totalHashes: this.totalHashes,
            acceptedShares: this.acceptedShares,
            uptime: this.startTime ? Math.floor((Date.now() - this.startTime) / 1000) : 0
        };
    }
}

// Экспортируем для использования в браузере
window.LocalMiner = LocalMiner;

console.log('📦 LocalMiner загружен');
