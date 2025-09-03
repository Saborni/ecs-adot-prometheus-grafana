import logging
import requests
import time

from flask import Flask, request
from opentelemetry import trace, metrics
from opentelemetry import _logs
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Resource configuration
resource = Resource.create({
    "service.name": "zap-flask-app-local",
    "service.version": "1.0.0"
})

# Configure logging and OTLP log exporter
logger_provider = LoggerProvider(resource=resource)
_logs.set_logger_provider(logger_provider)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger = logging.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Configure tracing and OTLP trace exporter
trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)
span_processor = BatchSpanProcessor(OTLPSpanExporter())
trace.get_tracer_provider().add_span_processor(span_processor)

# Configure metrics and OTLP metric exporter
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(),export_interval_millis=5000)
metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
meter = metrics.get_meter(__name__)

# Create custom metrics
request_counter = meter.create_counter("zapp_http_requests_total",description="Total HTTP requests")
exception_counter = meter.create_counter("zapp_http_exceptions_total",description="Total Exceptions requests")
request_duration = meter.create_histogram("zapp_http_request_duration_seconds",description="HTTP request duration")

app = Flask(__name__)

# Instrument Flask and requests
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

@app.before_request
def before_request():
    app.start_time = time.time()

@app.after_request
def after_request(response):
    endpoint = request.endpoint or "unknown"
    request_counter.add(1, {"endpoint": endpoint, "method": request.method})
    request_duration.record(time.time() - app.start_time, {"endpoint": endpoint})
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    endpoint = request.endpoint or "unknown"
    exception_counter.add(1, {"endpoint": endpoint, "method": request.method})
    return {"error": str(e)}, 500

@app.route('/health')
def health():
    logging.info("Health check endpoint called")
    return {"status": "healthy"}

@app.route('/api/data')
def get_data():
    with tracer.start_as_current_span("get_data") as span:
        span.set_attribute("endpoint", "/api/data")
        logging.info("Data endpoint called")
        return {"data": "sample data", "timestamp": time.time()}

@app.route('/external')
def call_external():
    with tracer.start_as_current_span("external_call") as span:
        span.set_attribute("endpoint", "/external")
        logging.info("Making external HTTP call")
        try:
            response = requests.get("http://0.0.0.0:5000/health", timeout=5)
            span.set_attribute("http.status_code", response.status_code)
            result = {"external_status": response.status_code}
        except Exception as e:
            logging.error(f"External call failed: {e}")
            span.record_exception(e)
            result = {"error": str(e)}
        return result

@app.route('/error')
def trigger_error():
    logging.error("Intentional error triggered")
    raise Exception("This is a custom error")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)