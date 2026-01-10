"""
Parsers module for HTML, PDF, CSV, and message-based API specifications.

Implements: V1 Gap Closure Plan P4 & P5
"""
from integration_coworker.parsers.html_parser import (
    parse_html_spec,
    extract_html_endpoints,
    HtmlSpecDocument,
)
from integration_coworker.parsers.pdf_parser import (
    extract_pdf_text,
    parse_pdf_spec,
    PdfSpecDocument,
)
from integration_coworker.parsers.text_spec_heuristics import (
    detect_endpoints,
    infer_request_schema,
    infer_response_schema,
    ParsedEndpoint,
    ParsedSchema,
    ParsedEvent,
)
from integration_coworker.parsers.csv_schema import (
    infer_csv_schema,
    csv_schema_to_json_schema,
    CsvSchema,
    InferredField,
)
from integration_coworker.parsers.message_schema import (
    parse_message_schema,
    parse_json_schema,
    parse_avro_schema,
    parse_yaml_topic_descriptor,
    MessageSchema,
    MessageSchemaField,
    TopicDescriptor,
)

__all__ = [
    # HTML parsing
    "parse_html_spec",
    "extract_html_endpoints",
    "HtmlSpecDocument",
    # PDF parsing
    "extract_pdf_text",
    "parse_pdf_spec",
    "PdfSpecDocument",
    # Text heuristics
    "detect_endpoints",
    "infer_request_schema",
    "infer_response_schema",
    "ParsedEndpoint",
    "ParsedSchema",
    "ParsedEvent",
    # CSV schema
    "infer_csv_schema",
    "csv_schema_to_json_schema",
    "CsvSchema",
    "InferredField",
    # Message schemas
    "parse_message_schema",
    "parse_json_schema",
    "parse_avro_schema",
    "parse_yaml_topic_descriptor",
    "MessageSchema",
    "MessageSchemaField",
    "TopicDescriptor",
]
