import time
import json
import random
import logging
import os
import sys
from datetime import datetime
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
import socket

# Add shared module to path
sys.path.insert(0, '/app/shared')
from metrics_collector import MetricsCollector, PerformanceMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'logs')
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9094')
EXPERIMENT_ID = int(os.getenv('EXPERIMENT_ID', '0'))
PRODUCER_ID = os.getenv('PRODUCER_ID', socket.gethostname())
LOG_RATE = float(os.getenv('LOG_RATE', '1.0'))  # Messages per second

# Database configuration for metrics
POSTGRES_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'db'),
    'database': os.getenv('POSTGRES_DB', 'logsdb'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', 'postgres')
}

# Service simulation data
services = ['auth-service', 'payment-service', 'inventory-service', 'user-service', 'notification-service']
log_levels = ['INFO', 'WARN', 'ERROR']

log_messages = {
    'INFO': [
        "Request processed successfully",
        "User authentication completed",
        "Payment transaction approved",
        "Inventory updated successfully",
        "Notification sent to user",
        "Database connection established",
        "Cache hit for user data",
        "API endpoint responded in {}ms",
        "Background job completed",
        "Session created for user"
    ],
    'WARN': [
        "High response time detected: {}ms",
        "Memory usage above 80%",
        "Retry attempt {} for failed request",
        "Rate limit approaching for user",
        "SSL certificate expires in {} days",
        "Database connection pool nearly full",
        "Disk usage above threshold",
        "Deprecated API endpoint used"
    ],
    'ERROR': [
        "Database connection failed",
        "Payment gateway timeout",
        "Authentication token expired",
        "Inventory service unavailable",
        "Failed to send notification",
        "Out of memory error",
        "Network connection lost",
        "API rate limit exceeded",
        "Transaction rollback failed"
    ]
}


def create_topic_if_not_exists(bootstrap_servers, topic_name, num_partitions=1):
    """
    Create Kafka topic with specified number of partitions.
    Recreates topic if partition count differs from existing.
    """
    admin_client = AdminClient({'bootstrap.servers': bootstrap_servers})

    try:
        metadata = admin_client.list_topics(timeout=10)

        if topic_name in metadata.topics:
            existing_partitions = len(metadata.topics[topic_name].partitions)

            if existing_partitions != num_partitions:
                logger.info(
                    f"Topic '{topic_name}' exists with {existing_partitions} partitions, expected {num_partitions}")
                logger.info(f"Deleting and recreating topic...")

                # Delete existing topic
                fs = admin_client.delete_topics([topic_name], operation_timeout=30)
                for topic, f in fs.items():
                    try:
                        f.result()
                        logger.info(f"Deleted topic '{topic}'")
                    except Exception as e:
                        logger.warning(f"Could not delete topic '{topic}': {e}")

                time.sleep(5)  # Wait for deletion to propagate
            else:
                logger.info(f"Topic '{topic_name}' already exists with {num_partitions} partitions")
                return True

        # Create topic with specified partitions
        topic = NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
        fs = admin_client.create_topics([topic])

        for topic, f in fs.items():
            try:
                f.result()
                logger.info(f"Created topic '{topic}' with {num_partitions} partitions")
                return True
            except Exception as e:
                if 'already exists' not in str(e).lower():
                    logger.error(f"Failed to create topic '{topic}': {e}")
                    return False
                return True

    except Exception as e:
        logger.error(f"Error managing topic: {e}")
        return False


def generate_log():
    """Generate a realistic log entry with occasional anomalies."""
    service = random.choice(services)

    # Inject anomalies ~5% of the time
    if random.random() < 0.05:
        level = 'ERROR'
        message = random.choice(log_messages['ERROR'])
        if random.random() < 0.5:
            message += f" - Unusual error code: {random.randint(9000, 9999)}"
    else:
        level = random.choices(log_levels, weights=[0.8, 0.15, 0.05])[0]
        message = random.choice(log_messages[level])

        # Fill placeholders in messages
        if '{}' in message:
            if 'response time' in message or 'responded in' in message:
                message = message.format(random.randint(50, 500))
            elif 'Retry attempt' in message:
                message = message.format(random.randint(2, 5))
            elif 'expires in' in message:
                message = message.format(random.randint(1, 30))

    log = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'service': service,
        'log_level': level,
        'message': f"{service}: {message}"
    }
    return log


class MetricsAwareProducer:
    """
    Kafka producer with integrated performance monitoring.
    Tracks message production latency and throughput.
    """

    def __init__(self, producer_config, experiment_id, producer_id, db_config):
        self.producer = Producer(producer_config)
        self.experiment_id = experiment_id
        self.producer_id = producer_id

        # Initialize metrics collection if experiment is active
        self.metrics_enabled = experiment_id > 0
        if self.metrics_enabled:
            self.metrics_collector = MetricsCollector(
                experiment_id=experiment_id,
                component_type='producer',
                component_id=producer_id,
                db_config=db_config,
                batch_size=50,
                flush_interval=10.0
            )
            self.performance_monitor = PerformanceMonitor(self.metrics_collector)
        else:
            logger.info("Metrics collection disabled (no experiment ID)")

        self.sent_times = {}  # Track send timestamps for latency calculation

    def delivery_callback(self, err, msg):
        """Handle delivery reports and calculate message latency."""
        if err:
            logger.error(f'Message delivery failed: {err}')
        else:
            # Calculate latency if we tracked the send time
            key = msg.key().decode('utf-8') if msg.key() else None
            if key and key in self.sent_times:
                latency_ms = (time.time() - self.sent_times[key]) * 1000
                del self.sent_times[key]

                if self.metrics_enabled:
                    self.performance_monitor.record_message(latency_ms)

                logger.debug(f'Message delivered to {msg.topic()} [{msg.partition()}] in {latency_ms:.2f}ms')
            else:
                if self.metrics_enabled:
                    self.performance_monitor.record_message()

    def send_message(self, topic, key, value):
        """Send a message and track send time for latency measurement."""
        send_time = time.time()

        # Store send time for latency calculation
        if key:
            self.sent_times[key] = send_time

        self.producer.produce(
            topic,
            key=key,
            value=value,
            callback=self.delivery_callback
        )
        self.producer.poll(0)

    def flush(self):
        """Flush pending messages."""
        self.producer.flush(timeout=10)

    def shutdown(self):
        """Shutdown producer and metrics collection."""
        logger.info("Shutting down producer...")
        self.flush()

        if self.metrics_enabled:
            self.performance_monitor.shutdown()
            self.metrics_collector.shutdown()


def wait_for_kafka(producer, max_retries=60):
    """Wait for Kafka broker to be available."""
    for attempt in range(max_retries):
        try:
            metadata = producer.list_topics(timeout=5)
            logger.info("Kafka broker is available")
            return True
        except Exception as e:
            logger.warning(f"Waiting for Kafka broker... attempt {attempt + 1}/{max_retries}: {e}")
            time.sleep(2)
    return False


def main():
    """Main producer loop with rate limiting and metrics collection."""
    logger.info(f"Starting log producer: {PRODUCER_ID}")
    logger.info(f"Experiment ID: {EXPERIMENT_ID}")
    logger.info(f"Target rate: {LOG_RATE} messages/second")

    producer_conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'client.id': PRODUCER_ID,
        'acks': 'all',
        'retries': 5,
        'retry.backoff.ms': 1000,
        'request.timeout.ms': 30000,
        'message.timeout.ms': 30000,
        'compression.type': 'snappy'
    }

    # Create base producer for initialization
    base_producer = Producer(producer_conf)

    # Wait for Kafka to be ready
    logger.info("Waiting for Kafka broker...")
    if not wait_for_kafka(base_producer):
        logger.error("Kafka broker not available after waiting")
        sys.exit(1)

    # Get partition count from environment
    num_partitions = int(os.getenv('KAFKA_NUM_PARTITIONS', '3'))

    # Create or verify topic
    logger.info(f"Setting up topic '{KAFKA_TOPIC}' with {num_partitions} partitions...")
    if not create_topic_if_not_exists(KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, num_partitions):
        logger.error("Failed to create topic")
        sys.exit(1)

    time.sleep(5)  # Allow topic to propagate

    # Initialize metrics-aware producer
    producer = MetricsAwareProducer(
        producer_conf,
        EXPERIMENT_ID,
        PRODUCER_ID,
        POSTGRES_CONFIG
    )

    logger.info("Producer started successfully")

    try:
        message_count = 0

        while True:
            loop_start = time.time()

            # Generate and send log
            log = generate_log()
            message_key = f"{PRODUCER_ID}:{message_count}"

            producer.send_message(
                KAFKA_TOPIC,
                key=message_key,
                value=json.dumps(log)
            )

            message_count += 1

            if message_count % 100 == 0:
                if producer.metrics_enabled:
                    throughput = producer.performance_monitor.get_total_throughput()
                    logger.info(f"Produced {message_count} messages (avg throughput: {throughput:.2f} msg/s)")
                else:
                    logger.info(f"Produced {message_count} messages")

            # Rate limiting
            elapsed = time.time() - loop_start
            target_interval = 1.0 / LOG_RATE
            sleep_time = target_interval - elapsed

            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Producer error: {e}", exc_info=True)
    finally:
        producer.shutdown()
        logger.info("Producer shutdown complete")


if __name__ == "__main__":
    main()