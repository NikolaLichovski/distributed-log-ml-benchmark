-- Core log storage table
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    service VARCHAR(100) NOT NULL,
    log_level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Anomaly detection results
CREATE TABLE IF NOT EXISTS anomalies (
    id SERIAL PRIMARY KEY,
    log_id INT REFERENCES logs(id) ON DELETE CASCADE,
    is_anomaly BOOLEAN NOT NULL,
    anomaly_score FLOAT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Experimental run tracking
CREATE TABLE IF NOT EXISTS experiments (
    id BIGINT PRIMARY KEY,
    experiment_name VARCHAR(255) NOT NULL,
    num_producers INT NOT NULL,
    num_consumers INT NOT NULL,
    num_partitions INT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    status VARCHAR(50) DEFAULT 'running',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT status_check CHECK (status IN ('running', 'completed', 'failed', 'stopped'))
);

-- Performance metrics collection
CREATE TABLE IF NOT EXISTS performance_metrics (
    id SERIAL PRIMARY KEY,
    experiment_id BIGINT REFERENCES experiments(id) ON DELETE CASCADE,
    component_type VARCHAR(50) NOT NULL,
    component_id VARCHAR(100) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT component_type_check CHECK (component_type IN ('producer', 'consumer', 'system'))
);

-- Consumer lag tracking for performance analysis
CREATE TABLE IF NOT EXISTS consumer_lag (
    id SERIAL PRIMARY KEY,
    experiment_id BIGINT REFERENCES experiments(id) ON DELETE CASCADE,
    consumer_id VARCHAR(100) NOT NULL,
    partition INT NOT NULL,
    current_offset BIGINT NOT NULL,
    log_end_offset BIGINT NOT NULL,
    lag BIGINT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Optimized indexes for query performance
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_service ON logs(service);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(log_level);
CREATE INDEX IF NOT EXISTS idx_anomalies_detected_at ON anomalies(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_is_anomaly ON anomalies(is_anomaly) WHERE is_anomaly = true;
CREATE INDEX IF NOT EXISTS idx_performance_metrics_experiment ON performance_metrics(experiment_id);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_timestamp ON performance_metrics(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_component ON performance_metrics(component_type, component_id);
CREATE INDEX IF NOT EXISTS idx_consumer_lag_experiment ON consumer_lag(experiment_id);
CREATE INDEX IF NOT EXISTS idx_consumer_lag_timestamp ON consumer_lag(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_start_time ON experiments(start_time DESC);

-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_performance_metrics_exp_component ON performance_metrics(experiment_id, component_type, metric_name);
CREATE INDEX IF NOT EXISTS idx_consumer_lag_exp_consumer ON consumer_lag(experiment_id, consumer_id, partition);