import streamlit as st
import psycopg2
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import time
import os

# Database configuration
POSTGRES_CONN = {
    'host': os.getenv('POSTGRES_HOST', 'db'),
    'database': os.getenv('POSTGRES_DB', 'logsdb'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', 'postgres')
}


def get_db_connection():
    """Get database connection with error handling."""
    try:
        return psycopg2.connect(**POSTGRES_CONN)
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return None


@st.cache_data(ttl=10)
def get_recent_logs(limit=100):
    """Fetch recent logs with anomaly information."""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT 
                l.id, 
                l.timestamp, 
                l.service, 
                l.log_level, 
                l.message,
                COALESCE(a.is_anomaly, false) as is_anomaly,
                COALESCE(a.anomaly_score, 0.5) as anomaly_score,
                a.detected_at
            FROM logs l
            LEFT JOIN anomalies a ON l.id = a.log_id
            ORDER BY l.timestamp DESC
            LIMIT %s
        """
        df = pd.read_sql_query(query, conn, params=[limit])
        conn.close()
        return df
    except Exception as e:
        st.error(f"Error fetching logs: {e}")
        conn.close()
        return pd.DataFrame()


@st.cache_data(ttl=30)
def get_anomaly_stats():
    """Get anomaly statistics."""
    conn = get_db_connection()
    if conn is None:
        return {}

    try:
        query = """
            SELECT 
                COUNT(*) as total_logs,
                COUNT(CASE WHEN a.is_anomaly = true THEN 1 END) as anomalies,
                AVG(CASE WHEN a.is_anomaly = true THEN 1.0 ELSE 0.0 END) * 100 as anomaly_rate
            FROM logs l
            LEFT JOIN anomalies a ON l.id = a.log_id
            WHERE l.timestamp >= NOW() - INTERVAL '1 hour'
        """
        result = pd.read_sql_query(query, conn)
        conn.close()

        return {
            'total_logs': int(result.iloc[0]['total_logs']),
            'anomalies': int(result.iloc[0]['anomalies']),
            'anomaly_rate': float(result.iloc[0]['anomaly_rate'] or 0)
        }
    except Exception as e:
        st.error(f"Error fetching stats: {e}")
        conn.close()
        return {'total_logs': 0, 'anomalies': 0, 'anomaly_rate': 0}


@st.cache_data(ttl=10)
def get_active_experiment():
    """Get currently running experiment."""
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        query = """
            SELECT * FROM experiments 
            WHERE status = 'running' 
            ORDER BY start_time DESC 
            LIMIT 1
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if not df.empty:
            return df.iloc[0].to_dict()
        return None
    except Exception as e:
        conn.close()
        return None


@st.cache_data(ttl=30)
def get_all_experiments():
    """Get all experiments."""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT 
                id,
                experiment_name,
                num_producers,
                num_consumers,
                num_partitions,
                start_time,
                end_time,
                status,
                notes
            FROM experiments
            ORDER BY start_time DESC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        conn.close()
        return pd.DataFrame()


@st.cache_data(ttl=30)
def get_experiment_metrics(experiment_id):
    """Get performance metrics for a specific experiment."""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT 
                component_type,
                component_id,
                metric_name,
                metric_value,
                timestamp
            FROM performance_metrics
            WHERE experiment_id = %s
            ORDER BY timestamp
        """
        df = pd.read_sql_query(query, conn, params=[experiment_id])
        conn.close()
        return df
    except Exception as e:
        conn.close()
        return pd.DataFrame()


@st.cache_data(ttl=30)
def get_experiment_lag(experiment_id):
    """Get consumer lag data for a specific experiment."""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT 
                consumer_id,
                partition,
                lag,
                timestamp
            FROM consumer_lag
            WHERE experiment_id = %s
            ORDER BY timestamp
        """
        df = pd.read_sql_query(query, conn, params=[experiment_id])
        conn.close()
        return df
    except Exception as e:
        conn.close()
        return pd.DataFrame()


@st.cache_data(ttl=30)
def get_experiment_summary(experiment_id):
    """Get summary statistics for an experiment."""
    conn = get_db_connection()
    if conn is None:
        return {}

    try:
        # Get message counts
        logs_query = """
            SELECT COUNT(*) as total_logs
            FROM logs
            WHERE created_at >= (SELECT start_time FROM experiments WHERE id = %s)
            AND created_at <= COALESCE((SELECT end_time FROM experiments WHERE id = %s), NOW())
        """
        logs_df = pd.read_sql_query(logs_query, conn, params=[experiment_id, experiment_id])

        # Get anomaly counts
        anomaly_query = """
            SELECT COUNT(*) as total_anomalies
            FROM anomalies a
            JOIN logs l ON a.log_id = l.id
            WHERE l.created_at >= (SELECT start_time FROM experiments WHERE id = %s)
            AND l.created_at <= COALESCE((SELECT end_time FROM experiments WHERE id = %s), NOW())
            AND a.is_anomaly = true
        """
        anomaly_df = pd.read_sql_query(anomaly_query, conn, params=[experiment_id, experiment_id])

        # Get average metrics
        metrics_query = """
            SELECT 
                metric_name,
                AVG(metric_value) as avg_value,
                MAX(metric_value) as max_value,
                MIN(metric_value) as min_value
            FROM performance_metrics
            WHERE experiment_id = %s
            GROUP BY metric_name
        """
        metrics_df = pd.read_sql_query(metrics_query, conn, params=[experiment_id])

        # Get max lag
        lag_query = """
            SELECT MAX(lag) as max_lag, AVG(lag) as avg_lag
            FROM consumer_lag
            WHERE experiment_id = %s
        """
        lag_df = pd.read_sql_query(lag_query, conn, params=[experiment_id])

        conn.close()

        summary = {
            'total_logs': int(logs_df.iloc[0]['total_logs']),
            'total_anomalies': int(anomaly_df.iloc[0]['total_anomalies']),
            'max_lag': int(lag_df.iloc[0]['max_lag']) if not pd.isna(lag_df.iloc[0]['max_lag']) else 0,
            'avg_lag': float(lag_df.iloc[0]['avg_lag']) if not pd.isna(lag_df.iloc[0]['avg_lag']) else 0,
        }

        # Add metrics
        for _, row in metrics_df.iterrows():
            summary[f"{row['metric_name']}_avg"] = float(row['avg_value'])
            summary[f"{row['metric_name']}_max"] = float(row['max_value'])
            summary[f"{row['metric_name']}_min"] = float(row['min_value'])

        return summary
    except Exception as e:
        conn.close()
        return {}


def show_realtime_view():
    """Display real-time log monitoring view."""
    st.header("📊 Real-Time Log Monitoring")

    # Check for active experiment
    active_exp = get_active_experiment()
    if active_exp:
        st.info(f"🔬 Active Experiment: **{active_exp['experiment_name']}** (ID: {active_exp['id']}) - "
                f"{active_exp['num_producers']}P / {active_exp['num_consumers']}C / {active_exp['num_partitions']} partitions")

    # Get data
    df = get_recent_logs(log_limit)
    stats = get_anomaly_stats()

    if df.empty:
        st.warning("No data available. Make sure the producer and consumer are running.")
        return

    # Convert timestamp
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Metrics row
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Logs (1h)", stats['total_logs'])

    with col2:
        st.metric("Anomalies (1h)", stats['anomalies'])

    with col3:
        st.metric("Anomaly Rate", f"{stats['anomaly_rate']:.2f}%")

    with col4:
        st.metric("Services", df['service'].nunique())

    # Charts row
    col1, col2 = st.columns(2)

    with col1:
        # Log levels distribution
        fig_levels = px.pie(
            df.groupby('log_level').size().reset_index(name='count'),
            values='count',
            names='log_level',
            title="Log Levels Distribution",
            color_discrete_map={
                'INFO': '#00CC96',
                'WARN': '#FFA15A',
                'ERROR': '#EF553B'
            }
        )
        st.plotly_chart(fig_levels, use_container_width=True)

    with col2:
        # Services distribution
        fig_services = px.bar(
            df.groupby('service').size().reset_index(name='count').sort_values('count', ascending=False),
            x='service',
            y='count',
            title="Logs by Service"
        )
        fig_services.update_xaxes(tickangle=45)
        st.plotly_chart(fig_services, use_container_width=True)

    # Timeline chart
    df_timeline = df.set_index('timestamp').resample('1T').agg({
        'id': 'count',
        'is_anomaly': 'sum'
    }).reset_index()
    df_timeline.columns = ['timestamp', 'total_logs', 'anomalies']

    fig_timeline = go.Figure()
    fig_timeline.add_trace(go.Scatter(
        x=df_timeline['timestamp'],
        y=df_timeline['total_logs'],
        mode='lines+markers',
        name='Total Logs',
        line=dict(color='blue')
    ))
    fig_timeline.add_trace(go.Scatter(
        x=df_timeline['timestamp'],
        y=df_timeline['anomalies'],
        mode='lines+markers',
        name='Anomalies',
        line=dict(color='red'),
        yaxis='y2'
    ))

    fig_timeline.update_layout(
        title="Logs Timeline (per minute)",
        xaxis_title="Time",
        yaxis_title="Total Logs",
        yaxis2=dict(
            title="Anomalies",
            overlaying='y',
            side='right'
        ),
        hovermode='x unified'
    )

    st.plotly_chart(fig_timeline, use_container_width=True)

    # Recent anomalies
    st.subheader("🚨 Recent Anomalies")
    anomalies = df[df['is_anomaly'] == True].head(20)

    if anomalies.empty:
        st.success("No anomalies detected in recent logs!")
    else:
        for _, anomaly in anomalies.iterrows():
            with st.expander(
                    f"🔴 {anomaly['service']} - {anomaly['timestamp'].strftime('%H:%M:%S')} "
                    f"(Score: {anomaly['anomaly_score']:.3f})"
            ):
                st.write(f"**Service:** {anomaly['service']}")
                st.write(f"**Level:** {anomaly['log_level']}")
                st.write(f"**Time:** {anomaly['timestamp']}")
                st.write(f"**Message:** {anomaly['message']}")
                st.write(f"**Anomaly Score:** {anomaly['anomaly_score']:.3f}")

    # All logs table
    st.subheader("📋 Recent Logs")

    def highlight_anomalies(row):
        if row['is_anomaly']:
            return ['background-color: #ffebee'] * len(row)
        return [''] * len(row)

    display_df = df[['timestamp', 'service', 'log_level', 'message', 'is_anomaly', 'anomaly_score']].copy()
    display_df['timestamp'] = display_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')

    st.dataframe(
        display_df.style.apply(highlight_anomalies, axis=1),
        use_container_width=True,
        height=400
    )


def show_experiment_analysis():
    """Display experiment analysis and comparison."""
    st.header("🔬 Experiment Analysis")

    experiments_df = get_all_experiments()

    if experiments_df.empty:
        st.info("No experiments recorded yet. Run experiments using `./run_experiment.sh`")
        return

    # Experiment selection
    st.subheader("Select Experiments to Analyze")

    # Format experiment display
    experiments_df['display_name'] = (
            experiments_df['experiment_name'] + ' (ID: ' +
            experiments_df['id'].astype(str) + ') - ' +
            experiments_df['num_producers'].astype(str) + 'P/' +
            experiments_df['num_consumers'].astype(str) + 'C/' +
            experiments_df['num_partitions'].astype(str) + 'Part - ' +
            experiments_df['status']
    )

    selected_experiments = st.multiselect(
        "Choose experiments to compare",
        options=experiments_df['id'].tolist(),
        format_func=lambda x: experiments_df[experiments_df['id'] == x]['display_name'].iloc[0],
        default=[experiments_df['id'].iloc[0]] if not experiments_df.empty else []
    )

    if not selected_experiments:
        st.warning("Please select at least one experiment to analyze.")
        return

    # Show experiment details
    st.subheader("Experiment Configurations")

    comparison_data = []
    for exp_id in selected_experiments:
        exp_info = experiments_df[experiments_df['id'] == exp_id].iloc[0]
        summary = get_experiment_summary(exp_id)

        comparison_data.append({
            'Experiment': exp_info['experiment_name'],
            'ID': exp_id,
            'Producers': exp_info['num_producers'],
            'Consumers': exp_info['num_consumers'],
            'Partitions': exp_info['num_partitions'],
            'Status': exp_info['status'],
            'Total Logs': summary.get('total_logs', 0),
            'Anomalies': summary.get('total_anomalies', 0),
            'Avg Throughput': f"{summary.get('throughput_avg', 0):.2f}",
            'Avg Latency (ms)': f"{summary.get('latency_avg_avg', 0):.2f}",
            'Max Lag': summary.get('max_lag', 0)
        })

    comparison_df = pd.DataFrame(comparison_data)
    st.dataframe(comparison_df, use_container_width=True)

    # Detailed metrics for selected experiments
    if len(selected_experiments) == 1:
        show_single_experiment_details(selected_experiments[0])
    else:
        show_experiment_comparison(selected_experiments)


def show_single_experiment_details(experiment_id):
    """Show detailed analysis for a single experiment."""
    st.subheader("Detailed Metrics")

    metrics_df = get_experiment_metrics(experiment_id)
    lag_df = get_experiment_lag(experiment_id)

    if metrics_df.empty:
        st.warning("No performance metrics collected for this experiment.")
        return

    # Throughput over time
    st.markdown("### Throughput Over Time")
    throughput_df = metrics_df[metrics_df['metric_name'] == 'throughput']

    if not throughput_df.empty:
        fig = px.line(
            throughput_df,
            x='timestamp',
            y='metric_value',
            color='component_id',
            facet_row='component_type',
            title='Throughput by Component',
            labels={'metric_value': 'Messages/sec', 'timestamp': 'Time'}
        )
        st.plotly_chart(fig, use_container_width=True)

    # Latency distribution
    st.markdown("### Latency Distribution")
    latency_df = metrics_df[metrics_df['metric_name'].str.contains('latency')]

    if not latency_df.empty:
        fig = px.box(
            latency_df,
            x='component_id',
            y='metric_value',
            color='metric_name',
            title='Latency Distribution',
            labels={'metric_value': 'Latency (ms)'}
        )
        st.plotly_chart(fig, use_container_width=True)

    # Consumer lag over time
    if not lag_df.empty:
        st.markdown("### Consumer Lag Over Time")
        fig = px.line(
            lag_df,
            x='timestamp',
            y='lag',
            color='consumer_id',
            facet_col='partition',
            title='Consumer Lag by Partition',
            labels={'lag': 'Messages Behind', 'timestamp': 'Time'}
        )
        st.plotly_chart(fig, use_container_width=True)


def show_experiment_comparison(experiment_ids):
    """Compare multiple experiments."""
    st.subheader("Comparative Analysis")

    all_metrics = []
    for exp_id in experiment_ids:
        metrics_df = get_experiment_metrics(exp_id)
        if not metrics_df.empty:
            metrics_df['experiment_id'] = exp_id
            all_metrics.append(metrics_df)

    if not all_metrics:
        st.warning("No metrics available for comparison.")
        return

    combined_df = pd.concat(all_metrics, ignore_index=True)

    # Average throughput comparison
    st.markdown("### Average Throughput Comparison")
    throughput_summary = combined_df[combined_df['metric_name'] == 'throughput'].groupby(
        ['experiment_id', 'component_type']
    )['metric_value'].mean().reset_index()

    fig = px.bar(
        throughput_summary,
        x='experiment_id',
        y='metric_value',
        color='component_type',
        barmode='group',
        title='Average Throughput by Experiment',
        labels={'metric_value': 'Messages/sec', 'experiment_id': 'Experiment ID'}
    )
    st.plotly_chart(fig, use_container_width=True)

    # Latency comparison
    st.markdown("### Latency Comparison")
    latency_summary = combined_df[combined_df['metric_name'] == 'latency_avg'].groupby(
        'experiment_id'
    )['metric_value'].mean().reset_index()

    fig = px.bar(
        latency_summary,
        x='experiment_id',
        y='metric_value',
        title='Average Latency by Experiment',
        labels={'metric_value': 'Latency (ms)', 'experiment_id': 'Experiment ID'}
    )
    st.plotly_chart(fig, use_container_width=True)


# Main app
def main():
    st.set_page_config(
        page_title="Log Monitoring & Performance Analysis",
        page_icon="📊",
        layout="wide"
    )

    st.title("📊 Distributed Log Anomaly Detection System")

    # Sidebar
    st.sidebar.header("Navigation")
    view = st.sidebar.radio(
        "Select View",
        ["Real-Time Monitoring", "Experiment Analysis"]
    )

    if view == "Real-Time Monitoring":
        st.sidebar.header("Controls")
        global auto_refresh, log_limit
        auto_refresh = st.sidebar.checkbox("Auto Refresh (10s)", value=True)
        log_limit = st.sidebar.slider("Number of logs to display", 50, 500, 100)

        show_realtime_view()

        if auto_refresh:
            time.sleep(10)
            st.rerun()

    else:
        show_experiment_analysis()


if __name__ == "__main__":
    main()